# Changelog

## 1.3.6

- Adds exponential retry backoff up to five minutes when the inverter is unavailable, while Refresh and Rescan still retry immediately.
- Publishes changed Home Assistant sensor states at a configurable 30-second minimum and unchanged states only as a five-minute heartbeat.
- Buffers the daily-energy state and writes it once per minute instead of on every inverter poll.
- Reduces periodic settings reads to every 15 minutes and firmware-version reads to every six hours; both remain available immediately from the dashboard.
- Prevents unsupported optional warning, settings, and firmware inquiries from being retried and logged on every live poll.
- Caches unchanged app options and short-lived USB discovery results to reduce filesystem work.
- Refreshes the dashboard every five seconds while visible, every 30 seconds while hidden, and temporarily every second during an address scan.

## 1.3.5

- Makes the dashboard substantially more compact on phones with a two-column live-power layout, condensed connection details, and reduced spacing.
- Keeps Refresh, Rescan, live readings, connection health, and the RS485 address scan immediately visible.
- Collapses settings, firmware, troubleshooting, and event-history sections on small screens to reduce scrolling while keeping every tool one tap away.
- Adds automatic system dark mode plus a remembered light/dark toggle in the header.

## 1.3.4

- Adds a read-only RS485 sweep across documented Modbus addresses 1 through 247.
- Shows live scan progress and automatically prioritizes any responding address.

## 1.3.3

- Adds a native Growatt Modbus RTU function-04 reader that bypasses pymodbus when its serial-port open fails.
- Accepts locally echoed Modbus requests and validates the inverter response with a wire-order CRC before decoding telemetry.

## 1.3.2

- Keeps reading after USB-to-RS485 request echoes and leading serial noise instead of discarding the inverter reply at the first carriage return.
- Extracts the first complete Growatt PI response from a fragmented receive buffer and includes a short hexadecimal preview when non-Growatt bytes are received.

## 1.3.1

- Deduplicates stable and numbered paths that point to the same physical USB adapter.
- Adds a native read-only QPIGS serial fallback that does not depend on mpp-solar response parsing.
- Recovery discovery safely tests the common SPF direct-USB speeds and Modbus 9600/19200 baud at addresses 1 and 2.
- Retries the Growatt Modbus input map in smaller blocks for inverter revisions that reject a single long request.
- Reports the active recovered baud rate and preserves the useful Modbus failures in connection diagnostics.

## 1.3.0

- Adds native Growatt Off-Grid Modbus RTU v0.14 support for the SPF 5000 ES RJ45 RS485-to-USB cable.
- Automatically tries address 1 at Growatt's documented 9600-baud RS485 speed before direct-USB PI protocols.
- Decodes PV, grid, load, battery, temperature, mode, fault, warning, and inverter energy registers.
- Reads the Modbus inverter configuration, communication version/address, firmware versions, and serial number.
- Adds RS485-specific status, configuration choices, and troubleshooting while retaining direct USB compatibility.
- Keeps Modbus writes safety-locked until the installed inverter revision and read-back behavior are verified on hardware.

## 1.2.3

- Exclude u-blox GPS and CANable interfaces from Growatt automatic discovery, including their generic `ttyACM` aliases.
- Prefer stable CP210x, FTDI, Exar, and CH34x USB-serial paths when the Growatt interface is present.

## 1.2.2

- Discover SPF firmware that supports live `QPIGS` telemetry but does not answer the optional `QPI` protocol identity inquiry.

## 1.2.1

- Rejects mpp-solar empty-response and NAK diagnostics instead of reporting a false Online state.
- Decodes the list-style value/unit JSON emitted by mpp-solar so live measurements populate correctly.

## 1.2.0

- Adds installed CPU firmware version inquiries and periodic version refresh.
- Adds a downloadable pre-update backup of inverter identity, firmware versions, and current settings.
- Adds local validation and staging for official Growatt `.bin`, `.hex`, `.fw`, and `.zip` packages with an exact model confirmation, mandatory SHA-256 match, file-size limits, and persistent audit metadata.
- Adds guarded server-side fetching from an operator-supplied Growatt HTTPS URL; non-Growatt sources and off-domain redirects are refused, and the same model/checksum checks remain mandatory.
- Keeps direct firmware flashing unavailable until a documented Growatt SPF 5000 ES update driver, signed-package specification, and recovery procedure can be safely implemented.
- Adds a touch-friendly firmware preflight and handoff panel to the Baiamonte dashboard.

## 1.1.0

- Adds explicit serial and USB HID transport selection, including correct serial handling for ttyACM and by-id paths.
- Adds all PI protocol profiles supported by the bundled inverter library, with an SPF-focused automatic discovery order.
- Reads and displays the current inverter configuration.
- Adds a guarded settings editor for approved PI30-family priorities, voltage thresholds, charging limits, operating flags, and display/alarm preferences.
- Requires two configuration gates, per-change APPLY confirmation, command validation, event logging, and post-change read-back.
- Makes buttons, forms, status rows, and commissioning controls touch-friendly on phones, tablets, and wall panels.

## 1.0.0

- Replaces the manual Growatt SPF USB reader with a managed Home Assistant app.
- Adds automatic serial/HID device discovery and PI30-family protocol probing.
- Publishes local Growatt power, voltage, frequency, load, battery, temperature, mode, connectivity, and daily energy entities.
- Adds a Baiamonte-branded ingress dashboard with live power flow, connection status, event history, USB rescan, and guided troubleshooting.
- Enforces read-only operation and uses inquiry commands only.
- Adds multi-architecture GitHub image publishing for Home Assistant automatic updates.
