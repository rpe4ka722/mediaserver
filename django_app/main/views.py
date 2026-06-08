from django.shortcuts import render, redirect, get_object_or_404
import requests
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.views.decorators.http import require_POST
from .models import Camera, CameraRecord
from .services import mediamtx_add_path, mediamtx_delete_path, mediamtx_edit_path
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from datetime import datetime
from django.core.cache import cache
from django.contrib import messages
from django.conf import settings
from urllib.parse import quote
from django.http import JsonResponse, HttpResponse, FileResponse
from django.db import transaction
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


@login_required(login_url='account:login')
def camera(request):
        cameras = Camera.objects.all()
        context = {'cameras': cameras}
        return render(request, 'main/templates/camera.html', context)


@login_required(login_url='account:login')
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


        try:
            # Используем блок транзакции
            with transaction.atomic():
                # Создаем объект в памяти (без сохранения в БД пока что, если нужно)
                # Или создаем в БД, но в рамках транзакции
                camera = Camera.objects.create(
                    name=name,
                    description=description,
                    camera_address=address,
                    camera_port=port,
                    camera_login=login,
                    camera_password=password,
                    camera_path=path,
                    onvif_port=onvif_port 
                )

                # 2. Попытка добавить в MediaMTX
                success, error_msg = mediamtx_add_path(camera)
                
                if success:
                    messages.success(request, f'Камера "{name}" успешно добавлена.')
                else:
                    # ВАЖНО: Выбрасываем исключение, чтобы откатить транзакцию в БД
                    raise Exception(f"MediaMTX Error: {error_msg}")

        except Exception as e:
            # Сюда попадем и при ошибке БД, и при нашей ошибке MediaMTX
            messages.error(request, f'Камера не добавлена: {e}')
            # Если возникло исключение внутри atomic(), запись в БД не будет создана
        
        return redirect('main:camera')
    
    return redirect('main:camera')


@login_required(login_url='account:login')
def delete_camera(request, camera_id):
    if request.method == 'POST':
        camera = get_object_or_404(Camera, pk=camera_id)
        name = camera.name
        
        try:
            with transaction.atomic():
                # 1. Сначала удаляем из MediaMTX
                success, error_msg = mediamtx_delete_path(name)
                
                if success:
                    # 2. Если сервис удалил, удаляем из БД
                    camera.delete()
                    messages.success(request, f'Камера "{name}" удалена.')
                else:
                    raise Exception(f"MediaMTX не позволил удалить поток: {error_msg}")
        except Exception as e:
            messages.error(request, str(e))
            
    return redirect('main:camera')


@login_required(login_url='account:login')
def edit_camera(request, camera_id):
    if request.method == 'POST':
        camera = get_object_or_404(Camera, pk=camera_id)
        
        # Сохраняем старые значения для сравнения и удаления
        old_name = camera.name
        old_path = camera.camera_path  # Именно по этому полю ищем в MediaMTX

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
        new_path = camera.camera_path

        try:
            # 1. Сначала меняем настройки в MediaMTX
            if old_path != new_path:
                mediamtx_delete_path(old_name)
                mediamtx_add_path(camera) # обновленный объект
            else:
                mediamtx_edit_path(camera)
            
            # 2. Если API ответило ОК, фиксируем в БД
            with transaction.atomic():
                camera.save()
                messages.success(request, 'Успешно')

        except Exception as e:
            # Если API упало, БД осталась нетронутой (старой и верной)
            messages.error(request, f'Ошибка интеграции: {e}')

    return redirect('main:camera')


@login_required(login_url='account:login')
def get_camera_stream_url(request, camera_id):

        camera = get_object_or_404(Camera, id=camera_id)

        try:
            update_camera_onvif_cache(camera)
        except Exception as e:
            # Логируем, но продолжаем работу, чтобы не ломать видеопоток
            logger.error(f"Ошибка обновления кэша ONVIF для {camera.name}: {e}")
        
        # Берем IP из настроек
        base_ip = settings.MEDIAMTX_EXTERNAL_IP  # Например, '1.2.3.4' или 'http://1.2.3.4'
        
        # Проверяем, начинается ли IP с протокола, если нет — добавляем http
        if not base_ip.startswith(('http://', 'https://')):
                base_ip = f'http://{base_ip}'
        
        # Экранируем имя камеры для URL (на случай спецсимволов)
        safe_camera_name = quote(camera.name)
        
        # Формируем итоговый URL (обычно MediaMTX использует порт 8889 для WebRTC или HLS)
        stream_url = f"{base_ip.rstrip('/')}:8889/{safe_camera_name}"
        
        return JsonResponse({
                'status': 'success',
                'name': camera.name,
                'url': stream_url
    })


