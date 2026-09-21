FROM python:3.11-slim-bookworm

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="${BUILD_ARCH}"

COPY engine.py power_manager.py /
COPY web /web

CMD ["python", "-u", "/power_manager.py"]
