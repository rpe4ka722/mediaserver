import os
import logging
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.validators import MaxValueValidator, MinValueValidator
from onvif import ONVIFCamera
from zeep.transports import Transport



logger = logging.getLogger(__name__)

class Camera(models.Model):
    name = models.SlugField(unique=True) # Идентификатор для MediaMTX
    description = models.CharField(max_length=500, null=True, blank=True)
    camera_login =  models.CharField(max_length=100, null=True, blank=True)
    camera_password =  models.CharField(max_length=100, null=True, blank=True)
    camera_address = models.GenericIPAddressField(null=True, blank=True)
    camera_port = models.IntegerField(
        null=True, 
        blank=True, 
        default=554,
        validators=[MinValueValidator(1), MaxValueValidator(65535)]
    )
    camera_path = models.CharField(
        max_length=255, 
        default="/stream1", 
        help_text="Остаток пути после IP и порта"
    )
    onvif_port = models.IntegerField(
        null=True, 
        blank=True, 
        default=80,
        validators=[MinValueValidator(1), MaxValueValidator(65535)]
    )

    is_recording = models.BooleanField(
        default=False, 
        verbose_name="Статус записи (Запущена/Остановлена)"
    )

    @property
    def rtsp_url(self):
        """Собирает полную строку rtsp://login:pass@ip:port/path"""
        auth = ""
        if self.camera_login and self.camera_password:
            auth = f"{self.camera_login}:{self.camera_password}@"
        
        path = self.camera_path.lstrip('/') # Убираем лишние слеши слева для чистоты
        
        return f"rtsp://{auth}{self.camera_address}:{self.camera_port}/{path}"


    def __str__(self):
        return f"{self.name} ({self.camera_address})"

    
    def get_onvif_client(self, timeout=5):
        if not self.camera_address:
            return None
        
        # Уменьшаем таймаут до минимума для проверки
        transport = Transport(timeout=timeout, operation_timeout=timeout)

        try:
            # no_cache=True заставляет клиента не пытаться 
            # делать лишние запросы при инициализации
            client = ONVIFCamera(
                self.camera_address,
                self.onvif_port,
                self.camera_login,
                self.camera_password,
                transport=transport
            )
            return client
        except Exception as e:
            logger.error(f"Ошибка инициализации ONVIF для {self.name}: {e}")
            return None


    def execute_onvif_call(self, func, *args, timeout=5, **kwargs):
        """Универсальный обработчик для вызовов ONVIF"""
        try:
            client = self.get_onvif_client(timeout=timeout)
            if not client:
                raise Exception("Не удалось инициализировать ONVIF-клиент")
            
            # Создаем сервис здесь один раз
            media_service = client.create_media_service()
            
            # Передаем и клиент, и сервис в функцию
            return func(client, media_service, *args, **kwargs)
            
        except Exception as e:
            logger.error(f"ONVIF Error for {self.name}: {e}")
            return {"error": str(e)}
            

    def get_camera_info(self):
        def _action(client, media_service):
            dev_service = client.create_devicemgmt_service()
            device_info = dev_service.GetDeviceInformation()
            profiles = media_service.GetProfiles()
            main_profile = profiles[0]
            
            encoder_config = media_service.GetVideoEncoderConfiguration(
                {'ConfigurationToken': main_profile.VideoEncoderConfiguration.token}
            )
            return {
                "manufacturer": device_info.Manufacturer,
                "model": device_info.Model,
                "firmware": device_info.FirmwareVersion,
                "resolution": {"width": encoder_config.Resolution.Width, "height": encoder_config.Resolution.Height},
                "fps": encoder_config.RateControl.FrameRateLimit
            }
        
        return self.execute_onvif_call(_action)
        
    def get_supported_options(self):
        def _action(client, media_service):
            profiles = media_service.GetProfiles()
            main_profile = profiles[0]
            options = media_service.GetVideoEncoderConfigurationOptions({
                'ProfileToken': main_profile.token,
                'ConfigurationToken': main_profile.VideoEncoderConfiguration.token
            })
            return {
                "resolutions": [f"{res.Width}x{res.Height}" for res in options.H264.ResolutionsAvailable],
                "max_fps": options.H264.FrameRateRange.Max,
                "min_fps": options.H264.FrameRateRange.Min
            }
        
        return self.execute_onvif_call(_action)

    def get_supported_fps_options(self):
        def _action(client, media_service):
            profiles = media_service.GetProfiles()
            main_profile = profiles[0]
            options = media_service.GetVideoEncoderConfigurationOptions({
                'ProfileToken': main_profile.token,
                'ConfigurationToken': main_profile.VideoEncoderConfiguration.token
            })
            fps_range = options.H264.FrameRateRange
            return {
                "min": fps_range.Min,
                "max": fps_range.Max,
                "description": f"От {fps_range.Min} до {fps_range.Max} кадров в секунду"
            }
            
        return self.execute_onvif_call(_action)

    def set_resolution(self, width, height):
        def _action(client, media_service):
            profiles = media_service.GetProfiles()
            token = profiles[0].VideoEncoderConfiguration.token
            config = media_service.GetVideoEncoderConfiguration({'ConfigurationToken': token})
            
            config.Resolution.Width = int(width)
            config.Resolution.Height = int(height)
            
            request = media_service.create_type('SetVideoEncoderConfiguration')
            request.Configuration = config
            request.ForcePersistence = True
            return media_service.SetVideoEncoderConfiguration(request)
            
        return self.execute_onvif_call(_action)

    def set_fps(self, fps):
        def _action(client, media_service):
            profiles = media_service.GetProfiles()
            token = profiles[0].VideoEncoderConfiguration.token
            config = media_service.GetVideoEncoderConfiguration({'ConfigurationToken': token})

            # Обновляем FPS
            config.RateControl.FrameRateLimit = int(fps)

            request = media_service.create_type('SetVideoEncoderConfiguration')
            request.Configuration = config
            request.ForcePersistence = True
            return media_service.SetVideoEncoderConfiguration(request)

        return self.execute_onvif_call(_action)

    class Meta:
        verbose_name = "Камера"
        verbose_name_plural = "Камеры"


