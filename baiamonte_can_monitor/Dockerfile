FROM python:3.11-slim-bookworm

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "python-can[gs_usb]==4.6.1" pyserial==3.5

COPY can_decoder.py can_monitor.py /

CMD ["python", "-u", "/can_monitor.py"]