def check_mediamtx_health():
    """Проверка доступности самого сервера MediaMTX с отладочным выводом"""
    api_url = f"{settings.MEDIAMTX_API_URL.rstrip('/')}/v3/info"
    status = {"api": False}
    
    try:
        # 1. Проверка API
        r = requests.get(api_url, timeout=2) # Увеличил таймаут для стабильности отладки
        status["api"] = (r.status_code == 200)

    except requests.exceptions.RequestException as e:
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
    api_url = settings.MEDIAMTX_API_URL.rstrip('/')
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
            check_res = requests.get(f"{api_url}/v3/config/paths/get/{path_name}", timeout=5)
            
            if check_res.status_code == 200:
                result["details"]["path"] = True
                result["status"] = "success"
                result["message"] = "Path is ready"
            
            elif check_res.status_code == 404:
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
                
        except requests.exceptions.RequestException as e:
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
        api_url = settings.MEDIAMTX_API_URL.rstrip('/')

        # 1. Проверка наличия
        check_res = requests.get(f"{api_url}/v3/config/paths/get/{camera_name}", timeout=5)

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

    except requests.exceptions.RequestException as e:
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


@login_required(login_url='account:login')
def config_main(request):
        cameras = Camera.objects.all()
        context = {'cameras': cameras}
        return render(request, 'main/templates/config.html', context)


@login_required(login_url='account:login')
def get_camera_bitrate(request, camera_id):
    """
    Возвращает РЕАЛЬНУЮ текущую сетевую нагрузку (битрейт) потока из MediaMTX.
    """
    camera = get_object_or_404(Camera, id=camera_id)
    api_base = settings.MEDIAMTX_API_URL.rstrip('/')
    
    # В MediaMTX поток идентифицируется по camera_path (имени пути)
    path_name = camera.camera_path
    url = f"{api_base}/v3/paths/get/{path_name}"

    try:
        r = requests.get(url, timeout=2)
        
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
        
        # --- Парсинг структуры MediaMTX v3 ---
        # Текущая скорость входящего потока от камеры в MediaMTX лежит в bytesReceived
        # Она находится внутри объекта "sourceState" или корневого объекта пути, если поток активен.
        bytes_received_per_sec = 0
        
        if 'sourceState' in data and data['sourceState']:
            # MediaMTX v3 часто пишет метрики внутрь состояния источника
            bytes_received_per_sec = data['sourceState'].get('bytesReceived', 0)
        else:
            # Альтернативное расположение в некоторых сборках MediaMTX
            bytes_received_per_sec = data.get('bytesReceived', 0)

        # Конвертируем Bytes/sec в биты и килобиты
        # 1 Byte = 8 bits
        bits_per_sec = bytes_received_per_sec * 8
        kbps = bits_per_sec / 1000.0
        mbps = kbps / 1000.0

        return JsonResponse({
            'status': 'success',
            'path': path_name,
            'bytes_per_sec': bytes_received_per_sec, # для отладки
            'bitrate_kbps': round(kbps, 2),          # например: 3450.21 Kbps
            'bitrate_mbps': round(mbps, 2),          # например: 3.45 Mbps
        })

    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка запроса к MediaMTX API: {e}")
        return JsonResponse({'status': 'error', 'message': f'MediaMTX API недоступен: {str(e)}'}, status=502)
    except Exception as e:
        logger.exception("Неожиданная ошибка при получении битрейта")
        return JsonResponse({'status': 'error', 'message': f'Внутренняя ошибка: {str(e)}'}, status=500)