class CameraRecord(models.Model):
    camera = models.ForeignKey(
        Camera,
        on_delete=models.CASCADE,
        related_name='records',
        verbose_name=_("Камера")
    )
    file_name = models.CharField(
        max_length=255,
        verbose_name=_("Имя файла"),
        help_text=_("Например: 2026-05-24_12-00-00.mp4")
    )
    file_path = models.CharField(
        max_length=500,
        unique=True,
        verbose_name=_("Полный путь к файлу"),
        help_text=_("Путь внутри контейнера Django для проверки физического наличия")
    )
    file_size_bytes = models.BigIntegerField(
        default=0,
        verbose_name=_("Размер файла (в байтах)")
    )
    start_time = models.DateTimeField(
        verbose_name=_("Время начала записи"),
        db_index=True
    )
    duration_seconds = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Длительность (сек.)"),
        help_text=_("Длительность куска видео (выставляется на основе конфига MediaMTX)")
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("Добавлено в базу")
    )

    class Meta:
        verbose_name = _("Запись архива")
        verbose_name_plural = _("Записи архива")
        ordering = ['-start_time']  # По умолчанию выводим сначала самые свежие записи

    def __str__(self):
        return f"{self.camera.name} — {self.file_name}"

    @property
    def file_size_mb(self):
        """Возвращает размер файла в Мегабайтах для вывода в интерфейсе"""
        if self.file_size_bytes:
            return round(self.file_size_bytes / (1024 * 1024), 2)
        return 0.0

    @property
    def is_file_exists(self):
        """Быстрая проверка: не удалил ли MediaMTX этот файл по таймауту"""
        return os.path.exists(self.file_path)

    # # --- ПЕРЕОПРЕДЕЛЕНИЕ МЕТОДА DELETE ---
    # def delete(self, *args, **kwargs):
    #     """
    #     Переопределенный метод удаления: сначала удаляет физический файл с диска,
    #     а затем стирает саму запись из базы данных.
    #     """
    #     if self.file_path:
    #         try:
    #             if os.path.exists(self.file_path):
    #                 os.remove(self.file_path)
    #                 logger.info(f"Физический файл успешно удален с диска: {self.file_path}")
    #             else:
    #                 logger.warning(f"Попытка удалить файл, но он уже отсутствует на диске: {self.file_path}")
    #         except Exception as e:
    #             logger.error(f"Не удалось удалить физический файл {self.file_path}. Ошибка: {e}")
    #             # Опционально: если файл удалить НЕ удалось (например, нет прав), 
    #             # можно прервать удаление записи из БД, не вызывая super().delete()
        
    #     # Вызываем стандартный метод Django для удаления строки из базы данных
    #     super().delete(*args, **kwargs)
