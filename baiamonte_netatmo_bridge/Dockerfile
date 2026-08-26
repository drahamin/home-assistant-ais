FROM python:3.11-slim-bookworm

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

RUN pip install --no-cache-dir websocket-client==1.8.0

COPY netatmo_bridge.py /
COPY web /web

CMD ["python", "-u", "/netatmo_bridge.py"]
