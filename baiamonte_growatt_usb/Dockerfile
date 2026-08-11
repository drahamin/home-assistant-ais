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
    && pip install --no-cache-dir mppsolar==0.16.56 pyserial==3.5 pymodbus==3.6.9

COPY growatt_usb.py /
COPY web /web

CMD ["python", "-u", "/growatt_usb.py"]
