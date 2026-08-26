FROM python:3.11-slim-bookworm

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates nginx openvpn \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir cryptography==45.0.6

COPY dashboard_vpn.py /
COPY nginx.conf /etc/nginx/nginx.conf
COPY web /web

CMD ["python", "-u", "/dashboard_vpn.py"]
