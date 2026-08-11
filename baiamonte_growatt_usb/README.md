# Baiamonte Growatt USB

Baiamonte Growatt USB replaces a manual Home Assistant USB setup for a Growatt SPF inverter with one managed Home Assistant app. It automatically discovers the local inverter connection, publishes Home Assistant entities, reads the inverter configuration, and provides a guarded commissioning editor plus branded troubleshooting dashboard through ingress.

The first release is designed for the installed Growatt SPF 5000 ES and common PI30-family USB interfaces. It supports serial paths such as `/dev/ttyUSB0` and `/dev/serial/by-id/...`, plus HID paths exposed to the app by the Home Assistant host.

## Features

- Automatic serial/HID transport and PI protocol discovery
- Explicit read-only safety lock enabled by default
- Guarded, whitelisted commissioning changes with confirmation and read-back
- Firmware maintenance with CPU version inquiry, settings backup, and SHA-256/model validation of official packages
- Restricted direct fetching from Growatt-hosted HTTPS URLs, with off-domain redirects blocked
- A hard safety boundary that stages firmware locally but never sends undocumented flashing commands
- Local operation with no Growatt cloud credential
- Live PV, load, grid, battery, mode, and temperature readings
- Estimated local PV energy for the current day
- Stable `sensor.baiamonte_growatt_*` Home Assistant entities
- Guided USB, protocol, cable, contention, and stale-data diagnostics
- Home Assistant ingress dashboard in the Baiamonte visual system
- Multi-architecture image publishing and Home Assistant automatic updates

See [DOCS.md](DOCS.md) for migration, configuration, entities, and the full troubleshooting reference.
