from django.apps import AppConfig


# class MainConfig(AppConfig):
#     default_auto_field = 'django.db.models.BigAutoField'
#     name = 'main'


class DjangoAppConfig(AppConfig):
    name = 'main'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        # ready() вызывается каждым Gunicorn worker. Здесь нельзя обращаться к
        # БД или камерам: это задерживает старт и дублирует ONVIF-запросы.
        import main.signals  # noqa: F401
