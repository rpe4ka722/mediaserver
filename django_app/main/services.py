import shlex
from urllib.parse import quote

import requests
from django.conf import settings


def mediamtx_request(method, endpoint, **kwargs):
    """Выполняет аутентифицированный запрос к Control API MediaMTX."""
    base_url = settings.MEDIAMTX_API_URL.rstrip('/')
    username = settings.MEDIAMTX_API_USERNAME
    password = settings.MEDIAMTX_API_PASSWORD
    if not username or not password:
        raise RuntimeError("MediaMTX API credentials are not configured")

    kwargs.setdefault('timeout', 3)
    kwargs['auth'] = (username, password)
    return requests.request(method, f"{base_url}/{endpoint.lstrip('/')}", **kwargs)


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
        f"-f rtsp -rtsp_transport tcp "
        f"rtsp://internal-publisher:$PUBLISH_PASSWORD@127.0.0.1:$RTSP_PORT/$MTX_PATH"
    )

    return {
        "source": "publisher",
        "runOnDemand": ffmpeg_command,
        "runOnDemandRestart": True,
        "runOnDemandStartTimeout": "15s",
        "runOnDemandCloseAfter": "10s",
        "record": False,
        "runOnRecordSegmentComplete": "curl -fsS -X POST -H \"X-MediaMTX-Webhook-Token: $WEBHOOK_TOKEN\" \"http://django-app:8000/archive/webhook/record-created/?status=stopped&path=$MTX_PATH&file=$MTX_SEGMENT_PATH\"",
        "runOnUnread": "curl -fsS -X POST -H \"X-MediaMTX-Webhook-Token: $WEBHOOK_TOKEN\" \"http://django-app:8000/archive/webhook/record_stop/?path=$MTX_PATH\"",
    }


def mediamtx_add_path(camera):
    """Добавляет поток в MediaMTX с серверным перекодированием через FFmpeg."""
    path_name = quote(camera.name, safe='')

    try:
        response = mediamtx_request('POST', f"/v3/config/paths/add/{path_name}", json=_mediamtx_path_payload(camera))
        return response.status_code in [200, 201], response.text
    except (requests.exceptions.RequestException, RuntimeError) as e:
        return False, str(e)


def mediamtx_delete_path(camera_name):
    """Удаляет поток из конфигурации MediaMTX."""
    path_name = quote(camera_name, safe='')

    try:
        response = mediamtx_request('DELETE', f"/v3/config/paths/delete/{path_name}")
        # 404 тоже успех (пути уже нет)
        return response.status_code in [200, 404], response.text
    except (requests.exceptions.RequestException, RuntimeError) as e:
        return False, str(e)


def mediamtx_edit_path(camera):
    """Обновляет существующий путь и параметры серверного перекодирования."""
    path_name = quote(camera.name, safe='')
    try:
        response = mediamtx_request('PATCH', f"/v3/config/paths/patch/{path_name}", json=_mediamtx_path_payload(camera))
        if response.status_code == 404:
            return mediamtx_add_path(camera)
        return response.status_code in [200, 204], response.text
    except (requests.exceptions.RequestException, RuntimeError) as e:
        return False, str(e)


def get_mediamtx_status():
    """Проверяет, отвечает ли API MediaMTX."""
    try:
        response = mediamtx_request('GET', '/v3/config/get', timeout=2)
        return response.status_code == 200
    except (requests.exceptions.RequestException, RuntimeError):
        return False
