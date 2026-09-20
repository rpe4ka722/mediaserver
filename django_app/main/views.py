from django.shortcuts import render, redirect, get_object_or_404
import requests
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.views.decorators.http import require_POST
from .models import Camera, CameraRecord
from .services import mediamtx_add_path, mediamtx_delete_path, mediamtx_edit_path, mediamtx_request
from .onvif_service import update_camera_onvif_cache, refresh_camera_onvif_cache_async
from .scripts import staff_required, mediamtx_webhook_required
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from datetime import datetime
from django.core.cache import cache
from django.contrib import messages
from django.conf import settings
from urllib.parse import quote
from django.http import JsonResponse, HttpResponse, Http404, FileResponse
from django.db import transaction
from pathlib import Path
import copy
import os
import re
import platform
import socket
import subprocess
import logging
import time
import json
from django.utils import timezone


logger = logging.getLogger(__name__)


@login_required(login_url='account:login')
def index(request):
        cameras = Camera.objects.all()
        context = {'cameras': cameras}
        return render(request, 'main/templates/index.html', context)


def stream_proxy_auth(request):
    """Внутренняя точка Nginx auth_request для доступа к WebRTC."""
    if request.user.is_authenticated and request.user.is_active:
        return HttpResponse(status=204)
    return HttpResponse(status=401)


@staff_required
def camera(request):
        cameras = Camera.objects.all()
        context = {'cameras': cameras}
        return render(request, 'main/templates/camera.html', context)


@staff_required
@require_POST
def create_camera(request):
    if request.method == 'POST':
        # 1. Сбор данных
        name = request.POST.get('camera_name')
        description = request.POST.get('camera_description')
        address = request.POST.get('camera_address')
        port = request.POST.get('camera_port') or 554
        login = request.POST.get('camera_login')
        password = request.POST.get('camera_password')
        path = request.POST.get('camera_path')
        onvif_port = request.POST.get('onvif_port') or 80


        camera = Camera(
            name=name,
            description=description,
            camera_address=address,
            camera_port=port,
            camera_login=login,
            camera_password=password,
            camera_path=path,
            onvif_port=onvif_port,
        )

        path_created = False
        try:
            camera.full_clean()
            success, error_msg = mediamtx_add_path(camera)
            if not success:
                raise RuntimeError(f"MediaMTX Error: {error_msg}")
            path_created = True

            try:
                with transaction.atomic():
                    camera.save()
            except Exception:
                # Не оставляем путь-сироту, если сохранение в БД не состоялось.
                cleanup_ok, cleanup_error = mediamtx_delete_path(camera.name)
                if not cleanup_ok:
                    logger.error("Не удалось удалить путь-сироту MediaMTX %s: %s", camera.name, cleanup_error)
                path_created = False
                raise

            messages.success(request, f'Камера "{name}" успешно добавлена.')

        except Exception as e:
            if path_created and not camera.pk:
                cleanup_ok, cleanup_error = mediamtx_delete_path(camera.name)
                if not cleanup_ok:
                    logger.error("Не удалось удалить путь-сироту MediaMTX %s: %s", camera.name, cleanup_error)
            messages.error(request, f'Камера не добавлена: {e}')
        
        return redirect('main:camera')
    
    return redirect('main:camera')


@staff_required
@require_POST
def delete_camera(request, camera_id):
    if request.method == 'POST':
        camera = get_object_or_404(Camera, pk=camera_id)
        name = camera.name
        
        try:
            success, error_msg = mediamtx_delete_path(name)
            if not success:
                raise RuntimeError(f"MediaMTX не позволил удалить поток: {error_msg}")

            try:
                with transaction.atomic():
                    camera.delete()
            except Exception:
                # Компенсируем удаление внешней конфигурации при ошибке БД.
                restored, restore_error = mediamtx_add_path(camera)
                if not restored:
                    logger.critical("Не удалось восстановить путь MediaMTX %s: %s", name, restore_error)
                raise

            messages.success(request, f'Камера "{name}" удалена.')
        except Exception as e:
            messages.error(request, str(e))
            
    return redirect('main:camera')


