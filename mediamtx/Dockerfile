FROM bluenviron/mediamtx:1 AS mediamtx

# 2. Финальный этап: разворачиваем полноценную Ubuntu 24.04
FROM ubuntu:24.04

# 3. Копируем исполняемый файл медиасервера и его стандартный конфиг из первого образа
COPY --from=mediamtx /mediamtx /
COPY --from=mediamtx /mediamtx.yml /

# 4. Обновляем списки пакетов Ubuntu и устанавливаем curl
RUN apt update && apt install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 5. Инструкция для запуска медиасервера при старте контейнера
ENTRYPOINT [ "/mediamtx" ]