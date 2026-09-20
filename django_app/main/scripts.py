import logging
import secrets
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse

logger = logging.getLogger(__name__)


def parse_ami_response(s:str):
    result = dict()
    raw_list = []
    raw_list = s.split('\r\n')
    for raw in raw_list:
        if raw != '':
            result[raw.split(': ')[0]] = raw.split(': ')[1]
    return result


def is_staff(view_func):
    """Требует авторизацию и роль staff; сохранено имя для совместимости."""
    @wraps(view_func)
    @login_required(login_url='account:login')
    def decorator(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return decorator


staff_required = is_staff


def mediamtx_webhook_required(view_func):
    """Проверяет общий секрет в webhook-запросах MediaMTX."""
    @wraps(view_func)
    def decorator(request, *args, **kwargs):
        expected = settings.MEDIAMTX_WEBHOOK_TOKEN
        if not expected:
            logger.error("MEDIAMTX_WEBHOOK_TOKEN не настроен")
            return HttpResponse("Webhook authentication is not configured", status=503)

        supplied = request.headers.get('X-MediaMTX-Webhook-Token', '')
        if not supplied or not secrets.compare_digest(supplied, expected):
            return HttpResponse("Forbidden", status=403)

        return view_func(request, *args, **kwargs)

    return decorator
