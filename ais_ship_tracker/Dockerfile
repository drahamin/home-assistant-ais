FROM debian:bookworm-slim AS ais-catcher-build

ARG AISCATCHER_VERSION=v0.70
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates cmake git librtlsdr-dev libsqlite3-dev pkg-config \
    && git clone --branch "${AISCATCHER_VERSION}" --depth 1 https://github.com/jvde-github/AIS-catcher.git /src/AIS-catcher \
    && cmake -S /src/AIS-catcher -B /src/AIS-catcher/build -DWEBVIEWER=OFF -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /src/AIS-catcher/build --parallel 2

FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends librtlsdr0 libsqlite3-0 libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ais-catcher-build /src/AIS-catcher/build/AIS-catcher /usr/local/bin/AIS-catcher
COPY --from=ais-catcher-build /src/AIS-catcher/LICENSE /usr/share/licenses/AIS-catcher/LICENSE

COPY ais_ship_tracker.py /
COPY gps_reader.py /
COPY web /web

CMD ["python", "-u", "/ais_ship_tracker.py"]
