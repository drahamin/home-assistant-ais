FROM python:3.11-alpine

COPY ais_ship_tracker.py /
COPY gps_reader.py /
COPY web /web

CMD ["python", "-u", "/ais_ship_tracker.py"]
