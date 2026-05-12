from django.shortcuts import render, redirect, get_object_or_404
import requests
from django.contrib.auth.decorators import login_required
from .models import Camera
from .services import mediamtx_add_path, mediamtx_delete_path, mediamtx_edit_path
from django.contrib import messages
from django.conf import settings
from urllib.parse import quote
from django.http import JsonResponse
from django.db import transaction
import os
import platform
import socket
import subprocess


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
                    camera_path=path
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
        
        new_name = camera.name
        new_path = camera.camera_path

        try:
            with transaction.atomic():
                # 1. Сначала сохраняем изменения в БД
                camera.save()

                # 2. Проверяем доступность MediaMTX через наш "предохранитель"
                # Передаем уже сохраненный ID, функция подтянет новые данные
                status, message = check_path_or_create(camera.id)
                if not status:
                    raise Exception(f"MediaMTX недоступен или ошибка пути: {message}")

                # 3. Синхронизация логики путей
                # Если путь в MediaMTX (camera_path) изменился:
                if old_path != new_path:
                    mediamtx_delete_path(old_path)
                    success, error = mediamtx_add_path(camera)
                else:
                    # Если путь тот же, просто обновляем параметры внутри (source и т.д.)
                    success, error = mediamtx_edit_path(camera)

                if not success:
                    raise Exception(f"Ошибка API MediaMTX: {error}")

                messages.success(request, f'Камера "{new_name}" обновлена.')

        except Exception as e:
            messages.error(request, f'Ошибка сохранения: {e}')
            logger.error(f"Error updating camera {camera_id}: {str(e)}")

    return redirect('main:camera')


@login_required(login_url='account:login')
def get_camera_stream_url(request, camera_id):

        camera = get_object_or_404(Camera, id=camera_id)
        
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

        path_name = camera.camera_path
        api_url = settings.MEDIAMTX_API_URL.rstrip('/')

        # 1. Проверка наличия
        check_res = requests.get(f"{api_url}/v3/paths/get/{path_name}", timeout=5)

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


