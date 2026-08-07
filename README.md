# Baiamonte AIS

Baiamonte AIS is a Home Assistant app for reciprocal vessel tracking through [AISHub](https://www.aishub.net/). It accepts raw NMEA data from your local AIS receiver, forwards that feed to AISHub, and retrieves the shared AISHub vessel network for your configured watch area.

The app includes a Tenuta Baiamonte-styled Home Assistant sidebar dashboard, live vessel entities, receiver health logging, and automatic container updates through GitHub.

## TV map

Open `http://HOME_ASSISTANT_IP:8099/tv` on a television, kiosk browser, or dashboard iframe for a full-screen Baiamonte view with a large live map and a side list of boats, flags, speeds, headings, MMSIs, status, and destinations. The map automatically fits the configured watch area and refreshes every ten seconds. Port 8099 is exposed by default in the app's Network settings.

Enable **TV Live Weather Radar** in the app configuration to place current RainViewer precipitation radar over the vessel map. **TV Weather Opacity** accepts values from 10 to 100 and defaults to 65. The radar source and observation time appear on the map whenever the layer is enabled.

The Overview vessel map can be dragged to reposition the watch area, zoomed with the mouse wheel or gold controls, reset to its original view, and resized with the height buttons or its lower-right corner. The chosen height is saved in that browser.

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
