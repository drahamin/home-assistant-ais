# Changelog

## 0.2.2

- Moved Home Assistant entity writes off the CAN receive loop so API delays cannot interrupt serial reception.
- Coalesced pending and unchanged entity states to one newest value per entity.
- Added exponential retry backoff and rate-limited error logging when Home Assistant is busy.
- Reduced connection-status writes from every 2 seconds to every 10 seconds while keeping the local dashboard live.
- Paused rapid dashboard polling in hidden tabs and avoided rebuilding unchanged data panels.
- Added tests for non-blocking publication, state coalescing, and background delivery.

## 0.2.1

- Added a CANable V2.0 Pro device-light panel to the Overview.
- Mirrors red PWR and blue STATE when the adapter is connected.
- Pulses green WORK while valid CAN frames are arriving.
- Clearly labels the display as a software inference rather than direct LED telemetry.

## 0.2.0

- Added a Baiamonte-branded Home Assistant ingress dashboard.
- Added responsive desktop, tablet, and touch-friendly mobile layouts.
- Added a purpose-built Baiamonte battery-and-CAN emblem plus favicon, Apple touch, and installable web-app sizes.
- Added live adapter, CAN bus, battery, alarm, and protection status.
- Added a dedicated CAN Traffic view with frame rate, active identifiers, raw payloads, decoded fields, and recent activity.
- Added guided troubleshooting and verified wiring references.
- Added a Supervisor watchdog health endpoint.
- Preserved hardware listen-only mode and omitted all CAN transmit controls.
