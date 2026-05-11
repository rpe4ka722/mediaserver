from django.contrib.auth.models import AbstractUser
from django.db import models

# Create your models here.
class CustomUser(AbstractUser):

    patronymic = models.CharField(verbose_name="Отчетство", max_length=30, blank=True, null=True, default=None)

    def __str__(self):
        return self.username 