# Baiamonte AIS setup

Baiamonte AIS exchanges data with AISHub: it forwards your receiver's raw NMEA AIS feed and retrieves the community vessel network for your chosen area.

## Before starting

Apply at [AISHub Join Us](https://www.aishub.net/join-us). AISHub will email the feed destination and, once your station meets its quality requirements, provide the username used by its data API.

## Connect the AIS hardware

Configure the receiver or its forwarding software to send raw NMEA over UDP to the IP address of your Home Assistant machine on port `10110`.

The app recognizes `!AIVDM`, `!AIVDO`, `!BSVDM`, and `!ABVDM` sentences. Open the app log after starting it. You should see the friendly receiver name, its network address, valid NMEA counts, and forwarding totals.

## App settings

- **AISHub Username:** the contributor username supplied by AISHub.
- **AISHub Feed Host:** the destination hostname or IP supplied by AISHub.
- **AISHub Feed Port:** your dedicated AISHub UDP port.
- **AIS Receiver Name:** the label shown in logs and Home Assistant status.
- **Bounding Box:** the west, south, east, and north limits of the vessel watch area.
- **Multi-Ship Tracking:** creates a separate sensor for each vessel.
- **MMSI Filter:** optional comma-separated list of vessels to retain.
- **Ship Entity Timeout:** removes stale vessel entities.

Start the app and open **AIS** in the Home Assistant sidebar. A green AISHub state confirms downloads. The reciprocal feed card confirms receiver traffic and sharing.

## Troubleshooting

- **Setup required:** add the AISHub username.
- **Receiver ready · AISHub destination needed:** add the feed host and assigned UDP port.
- **No hardware detected:** confirm the receiver is sending UDP NMEA to the Home Assistant host on port `10110` and that the port is reachable on your network.
- **Sharing error:** recheck the AISHub host and port supplied by email.
- **AISHub connection error:** confirm the username is active and wait at least one minute between retries.
- **Zero vessels:** verify the configured geographic bounds and maximum position age.

AISHub limits API access to one request per minute. Baiamonte AIS enforces that limit automatically.