@staff_required
@require_POST
def edit_camera(request, camera_id):
    if request.method == 'POST':
        camera = get_object_or_404(Camera, pk=camera_id)
        
        # Снимок нужен для компенсации, если БД не сохранится после изменения
        # внешней конфигурации MediaMTX.
        old_camera = copy.copy(camera)
        old_name = camera.name

        # Обновляем объект данными из формы (пока без save)
        camera.name = request.POST.get('camera_name')
        camera.description = request.POST.get('camera_description')
        camera.camera_address = request.POST.get('camera_address')
        camera.camera_port = request.POST.get('camera_port') or 554
        camera.camera_login = request.POST.get('camera_login')
        camera.camera_password = request.POST.get('camera_password')
        camera.camera_path = request.POST.get('camera_path')
        camera.onvif_port = request.POST.get('onvif_port') or 80
 

        new_name = camera.name

        try:
            camera.full_clean()

            if old_name != new_name:
                # При переименовании сначала создаём новый рабочий путь. Старый
                # удаляем только после успешного commit в Django.
                success, error_msg = mediamtx_add_path(camera)
            else:
                success, error_msg = mediamtx_edit_path(camera)

            if not success:
                raise RuntimeError(f"MediaMTX Error: {error_msg}")

            try:
                with transaction.atomic():
                    camera.save()
            except Exception:
                if old_name != new_name:
                    restored, restore_error = mediamtx_delete_path(new_name)
                else:
                    restored, restore_error = mediamtx_edit_path(old_camera)
                if not restored:
                    logger.critical(
                        "Не удалось компенсировать изменение MediaMTX для камеры %s: %s",
                        old_name,
                        restore_error,
                    )
                raise

            if old_name != new_name:
                deleted, delete_error = mediamtx_delete_path(old_name)
                if not deleted:
                    logger.error("Не удалось удалить старый путь MediaMTX %s: %s", old_name, delete_error)
                    messages.warning(request, f'Камера сохранена, но старый поток "{old_name}" требует очистки.')
                else:
                    messages.success(request, 'Успешно')
            else:
                messages.success(request, 'Успешно')

        except Exception as e:
            # Если API упало, БД осталась нетронутой (старой и верной)
            messages.error(request, f'Ошибка интеграции: {e}')

    return redirect('main:camera')


@login_required(login_url='account:login')
def get_camera_stream_url(request, camera_id):

        camera = get_object_or_404(Camera, id=camera_id)

        # ONVIF может отвечать несколько секунд, поэтому не задерживаем выдачу
        # ссылки на видеопоток.
        refresh_camera_onvif_cache_async(camera.id)
        
        base_url = settings.MEDIAMTX_PUBLIC_STREAM_BASE.rstrip('/')
        if not base_url:
            base_url = request.build_absolute_uri('/streams').rstrip('/')

        safe_camera_name = quote(camera.name)
        stream_url = f"{base_url}/{safe_camera_name}"
        
        return JsonResponse({
                'status': 'success',
                'name': camera.name,
                'url': stream_url
    })


def check_mediamtx_health():
    """Проверка доступности самого сервера MediaMTX с отладочным выводом"""
    status = {"api": False}
    
    try:
        # 1. Проверка API
        r = mediamtx_request('GET', '/v3/info', timeout=2)
        status["api"] = (r.status_code == 200)

    except (requests.exceptions.RequestException, RuntimeError) as e:
        print(f"Network/API Exception: {e}")
    except Exception as e:
        print(f"General Exception: {e}")
    
    return status

@login_required(login_url='account:login')
def mediamtx_ping(request):
    """Эндпоинт для проверки связи с сервером из фронтенда"""
    status_data = check_mediamtx_health() # Наша функция с отладкой
    
    # Определяем общий статус для простоты фронта
    is_online = status_data.get("api")
    
    return JsonResponse({
        "status": "online" if is_online else "offline",
        "details": status_data
    })



