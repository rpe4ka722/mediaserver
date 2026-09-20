import logging

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import Camera
from .onvif_service import refresh_camera_onvif_cache_async
from django.core.cache import cache
from django.db import transaction

logger = logging.getLogger(__name__)

@receiver(post_save, sender=Camera)
def camera_saved_handler(sender, instance, **kwargs):
    # Запускаем запрос к камере только после успешного commit. Это исключает
    # работу фонового потока с записью, которая затем была откатана.
    transaction.on_commit(lambda: refresh_camera_onvif_cache_async(instance.pk))

@receiver(post_delete, sender=Camera)
def camera_deleted_handler(sender, instance, **kwargs):
    try:
        cache.delete(f"cam_onvif_info_{instance.id}")
        cache.delete(f"cam_onvif_refresh_lock_{instance.id}")
    except Exception:
        # Недоступный Redis не должен отменять удаление камеры из БД.
        logger.exception("Не удалось очистить ONVIF-кэш удалённой камеры %s", instance.id)