def get_all_cameras_status(request):
    try:
        # 1. Запрашиваем живые потоки у MediaMTX
        api_url = f"{settings.MEDIAMTX_API_URL.rstrip('/')}/v3/paths/list"
        
        try:
            response = requests.get(api_url, timeout=3)
            status_code = response.status_code
        except requests.exceptions.RequestException as e:
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
        current_time = time.time()

        for camera in cameras:

            # 2. Получаем ONVIF информацию (из кэша, чтобы не тормозить)
            onvif_info = cache.get(f"cam_onvif_info_{camera.id}")

            slug_name = camera.name 
            mtx_cam = mtx_data.get(slug_name)

            if mtx_cam and mtx_cam.get('ready', False):
                # Камера активна. Считаем битрейт
                current_bytes = mtx_cam.get('bytesReceived', 0)
                
                # Ключ для хранения предыдущих замеров в кеше
                cache_key = f"cam_metrics_{camera.id}"
                previous_data = cache.get(cache_key)
                
                bitrate_mbps = 0.00
                
                if previous_data:
                    prev_bytes = previous_data.get('bytes', 0)
                    prev_time = previous_data.get('time', 0)
                    
                    # Вычисляем дельту байт и времени
                    bytes_delta = current_bytes - prev_bytes
                    time_delta = current_time - prev_time
                    
                    if time_delta > 0 and bytes_delta >= 0:
                        # Байты в биты -> в Мегабиты -> делим на секунды
                        bitrate_bps = bytes_delta * 8
                        bitrate_mbps = round(bitrate_bps / (1024 * 1024) / time_delta, 2)
                
                # Обновляем данные в кеше для следующего шага (на 10 секунд)
                cache.set(cache_key, {'bytes': current_bytes, 'time': current_time}, 10)
                
                # Если поток только пошел, покажем среднее значение, пока копится дельта
                if bitrate_mbps == 0.00 and current_bytes > 0:
                    bitrate_mbps = 0.50 
                
                

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


@login_required
@require_POST
def toggle_record_view(request, camera_id):
    """
    Включает или выключает запись потока камеры на лету 
    через обращение к REST API MediaMTX.
    """
    camera = get_object_or_404(Camera, pk=camera_id)
    
    # URL для изменения конфигурации пути конкретной камеры в MediaMTX API v3
    url = f"{settings.MEDIAMTX_API_URL.rstrip('/')}/v3/config/paths/patch/{camera.name}"
    
    # Меняем текущее состояние на противоположное
    target_state = not camera.is_recording
    
    # Формируем JSON-тело запроса для MediaMTX согласно документации
    # Параметр "record" принимает значения true или false
    payload = {
        "record": target_state,
    }
    
    try:
        # Отправляем PATCH запрос в MediaMTX для мгновенного изменения настроек пути
        response = requests.patch(url, json=payload, timeout=5)
        
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
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка подключения к MediaMTX API: {e}")
        messages.error(request, "Медиасервер недоступен. Проверьте работу контейнера MediaMTX.")

    # Возвращаем пользователя на ту страницу, откуда был нажат клик
    return redirect(request.META.get('HTTP_REFERER', '/'))



@csrf_exempt
def mediamtx_record_webhook(request):
    if request.method == 'POST':
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
            # Больше никаких .replace(), собираем путь на основе имени камеры и файла:
            django_file_path = os.path.join('/opt/mediaserver/django_app/recordings', camera_name, file_name)
            
            # Отсекаем расширение .mp4 -> "2026-05-24_09-28-40-650794"
            base_name = os.path.splitext(file_name)[0]
            
            try:
                # Так как маска ожидает ровно 19 символов (YYYY-MM-DD_HH-MM-SS),
                # мы делаем срез строки [:19], полностью игнорируя хвост из микросекунд.
                clean_date_str = base_name[:19]
                naive_datetime = datetime.strptime(clean_date_str, "%Y-%m-%d_%H-%M-%S")
                start_time = timezone.make_aware(naive_datetime)
            except ValueError:
                start_time = timezone.now()  # Фолбэк, если формат имени глобально изменится
                
            # Получаем реальный размер файла в байтах
            file_size = 0
            if os.path.exists(django_file_path):
                file_size = os.path.getsize(django_file_path)
            else:
                # Оставляем след в логах, если пути смонтированы несимметрично
                print(f"[WARNING] Файл не найден по пути: {django_file_path}")
                
            # Длительность сегмента
            duration = 3600 
            
            # Создаем или обновляем запись в базе данных
            CameraRecord.objects.update_or_create(
                file_path=django_file_path,
                defaults={
                    'camera': camera,
                    'file_name': file_name,
                    'file_size_bytes': file_size,
                    'start_time': start_time,
                    'duration_seconds': duration
                }
            )
            return HttpResponse("Record saved successfully", status=201)
            
        except Camera.DoesNotExist:
            return HttpResponse("Camera not found", status=404)
        except Exception as e:
            return HttpResponse(f"Error: {str(e)}", status=500)
            
    return HttpResponse("Method not allowed", status=405)


