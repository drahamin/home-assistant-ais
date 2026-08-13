# Baiamonte AIS setup

Baiamonte AIS includes AIS-catcher, so an attached RTL-SDR can receive, decode, display, and share AIS without a separate decoder add-on. AISHub is optional for local vessels and adds reciprocal community coverage when configured.

## Before starting

For local reception, attach an RTL-SDR and an AIS/VHF antenna. For wider reciprocal coverage, apply at [AISHub Join Us](https://www.aishub.net/join-us). AISHub will email the feed destination and, once your station meets its quality requirements, provide the username used by its data API.

## Connect the AIS hardware

Choose **SDR** for the included AIS-catcher decoder, or **UDP**, **TCP**, or **Serial** for an external receiver. UDP receivers normally send raw NMEA to the Home Assistant host on port `10110`; TCP mode connects to the configured receiver host and port; serial mode reads an attached radio at the selected device and baud rate. Raw single- and multipart AIS NMEA from these network modes is decoded locally into map vessels, so a private receiver such as Rahamin AIS Miami does not require working AISHub API credentials.

For the full Miami and Sicily map datasets, keep **AIS Network Data Source** set to **Rahamin Miami single-key proxy**. The default endpoint is `http://192.168.86.196:8999/api/status` on the private routed network. Baiamonte AIS requests the Pi's separate `?area=miami` and `?area=baiamonte` caches and imports each only into its matching map. The source area's scope and coverage are authoritative, and the displayed bounds expand to contain every accepted Rahamin target, so a difference between the installations' local bounds cannot hide vessels. The raw UDP stream remains useful as the direct Miami radio path, while the private status proxy supplies the broader cached vessel views. In this mode Home Assistant never polls AISHub directly, even if an older username remains stored; the Miami Pi is the sole reciprocal API client.

For a Nooelec NESDR SMArt v5, start with device `0`, tuner gain `auto`, correction `0` PPM, RTL AGC enabled, bias tee disabled, and decoder bandwidth `192K`. AIS-catcher listens to both AIS A at 161.975 MHz and AIS B at 162.025 MHz. With multiple RTL-SDRs, use an index, a unique selector such as `serial:AIS001`, or a stable physical selector such as `port:1-2.3`. The Watch Area status lists detected ports. Only one service can own a USB dongle at a time.

For two identical Nooelec receivers, leave AIS on device `0` and select device `1` or `auto` for **Marine VHF**. For a permanent installation, use different programmed serials or assign each role to its physical `port:` value so reconnecting or rebooting cannot swap jobs. If both dongles still have the same factory serial, do not select that serial: the app identifies it as ambiguous and asks for an index or port. The app refuses to start marine scanning if both roles resolve to one radio.

**Marine VHF USB recovery** is off by default. Enable **Allow Marine VHF USB Recovery** to expose the manual Recover button. The app first stops RTLSDR-Airband, resolves the configured marine device, and issues a Linux USB reset to only that device; AIS is not reset. **Automatically Reset Failed Marine VHF USB** can perform this after failures up to the configured limit (two recommended). If the Home Assistant host does not permit USB reset, the error is logged and ordinary process restarts continue.

The app recognizes `!AIVDM`, `!AIVDO`, `!BSVDM`, and `!ABVDM` sentences. Open the app log after starting it. You should see the friendly receiver name, its network address, valid NMEA counts, and forwarding totals.

## App settings

- **AIS Network Data Source:** keep **Rahamin Miami single-key proxy** for the shared Miami/Sicily installation. Choose Direct AISHub only if this app must operate without the Miami Pi.
- **AISHub Username:** used only in Direct AISHub fallback mode; leave it blank for the single-key proxy setup.
- **Share Receiver Data with AISHub:** off by default. Enable only after AISHub supplies the contributor feed destination.
- **AISHub Feed Host:** the raw NMEA contribution hostname or IP supplied by AISHub. Do not enter the AISHub API URL or a ShipXplorer address.
- **AISHub Feed Port:** your dedicated AISHub UDP port.
- **AIS Receiver Name:** the label shown in logs and Home Assistant status.
- **AIS Receiver Connection:** included AIS-catcher/RTL-SDR decoder, UDP listener, TCP client, or attached serial radio.
- **RTL-SDR Device / Gain / PPM / AGC:** selects and tunes the attached dongle.
- **RTL-SDR Bias Tee:** leave off for a NESDR SMArt v5 unless attached active hardware explicitly requires power.
- **AIS Decoder Bandwidth:** use the recommended `192K` starting filter, `288K`, or `OFF` for diagnosis.
- **Enable Marine VHF Receiver:** starts the bundled receive-only RTLSDR-Airband NFM scanner and private audio service.
- **Marine VHF RTL-SDR Device:** use `1`, `auto`, a unique serial, or a stable `port:` selector; it must resolve separately from AIS.
- **Marine VHF USB Recovery:** optionally allows manual recovery and up to 0–5 automatic device resets without touching the AIS radio.
- **Marine Frequencies / Labels:** matching comma-separated scan lists, with up to 12 frequencies between 156 and 163 MHz. Confirm the correct local channel plan.
- **Marine Gain / PPM / Squelch:** tuner settings for the second receiver. Start at gain `28`, PPM `0`, and automatic squelch. Use the manual dBFS threshold only for a channel that cannot work with adaptive squelch.
- **Use Attached USB GPS:** automatically uses a fresh NMEA fix for the estate position, map, and distance ranking.
- **Live Rain Radar on Dashboard / TV:** enable RainViewer independently for each surface.
- **FlightAware Airport Weather:** optional AeroAPI v4 observations, using an API key and ICAO airport code such as `LICC`.
- **Bounding Box:** the west, south, east, and north limits of the vessel watch area.
- **Multi-Ship Tracking:** creates a separate sensor for each vessel.
- **MMSI Filter:** optional comma-separated list of vessels to retain.
- **Ship Entity Timeout:** removes stale vessel entities.

Start the app and open **AIS** in the Home Assistant sidebar. A green local receiver state confirms AIS-catcher is running; received vessels appear without AISHub credentials. The reciprocal feed details separately confirm AISHub sharing and downloads.

Open **Marine radio** for the live audio player, current scanner state, device profile, and channel list. The receiver supplies one NFM scanner stream: it cycles through the configured channels and pauses on the first active transmission. Select **Start listening** to connect and **Stop listening** to disconnect. Audio also disconnects when you leave the page or hide the app. The status beside the buttons reports Connecting, Buffering, Listening, or a connection failure. Marine receiver activity is included in **Watch area → Receiver log**. The stream stays inside the add-on and is proxied through the same dashboard server, with no separate audio port or Icecast credentials exposed.

## TV map

For a television or kiosk display, open `http://HOME_ASSISTANT_IP:8999/tv` (or the shorter `/t` alias). With no query parameter, the display opens the Baiamonte Sicily map and shows every positioned live boat in the displayed coverage in its count and scrollable side list. Miami remains available only when deliberately selected on screen or opened with `/tv?area=miami`. Internal port `8099` remains dedicated to Home Assistant ingress.

Turn on **TV Live Weather Radar** to add current precipitation radar from RainViewer. Turn on **Live Rain Radar on Dashboard** for the Overview map. Adjust **TV Weather Opacity** between 10 and 100 if the radar is too faint or covers too much of the base map. Radar availability is best-effort; boats and the base map continue working if the weather service is temporarily unavailable.

Set **TV Map Target Size** from 30% to 180% to size vessel symbols on `/tv`; 100% is the normal-browser default. A display URL may override it with `?target_size=120` (combined with other options using `&target_size=120`) without changing every TV.

On Overview and TV pages, drag the map to move it, pinch or use the wheel and gold plus/minus buttons to zoom, and choose **Reset** to return to the automatic view. The Overview map also has height controls and a lower-right resize corner. Its height is remembered by the browser. The TV layout includes a flexbox fallback and same-origin tile proxy for Samsung/Tizen browsers.

The Overview map offers **Vessels**, **Labels**, and **Selected** controls. Vessels temporarily shows or hides all boat markers. Labels shows compact flag, MMSI, type, speed, destination, and last-seen callouts beside nearby vessels. Selected keeps the map clear until a vessel is tapped or clicked, then opens an expanded detail panel. When multiple vessels occupy the same few screen pixels, their icons spread into nearby open positions and a short line preserves each true reported location. **TV map** opens the Samsung-compatible fullscreen local map and vessel rail and uses the same overlap handling. The TV has its own Vessels control. The Home Assistant options **Show Vessels on Dashboard Map** and **Show Vessels on TV Map** set the startup behavior, while **TV Live Traffic Only** keeps the TV count and list limited to boats actually visible in the selected map area.

Vessel flags are derived from the MMSI Maritime Identification Digits when the transmitting identity contains an allocated MID. The same flag appears on overview map labels, recent contacts, Live traffic cards, and the TV vessel list. Special group, coast-station, SAR-aircraft, and AIS aid-to-navigation MMSI formats are recognized when possible. Unknown or malformed identities use a neutral flag rather than guessing a registry.

The **Watch Area** page includes the hardware receiver log, GPS/reference position, receiver profile, and optional FlightAware airport observation.

## Troubleshooting

- **AISHub setup required:** add the approved contributor username only if aggregated Sicily coverage is wanted. Local and private AIS inputs continue without it.
- **AISHub sharing not configured:** leave sharing disabled, or add the assigned raw NMEA host and UDP port before enabling it.
- **No hardware detected:** confirm the receiver is sending UDP NMEA to the Home Assistant host on port `10110` and that the port is reachable on your network.
- **AIS-catcher start failed / no supported devices:** confirm the RTL-SDR is attached to the Home Assistant host, no other add-on owns it, and the receiver mode is SDR.
- **Decoder running but no boats:** use an antenna designed for approximately 162 MHz, move it higher or outdoors, and confirm vessels are within VHF range.
- **Marine VHF device conflict:** select the second Nooelec or its unique serial; AIS and marine radio cannot share one dongle.
- **Marine audio is a solid tone or constant noise:** install 2.7.17 or newer, which explicitly selects NFM and defaults to adaptive squelch. Confirm automatic squelch is enabled before changing gain or the manual threshold.
- **Marine audio unavailable:** confirm Marine VHF is enabled, the second dongle is attached and not used elsewhere, and review the receiver log. Adjust squelch only after confirming the configured channels and antenna.
- **Sharing error:** recheck the contributor feed host and port supplied by AISHub. Receiving and decoding remain active while sharing is unavailable.
- **AISHub credentials rejected:** confirm the approved username. The app waits 15 minutes before another API request; restart it after correcting the username to retry immediately.
- **Zero vessels:** verify the configured geographic bounds and maximum position age.

AISHub limits API access to one request per minute. Baiamonte AIS enforces that limit automatically.

Marine VHF is receive-only and informational. Follow local laws concerning reception, recording, and redistribution, and never treat dashboard audio as a distress-monitoring or safety-of-life service.
