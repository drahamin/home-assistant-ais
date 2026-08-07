FROM python:3.11-alpine

RUN pip install websocket-client paho-mqtt

COPY ais_ship_tracker.py tracker_uuid.txt* /
COPY web /web

CMD ["python", "-u", "/ais_ship_tracker.py"]