@login_required
def download_record_view(request, record_id):
    """Находит видеозапись по ID и отдает её пользователю для скачивания."""
    
    # 1. Получаем объект записи из БД или отдаем 404, если такого ID нет
    record = get_object_or_404(CameraRecord, id=record_id)
    
    # 2. Берем путь к файлу, сохраненный в базе данных
    file_path = record.file_path
    
    # 3. Проверяем, существует ли файл физически на диске контейнера Django
    if not os.path.exists(file_path):
        raise Http404("Файл видеозаписи физически не найден на сервере.")
        
    # 4. Открываем файл в бинарном режиме чтения
    # Использование FileResponse позволяет эффективно отдавать большие файлы (видео) частями
    response = FileResponse(open(file_path, 'rb'), content_type='video/mp4')
    
    # 5. Принудительно заставляем браузер скачивать файл, а не воспроизводить его на месте
    # Имя файла берем из базы данных
    response['Content-Disposition'] = f'attachment; filename="{record.file_name}"'
    
    return response

@require_POST  # Защищаем метод: удалять можно только через POST-запрос
@login_required # Раскомментируйте, если требуется авторизация
def delete_record_view(request, record_id):
    """Удаляет файл видеозаписи с диска и стирает запись из базы данных."""
    
    # 1. Получаем объект записи из БД
    record = get_object_or_404(CameraRecord, id=record_id)
    file_path = record.file_path
    
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
    return redirect(request.META.get('HTTP_REFERER', 'archive_list'))


@csrf_exempt
def mediamtx_record_stop_webhook(request):
    print(f"[DEBUG] Получен запрос на остановку записи от MediaMTX: {request.method} {request.GET}")
    if request.method != 'POST':
        return HttpResponse("Method not allowed", status=405)
        
    camera_name = request.GET.get('path')
    if not camera_name:
        return HttpResponse("Missing data", status=400)
            
    try:
        # 1. Получаем объект камеры из БД
        camera = Camera.objects.get(name=camera_name)
    except Camera.DoesNotExist:
        return HttpResponse("Camera not found", status=404)
        
    # 2. Формируем запрос к MediaMTX API
    url = f"{settings.MEDIAMTX_API_URL.rstrip('/')}/v3/config/paths/patch/{camera_name}"
    payload = {"record": False}
    
    try:
        response = requests.patch(url, json=payload, timeout=5)
        
        if response.status_code in [200, 201, 204]:
            # 3. Обновляем статус только после успешного ответа сервера
            camera.is_recording = False
            camera.save()
            return HttpResponse("Record stopped and DB updated", status=200)
        else:
            logger.error(f"MediaMTX API error {response.status_code}: {response.text}")
            return HttpResponse("MediaMTX API error", status=502)
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Connection error: {e}")
        return HttpResponse("MediaMTX unreachable", status=503)


@csrf_protect
def set_camera_resolution(request, camera_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)
    
    try:
        data = json.loads(request.body)
        res = data.get('resolution') # Ожидаем формат "1920x1080"
        if not res or 'x' not in res:
            return JsonResponse({'status': 'error', 'message': 'Неверный формат'}, status=400)
            
        width, height = res.split('x')
        camera = get_object_or_404(Camera, id=camera_id)
        
        # Вызываем метод изменения настроек
        camera.set_resolution(width, height)
        
        # Принудительно обновляем кэш после успешной записи
        from .onvif_service import update_camera_onvif_cache
        update_camera_onvif_cache(camera)
        
        return JsonResponse({'status': 'success'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@csrf_protect
def set_camera_fps(request, camera_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)
    
    try:
        data = json.loads(request.body)
        camera = get_object_or_404(Camera, id=camera_id)

        if 'fps' in data:
            camera.set_fps(data['fps'])
            
        # Обновляем кэш
        from .onvif_service import update_camera_onvif_cache
        update_camera_onvif_cache(camera)
        
        return JsonResponse({'status': 'success'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)