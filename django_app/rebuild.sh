#!/bin/bash

TARGET="django_app"

echo "=== Старт очистки Docker для: $TARGET ==="

# 1. Используем Compose для корректного удаления сервисов
# Если есть файл docker-compose.yml, он сам удалит все, что создал
if [ -f "../docker-compose.yml" ]; then
    cd ..
    echo "Запуск docker compose down..."
    docker compose down -v --remove-orphans
    cd django_app
else
    echo "Файл docker-compose.yml не найден, пропускаем compose down."
fi


# 2. Удаление тома (если он не был удален через -v выше)
docker volume rm -f django_app_static_volume

# 3. Принудительное удаление образа, чтобы гарантировать чистую сборку
IMAGE_ID=$(docker images -q "$TARGET")
if [ -n "$IMAGE_ID" ]; then
    echo "Удаление старого образа: $IMAGE_ID"
    docker rmi -f "$IMAGE_ID"
fi

# 4. Выполнение Django команд
echo "Сбор статики..."
python3 manage.py collectstatic --noinput --clear

echo "=== Очистка завершена. Начинаем сборку ==="

# 5. Сборка
docker build --no-cache -t "$TARGET" .