@login_required(login_url='account:login')
def ensure_camera_in_mediamtx(request, camera_id):
    """
    Проверяет доступность камеры и наличие пути в MediaMTX.
    """
    camera = get_object_or_404(Camera, id=camera_id)
    camera_ip = camera.camera_address
    camera_port = camera.camera_port
    path_name = camera.name
    
    result = {
        "status": "error",
        "message": "Camera unreachable", # Сообщение по умолчанию
        "camera_name": path_name,
        "details": {
            "path": False,
            "tcp": False
        }
    }

    # 1. Сетевые проверки (зажигают первые две лампочки)

    result["details"]["tcp"] = check_ip(camera_ip, camera_port)

    # 2. Работа с MediaMTX (только если камера ответила по TCP)
    if result["details"]["tcp"]:
        try:
            # Проверка наличия пути
            check_res = mediamtx_request('GET', f"/v3/config/paths/get/{path_name}", timeout=5)
            
            if check_res.status_code == 200:
                result["details"]["path"] = True
                result["status"] = "success"
                result["message"] = "Path is ready"
            
            elif check_res.status_code == 404:
                if not request.user.is_staff:
                    result["message"] = "Path is not configured; contact an administrator"
                    return JsonResponse(result)

                # Попытка создания пути
                success, error_msg = mediamtx_add_path(camera)
                if success:
                    result["details"]["path"] = True
                    result["status"] = "success"
                    result["message"] = "Path created successfully"
                else:
                    result["message"] = f"Path creation failed: {error_msg}"
            else:
                result["message"] = f"MediaMTX unexpected status: {check_res.status_code}"
                
        except (requests.exceptions.RequestException, RuntimeError) as e:
            result["message"] = f"MediaMTX connection error: {str(e)}"
    else:
        result["message"] = f"Camera {camera_ip}:{camera_port} is offline (TCP check failed)"

    # Возвращаем 200 всегда, чтобы фронтенд мог отрисовать лампочки, 
    # а статус готовности проверял через result.status
    return JsonResponse(result)


def check_path_or_create(camera_id):
    """
    Проверяет наличие пути в MediaMTX и создает его, если он отсутствует.
    Возвращает (success: bool, message: str)
    """
    try:
        
        try:
            camera = Camera.objects.get(id=camera_id)
        except Camera.DoesNotExist:
            return False, f"Камера с ID {camera_id} не найдена в базе."

        camera_name = camera.name
        # 1. Проверка наличия
        check_res = mediamtx_request('GET', f"/v3/config/paths/get/{camera_name}", timeout=5)

        if check_res.status_code == 200:
            return True, "Путь уже существует."

        # 2. Создание, если не найдено (404)
        if check_res.status_code == 404:
            # Ваша функция добавления (убедитесь, что она тоже не требует request)
            success, error_msg = mediamtx_add_path(camera)
            if success:
                return True, "Путь успешно создан."
            else:
                return False, f"Ошибка MediaMTX при создании: {error_msg}"
        
        return False, f"MediaMTX вернул неожиданный статус: {check_res.status_code}"

    except (requests.exceptions.RequestException, RuntimeError) as e:
        logger.error(f"MediaMTX Connection Error: {e}")
        return False, f"Ошибка соединения с MediaMTX: {str(e)}"
    except Exception as e:
        logger.exception("Unexpected error in check_path_or_create")
        return False, f"Критическая ошибка: {str(e)}"



# @login_required(login_url='account:login')
# def get_camera_bitrate(request, camera_id):
#     """Возвращает битрейт потока камеры (попытками через API MediaMTX).

#     Алгоритм:
#     - Запрашивает несколько потенциальных эндпоинтов MediaMTX (/v3/streams/get/<name>, /v3/streams и т.д.).
#     - Парсит JSON-ответ рекурсивно и ищет числовые поля с именами, содержащими 'bit', 'bps' или 'rate'.
#     - Если найдено — возвращает значение в kbps (приближённо) и исходную пару (ключ+значение).
#     - Если не найдено — возвращает отладочную информацию для дальнейшего анализа.
#     """
#     camera = get_object_or_404(Camera, id=camera_id)
#     api_base = settings.MEDIAMTX_API_URL.rstrip('/')
#     endpoints = [
#         f"{api_base}/v3/streams/get/{camera.name}",
#         f"{api_base}/v3/streams",
#         f"{api_base}/v3/streams/list",
#     ]


# def ping_camera(ip):
#     # Определяем параметр в зависимости от ОС
#     param = '-n' if platform.system().lower() == 'windows' else '-c'
#     # Используем -W (timeout) для Linux, чтобы не ждать долго, если хост мертв
#     timeout_param = ['-w', '1000'] if platform.system().lower() == 'windows' else ['-W', '1']
    
#     command = ['ping', param, '1'] + timeout_param + [ip]
    
#     try:
#         # subprocess.run безопаснее и позволяет подавить вывод через devnull
#         result = subprocess.run(
#             command, 
#             stdout=subprocess.DEVNULL, 
#             stderr=subprocess.DEVNULL
#         )
#         return result.returncode == 0
#     except Exception:
#         return False


