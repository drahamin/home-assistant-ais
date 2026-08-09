# Baiamonte AIS

Baiamonte AIS is an end-to-end Home Assistant vessel station. It includes [AIS-catcher](https://github.com/jvde-github/AIS-catcher) for dual-channel decoding from an RTL-SDR such as the Nooelec NESDR SMArt v5, displays locally received vessels immediately, and can exchange the original NMEA feed with [AISHub](https://www.aishub.net/) for wider-area coverage. A second Nooelec can run a receive-only marine VHF scanner with live audio in the AIS sidebar.

The app includes a Tenuta Baiamonte-styled Home Assistant sidebar dashboard, live vessel entities, receiver health logging, and automatic container updates through GitHub.

## TV map

Open `http://HOME_ASSISTANT_IP:8999/tv` on a television or kiosk browser for a full-screen Baiamonte view with a large live map and the 10 closest positioned boats. The map automatically fits the configured watch area and refreshes every ten seconds. Home Assistant ingress continues to use internal port `8099`.

Enable **TV Live Weather Radar** in the app configuration to place current RainViewer precipitation radar over the vessel map. **TV Weather Opacity** accepts values from 10 to 100 and defaults to 65. The radar source and observation time appear on the map whenever the layer is enabled.

The Overview and TV maps can be dragged, pinched, zoomed with the wheel or gold controls, and reset. The Overview map can also be resized. Same-origin tile proxies and a flexbox layout fallback support older Samsung/Tizen TV browsers.

## How the exchange works

1. Attach the RTL-SDR and a VHF antenna suitable for the AIS channels around 162 MHz.
2. Select **SDR / included AIS-catcher**. The Nooelec-safe starting profile is device `0`, automatic gain, `0` PPM, RTL AGC on, 192K filter, and bias tee off.
3. Locally decoded vessels appear immediately on the dashboard, TV view, and Home Assistant entities.
4. Optionally apply to [join AISHub](https://www.aishub.net/join-us), then enter its assigned feed destination and contributor username for reciprocal network coverage.

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
| AIS Receiver Connection | Included AIS-catcher/RTL-SDR decoder, or external UDP, TCP, or serial NMEA receiver |
| AIS radio channel | Dual channel, 161.975 MHz channel A, or 162.025 MHz channel B |
| RTL-SDR device | Dongle index or serial number |
| Gain / PPM / AGC | Radio tuner and frequency-correction controls |
| Bias tee | Off by default for the NESDR SMArt v5; enable only for hardware that explicitly needs DC power |
| Decoder bandwidth | Recommended 192K AIS-catcher filter, 288K, or Off |
| Marine VHF receiver | Enables the second Nooelec as a receive-only NFM scanner |
| Marine device / channels | Separate dongle index or serial plus comma-separated frequencies and labels |
| Marine gain / PPM / squelch | Tuning controls for the second radio |
| USB GPS | Automatic estate reference location for map and distance ranking |
| Dashboard / TV live rain | Independent RainViewer precipitation overlays |
| FlightAware weather | Optional AeroAPI airport observation on Watch Area |
| Longitude/Latitude bounds | Geographic area returned by the AISHub API |
| Multi-Ship Tracking | Creates one Home Assistant entity per active vessel |
| MMSI Filter | Optional comma-separated list of nine-digit MMSIs |
| Ship Entity Timeout | Removes vessels that have not updated within this period |

The AISHub API does not allow polling more often than once per minute, so the app enforces a minimum 60-second interval.

## Nooelec NESDR SMArt v5

Plug the dongle into the Home Assistant machine, attach an AIS/VHF antenna, select **SDR**, and start with the defaults. The app builds and supervises AIS-catcher v0.70, decodes both international AIS channels, restarts the decoder if it exits, and shows decoder output in **Watch area → Receiver log**. Do not assign the same dongle to another add-on at the same time.

The bundled decoder is intended for observation only and is not approved as a navigation or safety-of-life system.

## Two-Nooelec AIS and marine VHF setup

Connect two separately assigned dongles and antennas. Keep Nooelec #1 on AIS device `0`, set Nooelec #2 as Marine VHF device `1`, and then enable **Marine VHF Receiver**. The app prevents both decoders from opening the same device. For a durable installation, program unique RTL-SDR serials and enter those serials instead of `0` and `1`, because USB indexes can change after a reboot.

The second receiver uses RTLSDR-Airband's NFM scanner and a private in-app audio stream. Open **Marine radio** in the AIS sidebar and press Play. Its startup, tuned channels, scanner output, failures, and restarts also appear in **Watch area → Receiver log**. The supplied channel list is only a starting profile; confirm and configure the frequencies authorized and used in your local waters.

This feature is receive-only. Do not use, transmit, record, or redistribute communications where prohibited, and never rely on the stream for navigation, distress response, or safety-of-life decisions.

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
- AIS-catcher startup, radio profile, decoder output, failures, and automatic restarts
- Marine VHF scanner and audio-server startup, profile, output, failures, and automatic restarts

No AISHub username, receiver payload, or geographic coordinates are sent to any Baiamonte telemetry service.

## Home Assistant entities

- `sensor.baiamonte_ais_connection_status`
- `sensor.baiamonte_ais_last_passing_ship`
- `sensor.baiamonte_ais_ship_<mmsi>` when Multi-Ship Tracking is enabled

The connection sensor includes AISHub status, API update time, receiver feed state, hardware message totals, shared-message totals, and last receiver activity.

## Updates

GitHub Actions publishes versioned `amd64` and `aarch64` images to `ghcr.io/drahamin/home-assistant-ais` whenever a release reaches `main`. Home Assistant compares the installed version with `ais_ship_tracker/config.yaml` and offers the new version automatically.

## Data source

Local SDR decoding is provided by [AIS-catcher](https://github.com/jvde-github/AIS-catcher). Receive-only marine audio uses [RTLSDR-Airband](https://github.com/rtl-airband/RTLSDR-Airband) and Icecast. Aggregated vessel data and contributor exchange are provided by [AISHub](https://www.aishub.net/). AIS and radio data are informational and must not be used as the sole source for navigation or safety decisions.
