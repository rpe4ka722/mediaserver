import os
import logging
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.validators import MaxValueValidator, MinValueValidator

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
