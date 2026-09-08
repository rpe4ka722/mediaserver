import shlex

import requests
from django.conf import settings


def _mediamtx_path_payload(camera):
    """Формирует конфигурацию MediaMTX с серверным ограничением битрейта."""
    bitrate_k = max(1, settings.MEDIAMTX_VIDEO_BITRATE_K)
    source_url = shlex.quote(camera.rtsp_url)

    # MediaMTX запускает FFmpeg только при появлении читателя. FFmpeg получает
    # исходный RTSP напрямую от камеры, перекодирует видео на сервере и
    # публикует результат обратно в запрошенный path MediaMTX.
    ffmpeg_command = (
        f"ffmpeg -hide_banner -loglevel warning -rtsp_transport tcp -i {source_url} "
        f"-map 0:v:0 -map 0:a? -c:v libx264 -pix_fmt yuv420p -preset veryfast "
        f"-tune zerolatency -b:v {bitrate_k}k -maxrate {bitrate_k}k "
        f"-bufsize {bitrate_k * 2}k -c:a copy "
        f"-f rtsp -rtsp_transport tcp rtsp://127.0.0.1:$RTSP_PORT/$MTX_PATH"
    )

    return {
        "source": "publisher",
        "runOnDemand": ffmpeg_command,
        "runOnDemandRestart": True,
        "runOnDemandStartTimeout": "15s",
        "runOnDemandCloseAfter": "10s",
        "record": False,
        "runOnRecordSegmentComplete": "curl -X POST http://django-app:8000/archive/webhook/record-created/?status=stopped&path=$MTX_PATH&file=$MTX_SEGMENT_PATH",
        "runOnUnread": "curl -X POST http://django-app:8000/archive/webhook/record_stop/?path=$MTX_PATH",
    }


def mediamtx_add_path(camera):
    """Добавляет поток в MediaMTX с серверным перекодированием через FFmpeg."""
    mtx_api_base = settings.MEDIAMTX_API_URL.rstrip('/')
    url = f"{mtx_api_base}/v3/config/paths/add/{camera.name}"

    try:
        response = requests.post(url, json=_mediamtx_path_payload(camera), timeout=3)
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
    """Обновляет существующий путь и параметры серверного перекодирования."""
    url = f"{settings.MEDIAMTX_API_URL.rstrip('/')}/v3/config/paths/patch/{camera.name}"
    try:
        response = requests.patch(url, json=_mediamtx_path_payload(camera), timeout=3)
        return response.status_code in [200, 204], response.text
    except requests.exceptions.RequestException as e:
        return False, str(e)


def get_mediamtx_status():
    """Проверяет, отвечает ли API MediaMTX."""
    url = f"{settings.MEDIAMTX_API_URL.rstrip('/')}/v3/config/get"
    try:
        response = requests.get(url, timeout=2)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False
