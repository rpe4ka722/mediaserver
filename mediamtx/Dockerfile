FROM bluenviron/mediamtx:1 AS mediamtx

# Финальный этап: полноценная Ubuntu 24.04 с MediaMTX и FFmpeg.
FROM ubuntu:24.04

# Копируем исполняемый файл MediaMTX и стандартный конфиг из официального образа.
COPY --from=mediamtx /mediamtx /
COPY --from=mediamtx /mediamtx.yml /

# FFmpeg используется для серверного перекодирования потока и ограничения битрейта.
# curl нужен для существующих webhook-команд MediaMTX.
RUN apt update && apt install -y \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT [ "/mediamtx" ]
