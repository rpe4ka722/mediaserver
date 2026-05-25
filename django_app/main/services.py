import requests
from django.conf import settings

def mediamtx_add_path(camera):
    """Добавляет поток в конфигурацию MediaMTX."""
    mtx_api_base = settings.MEDIAMTX_API_URL.rstrip('/')
    url = f"{mtx_api_base}/v3/config/paths/add/{camera.name}"
    
    payload = {
        "source": camera.rtsp_url,
        "sourceProtocol": "tcp",
        "sourceOnDemand": True,  # Включаем здесь для конкретной камеры
        "sourceOnDemandCloseAfter": "10s",  # Закрывать через 10 секунд после последнего клиента
        "record": False,  # Изначально не записывать, запись будет управляться через toggle_record_view 
        # Хук: сегмент записи завершен (запись остановилась или создался новый кусок)
        "runOnRecordSegmentComplete": "curl -X POST http://django-app:8000/archive/webhook/record-created/?status=stopped&path=$MTX_PATH&file=$MTX_SEGMENT_PATH",
        "runOnUnread": "curl -X POST http://django-app:8000/archive/webhook/record_stop/?path=$MTX_PATH"
    }

    try:
        response = requests.post(url, json=payload, timeout=3)
        return response.status_code in [200, 201], response.text
    except requests.exceptions.RequestException as e:
        return False, str(e)

def mediamtx_delete_path(camera_name):
    """Удаляет поток из конфигурации MediaMTX."""
    mtx_api_base = settings.MEDIAMTX_API_URL.rstrip('/')
    url = f"{mtx_api_base}/v3/config/paths/delete/{camera_name}"
    
    try:
        response = requests.delete(url, timeout=3)
        # 404 тоже успех (пути уже нет)
        return response.status_code in [200, 404], response.text
    except requests.exceptions.RequestException as e:
        return False, str(e)


def mediamtx_edit_path(camera):
    """Обновляет существующий путь через PATCH"""
    url = f"{settings.MEDIAMTX_API_URL.rstrip('/')}/v3/config/paths/patch/{camera.name}"
    payload = {"source": camera.rtsp_url}
    try:
        response = requests.patch(url, json=payload, timeout=3)
        return response.status_code in [200, 204], response.text
    except Exception as e:
        return False, str(e)




def get_mediamtx_status():
    """
    Проверяет, отвечает ли API MediaMTX.
    """
    url = "http://127.0.0.1:9997/v3/config/get" # Можно использовать любой эндпоинт v3
    try:
        response = requests.get(url, timeout=2)
        if response.status_code == 200:
            return True
        return False
    except requests.exceptions.RequestException:
        return False