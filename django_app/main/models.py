from django.db import models
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
