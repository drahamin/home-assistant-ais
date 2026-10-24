FROM alpine:3.22

ENV APP_VERSION="0.1.0"

RUN apk add --no-cache \
      ca-certificates \
      iproute2 \
      iptables \
      openvpn \
      python3 \
      py3-flask \
      tzdata

WORKDIR /app
COPY manager.py /app/manager.py
COPY templates /app/templates
COPY run.sh /run.sh
RUN chmod 0755 /run.sh

EXPOSE 8100
CMD ["/run.sh"]

