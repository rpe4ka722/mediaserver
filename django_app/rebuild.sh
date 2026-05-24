#!/bin/bash

# Название контейнера и образа для удаления
TARGET="django_app"

echo "=== Старт очистки Docker для: $TARGET ==="

# 1. Поиск и удаление контейнера
# Ищем контейнер по точному имени (включая остановленные)
CONTAINER_ID=$(docker ps -a -q --filter "name=^/${TARGET}$")

if [ -not -z "$CONTAINER_ID" ]; then
    echo "Найден контейнер $TARGET с ID: $CONTAINER_ID"
    
    # Проверяем, запущен ли он прямо сейчас
    if [ "$(docker ps -q --filter "name=^/${TARGET}$")" ]; then
        echo "Останавливаем запущенный контейнер..."
        docker stop "$CONTAINER_ID" > /dev/null
    fi
    
    echo "Удаляем контейнер..."
    docker rm "$CONTAINER_ID" > /dev/null
    echo "✓ Контейнер успешно удален."
else
    echo "Контейнер с именем $TARGET не найден."
fi

# 2. Поиск и удаление образа
# Ищем образ по имени репозитория
IMAGE_ID=$(docker images -q "$TARGET")

if [ -not -z "$IMAGE_ID" ]; then
    echo "Найден образ $TARGET с ID: $IMAGE_ID"
    echo "Удаляем образ..."
    # Используем -f (force) на случай, если он частично забаговался
    docker rmi -f "$IMAGE_ID" > /dev/null
    echo "✓ Образ успешно удален."
else
    echo "Образ с именем $TARGET не найден."
fi

docker compose down -v --remove-orphans
docker volume rm -f django_app_static_volume
python3 manage.py collectstatic --noinput --clear



echo "=== Очистка завершена ==="

docker build -t django_app .