FROM python:3.12-slim


# Создаем рабочую директорию
WORKDIR /opt/mediaserver/django_app

# Устанавливаем зависимости Python через pip
# Использование --no-cache-dir критично для уменьшения размера образа
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем проект
COPY . .
