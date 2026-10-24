ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.22
FROM ${BUILD_FROM}

ARG BUILD_VERSION=0.1.0
ENV APP_VERSION=${BUILD_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY relay.py run.sh ./
RUN chmod 0755 /app/run.sh /app/relay.py

EXPOSE 8104/tcp 15000/udp 14500/udp
CMD ["/app/run.sh"]