def check_ip(ip, port=554, timeout=1):
    try:
        # Пытаемся просто открыть TCP-соединение с портом камеры
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False



# def check_camera_network(request, camera_id):
#     # Используем булевы значения вместо строк "true"/"false" для удобства фронтенда
#     result = {
#         "status": "error",
#         "icmp_ping": False,
#         "tcp_connection": False,
#         "message": ""
#     }

#     try:
#         camera = get_object_or_404(Camera, id=camera_id)
#         ip = camera.camera_address
#         port = camera.camera_port

#         # 2. Проводим проверки
#         result["icmp_ping"] = ping_camera(ip)
#         result["tcp_connection"] = check_ip(ip, port)

#         # 3. Логика статуса: успех только если сервис (TCP) доступен
#         if result["tcp_connection"]:
#             result["status"] = "success"
#             result["message"] = "Камера доступна"
#         elif result["icmp_ping"]:
#             result["status"] = "warning"
#             result["message"] = f"Устройство {camera.name} в сети, но порт {camera.port} закрыт"
#         else:
#             result["message"] = "Устройство недоступно"

#     except Exception as e:
#         logger.error(f"Error checking camera {camera.name}: {e}")
#         result["message"] = f"Критическая ошибка: {str(e)}"
#         return JsonResponse(result, status=500)

#     return JsonResponse(result, status=200)


@staff_required
def config_main(request):
        cameras = Camera.objects.all()
        context = {'cameras': cameras}
        return render(request, 'main/templates/config.html', context)


@staff_required
def get_camera_bitrate(request, camera_id):
    """Возвращает битрейт, рассчитанный по дельте счётчика MediaMTX."""
    camera = get_object_or_404(Camera, id=camera_id)
    # Динамические пути создаются под уникальным именем Camera.name.
    path_name = camera.name

    try:
        r = mediamtx_request('GET', f"/v3/paths/get/{path_name}", timeout=2)
        
        if r.status_code == 404:
            return JsonResponse({
                'status': 'offline',
                'message': f'Поток "{path_name}" сейчас не активен в MediaMTX (камера отключена).',
                'bitrate_kbps': 0,
                'bitrate_mbps': 0
            })
            
        if r.status_code != 200:
            return JsonResponse({'status': 'error', 'message': f'MediaMTX вернул статус {r.status_code}'}, status=500)

        data = r.json()
        
        current_bytes = _extract_bytes_received(data)
        bitrate_mbps = _calculate_bitrate_mbps(
            camera.id,
            current_bytes,
            cache_namespace='detail',
        )

        return JsonResponse({
            'status': 'success',
            'path': path_name,
            'bytes_received': current_bytes,
            'bitrate_kbps': round((bitrate_mbps or 0) * 1000, 2),
            'bitrate_mbps': bitrate_mbps or 0.0,
            'warming_up': bitrate_mbps is None,
        })

    except (requests.exceptions.RequestException, RuntimeError) as e:
        logger.error(f"Ошибка запроса к MediaMTX API: {e}")
        return JsonResponse({'status': 'error', 'message': f'MediaMTX API недоступен: {str(e)}'}, status=502)
    except Exception as e:
        logger.exception("Неожиданная ошибка при получении битрейта")
        return JsonResponse({'status': 'error', 'message': f'Внутренняя ошибка: {str(e)}'}, status=500)


def _extract_bytes_received(path_data):
    """Извлекает накопительный счётчик принятых байтов из ответа MediaMTX."""
    source_state = path_data.get('sourceState') or {}
    value = source_state.get('bytesReceived', path_data.get('bytesReceived', 0))
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _calculate_bitrate_mbps(camera_id, current_bytes, cache_namespace='list'):
    """Считает скорость по двум замерам накопительного счётчика."""
    now = time.monotonic()
    cache_key = f"cam_metrics_{cache_namespace}_{camera_id}"
    previous = cache.get(cache_key)
    cache.set(cache_key, {'bytes': current_bytes, 'time': now}, timeout=30)

    if not previous:
        return None

    bytes_delta = current_bytes - previous.get('bytes', current_bytes)
    time_delta = now - previous.get('time', now)
    if time_delta <= 0 or bytes_delta < 0:
        return None

    return round((bytes_delta * 8) / 1_000_000 / time_delta, 2)



