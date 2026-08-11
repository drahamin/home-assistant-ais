# Baiamonte Growatt USB

Baiamonte Growatt USB replaces a manual Home Assistant connection for a Growatt SPF inverter with one managed Home Assistant app. It automatically discovers the local inverter connection, publishes Home Assistant entities, reads the inverter configuration, and provides a guarded commissioning editor plus branded troubleshooting dashboard through ingress.

It is designed for the installed Growatt SPF 5000 ES. It natively supports the Growatt RJ45 RS485-to-USB cable using Off-Grid Modbus RTU v0.14 at 9600 baud/address 1, as well as common PI30-family direct USB interfaces.

## Features

- Automatic RS485 Modbus RTU, serial, HID, and PI protocol discovery
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
