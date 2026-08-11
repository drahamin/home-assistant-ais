# Changelog

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