@staff_required
def get_all_cameras_status(request):
    try:
        # 1. Запрашиваем живые потоки у MediaMTX
        try:
            response = mediamtx_request('GET', '/v3/paths/list', timeout=3)
            status_code = response.status_code
        except (requests.exceptions.RequestException, RuntimeError) as e:
            logger.error(f"MediaMTX не отвечает в API статусов: {e}")
            status_code = 500

        mtx_data = {}
        if status_code == 200:
            # Превращаем список от MediaMTX в словарь с ключом — именем пути ("cam1")
            mtx_items = response.json().get('items', []) or []
            mtx_data = {item['name']: item for item in mtx_items if 'name' in item}

        # 2. Берем все камеры из нашей БД Django
        cameras = Camera.objects.all()
        output_data = {}
        for camera in cameras:

            # 2. Получаем ONVIF информацию (из кэша, чтобы не тормозить)
            onvif_info = cache.get(f"cam_onvif_info_{camera.id}")
            if onvif_info is None:
                refresh_camera_onvif_cache_async(camera.id)

            slug_name = camera.name 
            mtx_cam = mtx_data.get(slug_name)

            if mtx_cam and mtx_cam.get('ready', False):
                # Камера активна. Считаем битрейт
                current_bytes = _extract_bytes_received(mtx_cam)
                measured_bitrate = _calculate_bitrate_mbps(camera.id, current_bytes)
                bitrate_mbps = measured_bitrate or 0.0
                
                

                output_data[str(camera.id)] = {
                    "status": "online",
                    "bitrate_mbps": bitrate_mbps,
                    "onvif_info": onvif_info or "Нет данных ONVIF"
                }
            else:
                # Камера оффлайн
                output_data[str(camera.id)] = {
                    "status": "offline",
                    "bitrate_mbps": 0.00,
                    "onvif_info": onvif_info or "Нет данных ONVIF"
                }

            

        # Гарантированно возвращаем чистый Django JsonResponse
        return JsonResponse({'status': 'success', 'data': output_data})

    except Exception as e:
        logger.exception("Критическая ошибка в эндпоинте get_all_cameras_status")
        # Вместо падения в HTML (ошибка 500) отдаем JSON брейкдаун
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)



from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .models import CameraRecord

@login_required(login_url='account:login')
def all_cameras_archive_view(request):
    """
    Выводит единый список файлов видеоархива для ВСЕХ камер системы.
    Записи отсортированы по дате (сначала новые) и выводятся одним списком без пагинации.
    """
    # Запрашиваем все записи, оптимизируем запрос и сортируем от свежих к старым
    records = CameraRecord.objects.select_related('camera').order_by('-start_time')
        
    # Рендерим общий шаблон архива, передавая обычный QuerySet вместо page_obj
    return render(request, 'main/templates/files.html', {
        'records': records
    })


@staff_required
@require_POST
def toggle_record_view(request, camera_id):
    """
    Включает или выключает запись потока камеры на лету 
    через обращение к REST API MediaMTX.
    """
    camera = get_object_or_404(Camera, pk=camera_id)
    
    # URL для изменения конфигурации пути конкретной камеры в MediaMTX API v3
    # Меняем текущее состояние на противоположное
    target_state = not camera.is_recording
    
    # Формируем JSON-тело запроса для MediaMTX согласно документации
    # Параметр "record" принимает значения true или false
    payload = {
        "record": target_state,
    }
    
    try:
        # Отправляем PATCH запрос в MediaMTX для мгновенного изменения настроек пути
        response = mediamtx_request(
            'PATCH',
            f"/v3/config/paths/patch/{camera.name}",
            json=payload,
            timeout=5,
        )
        
        if response.status_code in [200, 201, 204]:
            # Если MediaMTX успешно применил настройки, сохраняем статус в БД Django
            camera.is_recording = target_state
            camera.save()
            
            if target_state:
                messages.success(request, f"Запись для камеры '{camera.name}' успешно запущена.")
            else:
                messages.warning(request, f"Запись для камеры '{camera.name}' остановлена.")
        else:
            logger.error(f"MediaMTX API вернул ошибку {response.status_code}: {response.text}")
            messages.error(request, "Не удалось изменить статус записи на медиасервере.")
            
    except (requests.exceptions.RequestException, RuntimeError) as e:
        logger.error(f"Ошибка подключения к MediaMTX API: {e}")
        messages.error(request, "Медиасервер недоступен. Проверьте работу контейнера MediaMTX.")

    # Возвращаем пользователя на ту страницу, откуда был нажат клик
    return redirect(request.META.get('HTTP_REFERER', '/'))



