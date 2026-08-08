# Baiamonte AIS

Baiamonte AIS is a Home Assistant app for reciprocal vessel tracking through [AISHub](https://www.aishub.net/). It accepts raw NMEA data from your local AIS receiver, forwards that feed to AISHub, and retrieves the shared AISHub vessel network for your configured watch area.

The app includes a Tenuta Baiamonte-styled Home Assistant sidebar dashboard, live vessel entities, receiver health logging, and automatic container updates through GitHub.

## TV map

Open `http://HOME_ASSISTANT_IP:8999/tv` on a television or kiosk browser for a full-screen Baiamonte view with a large live map and the 10 closest positioned boats. The map automatically fits the configured watch area and refreshes every ten seconds. Home Assistant ingress continues to use internal port `8099`.

Enable **TV Live Weather Radar** in the app configuration to place current RainViewer precipitation radar over the vessel map. **TV Weather Opacity** accepts values from 10 to 100 and defaults to 65. The radar source and observation time appear on the map whenever the layer is enabled.

The Overview and TV maps can be dragged, pinched, zoomed with the wheel or gold controls, and reset. The Overview map can also be resized. Same-origin tile proxies and a flexbox layout fallback support older Samsung/Tizen TV browsers.

## How the exchange works

1. Apply to [join AISHub](https://www.aishub.net/join-us) with an operational AIS receiver.
2. AISHub emails you a dedicated UDP destination and later supplies your username when the feed qualifies.
3. Configure your AIS receiver to send raw NMEA UDP data to the Home Assistant host on port `10110`.
4. Enter the AISHub username, destination host, and destination port in the Baiamonte AIS configuration.
5. Baiamonte AIS shares your receiver messages and downloads vessels inside your selected coverage area every 60 seconds.

AISHub requires an average of at least 10 vessels, 90% uptime, no more than 60 seconds of downsampling, and no more than 10 seconds of message delay for aggregated API access.

## Installation

Add this repository to the Home Assistant App Store:

`https://github.com/drahamin/home-assistant-ais`

Install **Baiamonte AIS**, configure it, start it, and enable **Show in sidebar**. Enable **Automatic updates** on the app Info page if you want Home Assistant to install new releases automatically.

## Configuration

| Setting | Purpose |
| --- | --- |
| AISHub Username | Contributor username supplied by AISHub |
| AISHub Feed Host | Feed destination host supplied by AISHub |
| AISHub Feed Port | Dedicated UDP port supplied by AISHub |
| AIS Receiver Name | Friendly hardware name used in logs and status sensors |
| AIS Receiver Connection | UDP, TCP, or serial NMEA receiver |
| AIS radio channel | Dual channel, 161.975 MHz channel A, or 162.025 MHz channel B |
| USB GPS | Automatic estate reference location for map and distance ranking |
| Dashboard / TV live rain | Independent RainViewer precipitation overlays |
| FlightAware weather | Optional AeroAPI airport observation on Watch Area |
| Longitude/Latitude bounds | Geographic area returned by the AISHub API |
| Multi-Ship Tracking | Creates one Home Assistant entity per active vessel |
| MMSI Filter | Optional comma-separated list of nine-digit MMSIs |
| Ship Entity Timeout | Removes vessels that have not updated within this period |

The AISHub API does not allow polling more often than once per minute, so the app enforces a minimum 60-second interval.

## Receiver connection and logging

Send NMEA UDP packets from the receiver to:

`HOME_ASSISTANT_IP:10110`

Accepted sentence prefixes are `!AIVDM`, `!AIVDO`, `!BSVDM`, and `!ABVDM`. The app logs:

- Receiver startup and listening port
- Receiver IP address and source port
- Hardware source changes
- Valid and ignored NMEA message counts
- Messages forwarded to AISHub
- Forwarding or socket errors
- A health summary approximately once per minute while traffic is flowing

No AISHub username, receiver payload, or geographic coordinates are sent to any Baiamonte telemetry service.

## Home Assistant entities

- `sensor.baiamonte_ais_connection_status`
- `sensor.baiamonte_ais_last_passing_ship`
- `sensor.baiamonte_ais_ship_<mmsi>` when Multi-Ship Tracking is enabled

The connection sensor includes AISHub status, API update time, receiver feed state, hardware message totals, shared-message totals, and last receiver activity.

## Updates

GitHub Actions publishes versioned `amd64` and `aarch64` images to `ghcr.io/drahamin/home-assistant-ais` whenever a release reaches `main`. Home Assistant compares the installed version with `ais_ship_tracker/config.yaml` and offers the new version automatically.

## Data source

Aggregated vessel data and contributor exchange are provided by [AISHub](https://www.aishub.net/). AIS data is informational and must not be used as the sole source for navigation or safety decisions.
