FROM debian:bookworm-slim AS ais-catcher-build

ARG AISCATCHER_VERSION=v0.70
ARG RTL_AIRBAND_VERSION=v5.2.0
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates cmake git librtlsdr-dev libsqlite3-dev pkg-config libmp3lame-dev libshout3-dev libconfig++-dev libfftw3-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --branch "${AISCATCHER_VERSION}" --depth 1 https://github.com/jvde-github/AIS-catcher.git /src/AIS-catcher \
    && cmake -S /src/AIS-catcher -B /src/AIS-catcher/build -DWEBVIEWER=OFF -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /src/AIS-catcher/build --parallel 2
RUN git clone --branch "${RTL_AIRBAND_VERSION}" --depth 1 https://github.com/rtl-airband/RTLSDR-Airband.git /src/RTLSDR-Airband \
    && cd /src/RTLSDR-Airband \
    && cmake -B build -DPLATFORM=generic -DCMAKE_BUILD_TYPE=Release -DNFM=TRUE -DBUILD_UNITTESTS=FALSE \
    && cmake --build build --parallel 2

FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends icecast2 librtlsdr0 libsqlite3-0 libusb-1.0-0 libmp3lame0 libshout3 libconfig++9v5 libfftw3-double3 libfftw3-single3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ais-catcher-build /src/AIS-catcher/build/AIS-catcher /usr/local/bin/AIS-catcher
COPY --from=ais-catcher-build /src/AIS-catcher/LICENSE /usr/share/licenses/AIS-catcher/LICENSE
COPY --from=ais-catcher-build /src/RTLSDR-Airband/build/src/rtl_airband /usr/local/bin/rtl_airband
COPY --from=ais-catcher-build /src/RTLSDR-Airband/LICENSE /usr/share/licenses/RTLSDR-Airband/LICENSE

COPY ais_ship_tracker.py /
COPY gps_reader.py /
COPY web /web

CMD ["python", "-u", "/ais_ship_tracker.py"]