@csrf_exempt
@require_POST
@mediamtx_webhook_required
def mediamtx_record_webhook(request):
    camera_name = request.GET.get('path')
    mediamtx_file_path = request.GET.get('file')

    if not camera_name or not mediamtx_file_path:
        return HttpResponse("Missing data", status=400)

    try:
        # Находим камеру по имени
        camera = Camera.objects.get(name=camera_name)

        # Извлекаем только имя файла (например, "2026-05-24_09-28-40-650794.mp4")
        file_name = os.path.basename(mediamtx_file_path)

        # Строим абсолютный путь для контейнера Django.
        django_file_path = os.path.join(settings.RECORDINGS_ROOT, camera_name, file_name)

        # Отсекаем расширение .mp4 -> "2026-05-24_09-28-40-650794"
        base_name = os.path.splitext(file_name)[0]

        try:
            clean_date_str = base_name[:19]
            naive_datetime = datetime.strptime(clean_date_str, "%Y-%m-%d_%H-%M-%S")
            start_time = timezone.make_aware(naive_datetime)
        except ValueError:
            start_time = timezone.now()

        file_size = 0
        if os.path.exists(django_file_path):
            file_size = os.path.getsize(django_file_path)
        else:
            logger.warning("Файл webhook не найден: %s", django_file_path)

        CameraRecord.objects.update_or_create(
            file_path=django_file_path,
            defaults={
                'camera': camera,
                'file_name': file_name,
                'file_size_bytes': file_size,
                'start_time': start_time,
                'duration_seconds': 3600,
            },
        )
        return HttpResponse("Record saved successfully", status=201)

    except Camera.DoesNotExist:
        return HttpResponse("Camera not found", status=404)
    except Exception:
        logger.exception("Ошибка обработки webhook записи MediaMTX")
        return HttpResponse("Internal server error", status=500)

@login_required(login_url='account:login')
def download_record_view(request, record_id):
    """Находит видеозапись по ID и отдает её пользователю для скачивания."""
    
    # 1. Получаем объект записи из БД или отдаем 404, если такого ID нет
    record = get_object_or_404(CameraRecord, id=record_id)
    
    # 2. Берем путь к файлу, сохраненный в базе данных
    file_path = Path(record.file_path).resolve()
    recordings_root = Path(settings.RECORDINGS_ROOT).resolve()

    try:
        relative_path = file_path.relative_to(recordings_root)
    except ValueError:
        logger.error("Запись %s указывает за пределы RECORDINGS_ROOT: %s", record.id, file_path)
        raise Http404("Некорректный путь к видеозаписи.")
    
    # 3. Проверяем, существует ли файл физически на диске контейнера Django
    if not file_path.is_file():
        raise Http404("Файл видеозаписи физически не найден на сервере.")

    if not settings.USE_X_ACCEL_REDIRECT:
        return FileResponse(
            file_path.open('rb'),
            as_attachment=True,
            filename=record.file_name,
            content_type='video/mp4',
        )

    # Файл отдаёт Nginx через internal location, не занимая Gunicorn worker.
    internal_path = quote(relative_path.as_posix(), safe='/')
    response = HttpResponse(content_type='video/mp4')
    response['X-Accel-Redirect'] = f'/protected_recordings/{internal_path}'
    encoded_name = quote(record.file_name, safe='')
    response['Content-Disposition'] = f"attachment; filename*=UTF-8''{encoded_name}"
    
    return response

