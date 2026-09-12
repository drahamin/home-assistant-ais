# Changelog

## 0.5.5

- Keep the adaptive bank time-to-empty and time-to-full forecasts authoritative across every RS485 frame.
- Prevent instantaneous per-frame values from hiding the learned runtime estimate while the generator is charging.

## 0.5.4

- Made time-to-empty adaptive using a smoothed, learned discharge load.
- Keeps a conservative runtime forecast visible while charging until real discharge history is available.
- Added the learned charge/discharge basis as native Home Assistant statistics and explains the forecast basis.

## 0.5.3

- Added bank and per-battery charging/discharging power sensors for clear input and output statistics.
- Added time-to-empty and time-to-full estimates with duration metadata for gauges and history.
- Added a live operating recommendation based on lowest pack SOC, pack imbalance, cell spread, connectivity, and charge direction.
- Enabled long-term measurement statistics for remaining-energy sensors.

## 0.5.2

- Refresh unchanged entities once per minute so every battery card returns automatically after a Home Assistant restart.
- Retain coalescing and five-second publishing to keep CPU, network and recorder use low.

## 0.5.1

- Added persistent cumulative bank charge and discharge energy sensors for the Home Assistant Energy dashboard.
- Reduced the default Home Assistant publishing rate to once every five seconds while retaining two-second battery polling.

## 0.5.0

- Added equal first-class monitoring for Felicity Battery 1 and Battery 2.
- Added a combined 200 Ah / 10.24 kWh bank with SOC, voltage, summed current/power, remaining energy, and online-pack status.
- Added per-pack remaining capacity/energy, pack temperature, cell extrema, cell numbers, and automatic cell-balance assessment.
- Added clear attention status when pack SOC differs by more than 10% or any cell spread exceeds 50 mV.
- Updated the overview, device-light mirror, diagnostics, and wiring page for the installed IOCREST FTDI RS485 adapter.
- Reduced steady RS485 traffic by reading firmware only at startup and pacing dynamic polls while retaining live updates.

## 0.4.0

- Added read-only Felicity LPBA Modbus RTU polling over USB RS485 at 9600 8N1.
- Added battery firmware, pack voltage/current/power/SOC, 16 cell voltages, and four temperature readings.
- Added support for multiple battery addresses and CRC/range validation before publishing values.
- Prevented CAN auto-discovery from claiming unrelated serial devices such as the estate GPS receiver.

## 0.3.1

- Added an opt-in, fixed Growatt V1.04 `0x301` heartbeat for batteries that wait for an inverter request before publishing status.
- The heartbeat is the only implemented outbound frame; battery-control and arbitrary transmit paths remain unavailable.

## 0.3.0

- Added a guarded `standalone_ack` mode for reading a battery when the inverter communication interface is unavailable.
- The CAN controller can acknowledge received frames in standalone mode, while the application still contains no data-frame transmit or battery-control path.
- Updated diagnostics for standalone termination and power checks.

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
