"""Isolated test database and cache; no production services required."""
import os

os.environ.setdefault('SECRET_KEY', 'test-only-not-for-deployment')
os.environ.setdefault('ALLOWED_HOSTS', 'testserver localhost')

from .settings import *  # noqa: F403,E402

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