@staff_required
@require_POST
def delete_record_view(request, record_id):
    """Удаляет файл видеозаписи с диска и стирает запись из базы данных."""
    
    # 1. Получаем объект записи из БД
    record = get_object_or_404(CameraRecord, id=record_id)
    file_path = Path(record.file_path).resolve()
    recordings_root = Path(settings.RECORDINGS_ROOT).resolve()

    try:
        file_path.relative_to(recordings_root)
    except ValueError:
        logger.error("Отказ удаления записи %s за пределами RECORDINGS_ROOT: %s", record.id, file_path)
        raise Http404("Некорректный путь к видеозаписи.")
    
    try:
        # 2. Удаляем файл физически, если он существует на диске
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            print(f"[INFO] Файл успешно удален с диска: {file_path}")
        else:
            print(f"[WARNING] Файл не найден на диске, удаляем только из БД: {file_path}")
            
        # 3. Удаляем саму запись из базы данных
        record.delete()
        
        # Добавляем всплывающее уведомление для пользователя (Django Messages Framework)
        messages.success(request, "Видеозапись успешно удалена.")
        
    except Exception as e:
        messages.error(request, f"Ошибка при удалении файла: {str(e)}")
        print(f"[ERROR] Не удалось удалить запись {record_id}: {str(e)}")
        
    # 4. Перенаправляем пользователя обратно на страницу архива
    # Замените 'archive_list' на имя вашего view со списком записей
    return redirect(request.META.get('HTTP_REFERER', 'main:camera_records'))


@csrf_exempt
@require_POST
@mediamtx_webhook_required
def mediamtx_record_stop_webhook(request):
    print(f"[DEBUG] Получен запрос на остановку записи от MediaMTX: {request.method} {request.GET}")
    camera_name = request.GET.get('path')
    if not camera_name:
        return HttpResponse("Missing data", status=400)
            
    try:
        # 1. Получаем объект камеры из БД
        camera = Camera.objects.get(name=camera_name)
    except Camera.DoesNotExist:
        return HttpResponse("Camera not found", status=404)
        
    # 2. Формируем запрос к MediaMTX API
    payload = {"record": False}
    
    try:
        response = mediamtx_request(
            'PATCH',
            f"/v3/config/paths/patch/{camera_name}",
            json=payload,
            timeout=5,
        )
        
        if response.status_code in [200, 201, 204]:
            # 3. Обновляем статус только после успешного ответа сервера
            camera.is_recording = False
            camera.save()
            return HttpResponse("Record stopped and DB updated", status=200)
        else:
            logger.error(f"MediaMTX API error {response.status_code}: {response.text}")
            return HttpResponse("MediaMTX API error", status=502)
            
    except (requests.exceptions.RequestException, RuntimeError) as e:
        logger.error(f"Connection error: {e}")
        return HttpResponse("MediaMTX unreachable", status=503)


@staff_required
@csrf_protect
def set_camera_resolution(request, camera_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)
    
    try:
        data = json.loads(request.body)
        res = data.get('resolution') # Ожидаем формат "1920x1080"
        if not res or 'x' not in res:
            return JsonResponse({'status': 'error', 'message': 'Неверный формат'}, status=400)
            
        width, height = (int(value) for value in res.split('x', 1))
        if width <= 0 or height <= 0:
            return JsonResponse({'status': 'error', 'message': 'Разрешение должно быть положительным'}, status=400)
        camera = get_object_or_404(Camera, id=camera_id)
        
        result = camera.set_resolution(width, height)
        if isinstance(result, dict) and result.get('error'):
            return JsonResponse({'status': 'error', 'message': result['error']}, status=502)
        
        onvif_info = update_camera_onvif_cache(camera)
        
        return JsonResponse({'status': 'success', 'onvif_info': onvif_info})
    except (TypeError, ValueError):
        return JsonResponse({'status': 'error', 'message': 'Неверный формат разрешения'}, status=400)
    except Exception as e:
        logger.exception("Ошибка изменения разрешения камеры %s", camera_id)
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@staff_required
@csrf_protect
def set_camera_fps(request, camera_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)
    
    try:
        data = json.loads(request.body)
        camera = get_object_or_404(Camera, id=camera_id)

        if 'fps' not in data:
            return JsonResponse({'status': 'error', 'message': 'FPS не указан'}, status=400)

        fps = int(data['fps'])
        if fps <= 0:
            return JsonResponse({'status': 'error', 'message': 'FPS должен быть положительным'}, status=400)

        result = camera.set_fps(fps)
        if isinstance(result, dict) and result.get('error'):
            return JsonResponse({'status': 'error', 'message': result['error']}, status=502)
            
        onvif_info = update_camera_onvif_cache(camera)
        
        return JsonResponse({'status': 'success', 'onvif_info': onvif_info})
    except (TypeError, ValueError):
        return JsonResponse({'status': 'error', 'message': 'Неверный формат FPS'}, status=400)
    except Exception as e:
        logger.exception("Ошибка изменения FPS камеры %s", camera_id)
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
