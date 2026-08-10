# Baiamonte AIS setup

Baiamonte AIS includes AIS-catcher, so an attached RTL-SDR can receive, decode, display, and share AIS without a separate decoder add-on. AISHub is optional for local vessels and adds reciprocal community coverage when configured.

## Before starting

For local reception, attach an RTL-SDR and an AIS/VHF antenna. For wider reciprocal coverage, apply at [AISHub Join Us](https://www.aishub.net/join-us). AISHub will email the feed destination and, once your station meets its quality requirements, provide the username used by its data API.

## Connect the AIS hardware

Choose **SDR** for the included AIS-catcher decoder, or **UDP**, **TCP**, or **Serial** for an external receiver. UDP receivers normally send raw NMEA to the Home Assistant host on port `10110`; TCP mode connects to the configured receiver host and port; serial mode reads an attached radio at the selected device and baud rate. Raw single- and multipart AIS NMEA from these network modes is decoded locally into map vessels, so a private receiver such as Rahamin AIS Miami does not require working AISHub API credentials.

For the full Miami map and inbound dataset, enable **Rahamin AIS Private Proxy**. The default endpoint is `http://192.168.86.196:8999/api/status` on the private routed network. This imports only vessels inside the configured Miami area and approach ring. The raw UDP stream remains useful as the direct radio path, while the private status proxy supplies the broader vessel view shown on the Rahamin AIS dashboard.

For a Nooelec NESDR SMArt v5, start with device `0`, tuner gain `auto`, correction `0` PPM, RTL AGC enabled, bias tee disabled, and decoder bandwidth `192K`. AIS-catcher listens to both AIS A at 161.975 MHz and AIS B at 162.025 MHz. If several RTL-SDR devices are attached, use the dongle serial number instead of index `0`. Only one app can own a USB dongle at a time.

For two Nooelec receivers, leave AIS on device `0` and select device `1` for **Marine VHF**. Prefer unique dongle serial numbers for a permanent installation so a USB reorder cannot swap the receivers. The app refuses to start marine scanning if its device matches the AIS device.

The app recognizes `!AIVDM`, `!AIVDO`, `!BSVDM`, and `!ABVDM` sentences. Open the app log after starting it. You should see the friendly receiver name, its network address, valid NMEA counts, and forwarding totals.

## App settings

- **AISHub Username:** the contributor username supplied by AISHub.
- **AISHub Feed Host:** the destination hostname or IP supplied by AISHub.
- **AISHub Feed Port:** your dedicated AISHub UDP port.
- **AIS Receiver Name:** the label shown in logs and Home Assistant status.
- **AIS Receiver Connection:** included AIS-catcher/RTL-SDR decoder, UDP listener, TCP client, or attached serial radio.
- **RTL-SDR Device / Gain / PPM / AGC:** selects and tunes the attached dongle.
- **RTL-SDR Bias Tee:** leave off for a NESDR SMArt v5 unless attached active hardware explicitly requires power.
- **AIS Decoder Bandwidth:** use the recommended `192K` starting filter, `288K`, or `OFF` for diagnosis.
- **Enable Marine VHF Receiver:** starts the bundled receive-only RTLSDR-Airband NFM scanner and private audio service.
- **Marine VHF RTL-SDR Device:** use `1` for the second Nooelec or its unique serial; it must not match the AIS device.
- **Marine Frequencies / Labels:** matching comma-separated scan lists, with up to 12 frequencies between 156 and 163 MHz. Confirm the correct local channel plan.
- **Marine Gain / PPM / Squelch:** tuner settings for the second receiver. Start at gain `28`, PPM `0`, and squelch `-28`, then adjust for the antenna and local noise floor.
- **Use Attached USB GPS:** automatically uses a fresh NMEA fix for the estate position, map, and distance ranking.
- **Live Rain Radar on Dashboard / TV:** enable RainViewer independently for each surface.
- **FlightAware Airport Weather:** optional AeroAPI v4 observations, using an API key and ICAO airport code such as `LICC`.
- **Bounding Box:** the west, south, east, and north limits of the vessel watch area.
- **Multi-Ship Tracking:** creates a separate sensor for each vessel.
- **MMSI Filter:** optional comma-separated list of vessels to retain.
- **Ship Entity Timeout:** removes stale vessel entities.

Start the app and open **AIS** in the Home Assistant sidebar. A green local receiver state confirms AIS-catcher is running; received vessels appear without AISHub credentials. The reciprocal feed details separately confirm AISHub sharing and downloads.

Open **Marine radio** for the live audio player, current scanner state, device profile, and channel list. The browser will not autoplay radio audio; press Play. Marine receiver activity is included in **Watch area → Receiver log**. The stream stays inside the add-on and is proxied through the same dashboard server, with no separate audio port or Icecast credentials exposed.

## TV map

For a television or kiosk display, open `http://HOME_ASSISTANT_IP:8999/tv`. With no query parameter, the display opens the Baiamonte Sicily map and shows only live boats inside the visible watch area in its count and compact side list. Miami remains available only when deliberately selected on screen or opened with `/tv?area=miami`. Internal port `8099` remains dedicated to Home Assistant ingress.

Turn on **TV Live Weather Radar** to add current precipitation radar from RainViewer. Turn on **Live Rain Radar on Dashboard** for the Overview map. Adjust **TV Weather Opacity** between 10 and 100 if the radar is too faint or covers too much of the base map. Radar availability is best-effort; boats and the base map continue working if the weather service is temporarily unavailable.

On Overview and TV pages, drag the map to move it, pinch or use the wheel and gold plus/minus buttons to zoom, and choose **Reset** to return to the automatic view. The Overview map also has height controls and a lower-right resize corner. Its height is remembered by the browser. The TV layout includes a flexbox fallback and same-origin tile proxy for Samsung/Tizen browsers.

The Overview map offers **Vessels**, **Labels**, and **Selected** controls. Vessels temporarily shows or hides all boat markers. Labels shows compact flag, MMSI, type, speed, destination, and last-seen callouts beside nearby vessels. Selected keeps the map clear until a vessel is tapped or clicked, then opens an expanded detail panel. **TV map** opens the Samsung-compatible fullscreen local map and vessel rail. The TV has its own Vessels control. The Home Assistant options **Show Vessels on Dashboard Map** and **Show Vessels on TV Map** set the startup behavior, while **TV Live Traffic Only** keeps the TV count and list limited to boats actually visible in the selected map area.

Vessel flags are derived from the MMSI Maritime Identification Digits when the transmitting identity contains an allocated MID. The same flag appears on overview map labels, recent contacts, Live traffic cards, and the TV vessel list. Special group, coast-station, SAR-aircraft, and AIS aid-to-navigation MMSI formats are recognized when possible. Unknown or malformed identities use a neutral flag rather than guessing a registry.

The **Watch Area** page includes the hardware receiver log, GPS/reference position, receiver profile, and optional FlightAware airport observation.

## Troubleshooting

- **Setup required:** add the AISHub username.
- **Receiver ready · AISHub destination needed:** add the feed host and assigned UDP port.
- **No hardware detected:** confirm the receiver is sending UDP NMEA to the Home Assistant host on port `10110` and that the port is reachable on your network.
- **AIS-catcher start failed / no supported devices:** confirm the RTL-SDR is attached to the Home Assistant host, no other add-on owns it, and the receiver mode is SDR.
- **Decoder running but no boats:** use an antenna designed for approximately 162 MHz, move it higher or outdoors, and confirm vessels are within VHF range.
- **Marine VHF device conflict:** select the second Nooelec or its unique serial; AIS and marine radio cannot share one dongle.
- **Marine audio unavailable:** confirm Marine VHF is enabled, the second dongle is attached and not used elsewhere, and review the receiver log. Adjust squelch only after confirming the configured channels and antenna.
- **Sharing error:** recheck the AISHub host and port supplied by email.
- **AISHub connection error:** confirm the username is active and wait at least one minute between retries.
- **Zero vessels:** verify the configured geographic bounds and maximum position age.

AISHub limits API access to one request per minute. Baiamonte AIS enforces that limit automatically.

Marine VHF is receive-only and informational. Follow local laws concerning reception, recording, and redistribution, and never treat dashboard audio as a distress-monitoring or safety-of-life service.
