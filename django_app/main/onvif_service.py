import logging
import threading

from django.core.cache import cache
from django.db import close_old_connections

from .models import Camera

logger = logging.getLogger(__name__)


def update_camera_onvif_cache(camera):
    # Базовая структура по умолчанию
    data = {
        "status": "offline",
        "res": "—",
        "fps": "—",
        "supported": {
            "resolutions": [],
            "fps_range": {"min": 0, "max": 0}
        },
        "error": None
    }
    
    try:
        # 1. Получаем текущие данные
        info = camera.get_camera_info()
        
        if isinstance(info, dict) and not info.get("error"):
            data.update({
                "status": "online",
                "res": f"{info['resolution']['width']}x{info['resolution']['height']}",
                "fps": info['fps'],
            })
            
            # 2. Получаем поддерживаемые опции
            try:
                opts = camera.get_supported_options()
                fps_opts = camera.get_supported_fps_options()
                
                if "error" not in opts:
                    data["supported"]["resolutions"] = opts.get("resolutions", [])
                if "error" not in fps_opts:
                    data["supported"]["fps_range"] = {
                        "min": fps_opts.get("min"),
                        "max": fps_opts.get("max")
                    }
            except Exception as opt_err:
                logger.warning("Не удалось получить ONVIF-опции камеры %s: %s", camera.name, opt_err)
            
        else:
            data["error"] = info.get('error', 'Некорректный ответ ONVIF') if isinstance(info, dict) else 'Некорректный ответ ONVIF'
            
    except Exception as e:
        data["error"] = str(e)
        logger.exception("Ошибка обновления ONVIF-кэша камеры %s", camera.name)

    try:
        cache.set(f"cam_onvif_info_{camera.id}", data, None)
    except Exception:
        # Сбой Redis не должен превращать успешную ONVIF-команду в ошибку.
        logger.exception("Не удалось сохранить ONVIF-кэш камеры %s", camera.name)
    return data


def refresh_camera_onvif_cache_async(camera_id):
    """Обновляет ONVIF-кэш в фоне, не создавая дублирующих задач."""
    lock_key = f"cam_onvif_refresh_lock_{camera_id}"
    try:
        lock_acquired = cache.add(lock_key, True, timeout=30)
    except Exception:
        logger.exception("Не удалось установить блокировку ONVIF-кэша камеры %s", camera_id)
        return False

    if not lock_acquired:
        return False

    def _refresh():
        close_old_connections()
        try:
            camera = Camera.objects.get(pk=camera_id)
            update_camera_onvif_cache(camera)
        except Camera.DoesNotExist:
            cache.delete(f"cam_onvif_info_{camera_id}")
        except Exception:
            logger.exception("Фоновое обновление ONVIF-кэша камеры %s завершилось ошибкой", camera_id)
        finally:
            try:
                cache.delete(lock_key)
            except Exception:
                logger.exception("Не удалось снять блокировку ONVIF-кэша камеры %s", camera_id)
            close_old_connections()

    threading.Thread(target=_refresh, daemon=True).start()
    return True
