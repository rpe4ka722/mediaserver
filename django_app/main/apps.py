from django.apps import AppConfig


# class MainConfig(AppConfig):
#     default_auto_field = 'django.db.models.BigAutoField'
#     name = 'main'


class DjangoAppConfig(AppConfig):
    name = 'main'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
            import main.signals
            import os

            lock_file = '/tmp/app_init_done.lock'

            if os.path.exists(lock_file):
                return
            
            # Создаем файл-флаг
            with open(lock_file, 'w') as f:
                f.write('initialized')

            from django.db import connection
            try:
                # Проверяем наличие таблицы перед запросом
                if 'main_camera' in connection.introspection.table_names():
                    from .models import Camera
                    all_cameras = Camera.objects.all()
                    
                    if all_cameras.exists():
                        from .onvif_service import update_camera_onvif_cache
                        for camera in all_cameras:
                            update_camera_onvif_cache(camera)
            except Exception:
                # Если возникла ошибка, удаляем файл, чтобы попробовать снова при следующем старте
                if os.path.exists(lock_file):
                    os.remove(lock_file)