# Baiamonte AIS setup

Baiamonte AIS exchanges data with AISHub: it forwards your receiver's raw NMEA AIS feed and retrieves the community vessel network for your chosen area.

## Before starting

Apply at [AISHub Join Us](https://www.aishub.net/join-us). AISHub will email the feed destination and, once your station meets its quality requirements, provide the username used by its data API.

## Connect the AIS hardware

Choose **UDP**, **TCP**, or **Serial** under **AIS Receiver Connection**. UDP receivers normally send raw NMEA to the Home Assistant host on port `10110`; TCP mode connects to the configured receiver host and port; serial mode reads an attached radio at the selected device and baud rate. Most serial AIS radios use 38400 baud. The radio-channel setting records whether your hardware receives both international AIS channels, channel A at 161.975 MHz, or channel B at 162.025 MHz.

The app recognizes `!AIVDM`, `!AIVDO`, `!BSVDM`, and `!ABVDM` sentences. Open the app log after starting it. You should see the friendly receiver name, its network address, valid NMEA counts, and forwarding totals.

## App settings

- **AISHub Username:** the contributor username supplied by AISHub.
- **AISHub Feed Host:** the destination hostname or IP supplied by AISHub.
- **AISHub Feed Port:** your dedicated AISHub UDP port.
- **AIS Receiver Name:** the label shown in logs and Home Assistant status.
- **AIS Receiver Connection:** UDP listener, TCP client, or attached serial radio.
- **Use Attached USB GPS:** automatically uses a fresh NMEA fix for the estate position, map, and distance ranking.
- **Live Rain Radar on Dashboard / TV:** enable RainViewer independently for each surface.
- **FlightAware Airport Weather:** optional AeroAPI v4 observations, using an API key and ICAO airport code such as `LICC`.
- **Bounding Box:** the west, south, east, and north limits of the vessel watch area.
- **Multi-Ship Tracking:** creates a separate sensor for each vessel.
- **MMSI Filter:** optional comma-separated list of vessels to retain.
- **Ship Entity Timeout:** removes stale vessel entities.

Start the app and open **AIS** in the Home Assistant sidebar. A green AISHub state confirms downloads. The reciprocal feed card confirms receiver traffic and sharing.

## TV map

For a television or kiosk display, open `http://HOME_ASSISTANT_IP:8999/tv`. This Baiamonte-styled view includes the live map plus a distance-ranked side list of the 10 closest positioned boats, and refreshes automatically. Internal port `8099` remains dedicated to Home Assistant ingress.

Turn on **TV Live Weather Radar** to add current precipitation radar from RainViewer. Turn on **Live Rain Radar on Dashboard** for the Overview map. Adjust **TV Weather Opacity** between 10 and 100 if the radar is too faint or covers too much of the base map. Radar availability is best-effort; boats and the base map continue working if the weather service is temporarily unavailable.

On Overview and TV pages, drag the map to move it, pinch or use the wheel and gold plus/minus buttons to zoom, and choose **Reset** to return to the automatic view. The Overview map also has height controls and a lower-right resize corner. Its height is remembered by the browser. The TV layout includes a flexbox fallback and same-origin tile proxy for Samsung/Tizen browsers.

The **Watch Area** page includes the hardware receiver log, GPS/reference position, receiver profile, and optional FlightAware airport observation.

## Troubleshooting

- **Setup required:** add the AISHub username.
- **Receiver ready · AISHub destination needed:** add the feed host and assigned UDP port.
- **No hardware detected:** confirm the receiver is sending UDP NMEA to the Home Assistant host on port `10110` and that the port is reachable on your network.
- **Sharing error:** recheck the AISHub host and port supplied by email.
- **AISHub connection error:** confirm the username is active and wait at least one minute between retries.
- **Zero vessels:** verify the configured geographic bounds and maximum position age.

AISHub limits API access to one request per minute. Baiamonte AIS enforces that limit automatically.
