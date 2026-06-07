from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import Camera
from .onvif_service import update_camera_onvif_cache
from django.core.cache import cache
import threading

@receiver(post_save, sender=Camera)
def camera_saved_handler(sender, instance, **kwargs):
    # Запускаем в отдельном потоке, чтобы не блокировать сохранение в админке
    threading.Thread(target=update_camera_onvif_cache, args=(instance,), daemon=True).start()

@receiver(post_delete, sender=Camera)
def camera_deleted_handler(sender, instance, **kwargs):
    cache.delete(f"cam_onvif_info_{instance.id}")