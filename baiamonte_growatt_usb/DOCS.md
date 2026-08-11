# Baiamonte Growatt USB setup

## What this replaces

This app owns the local connection between Home Assistant and the Growatt SPF inverter. It replaces the manually installed Growatt reader or service. It supports the Growatt RJ45 RS485-to-USB cable shown in commissioning, plus direct USB interfaces. It does **not** replace Baiamonte CAN Monitor: the CAN app reads the separate Growatt-to-Felicity battery communication link through its own CANable adapter.

Only one process can use the Growatt USB device at a time. Stop the manual reader before starting this app.

## Safe migration

1. Install **Baiamonte Growatt USB** from the Baiamonte Home Assistant Apps repository.
2. Leave **Growatt USB device**, **Connection transport**, and **Inverter protocol** set to `auto`. Keep RS485 at 9600 baud/address 1 and keep **Read-only safety lock** enabled.
3. Stop the old manual Growatt USB process, container, shell command, or integration. Do not remove its dashboard configuration yet.
4. Connect the inverter USB cable and start this app.
5. Open **Growatt USB** in the Home Assistant sidebar. Wait for **Online** and verify PV power, output power, battery voltage, and inverter mode against the Growatt display.
6. Update dashboards and automations to use the new `sensor.baiamonte_growatt_*` entities.
7. After a full day of stable readings, remove the retired manual setup. Retain a backup until the replacement is proven.
8. On the app Info page, enable **Start on boot**, **Watchdog**, **Show in sidebar**, and **Automatic updates**.

## Recommended configuration

```yaml
device: auto
protocol: auto
transport: auto
baud_rate: 2400
modbus_baud_rate: 9600
modbus_slave_id: 1
poll_interval_seconds: 10
stale_after_seconds: 45
publish_to_home_assistant: true
read_only: true
allow_setting_changes: false
firmware_tools_enabled: true
firmware_max_package_mb: 32
```

Use a stable `/dev/serial/by-id/...` path after commissioning if several serial USB adapters are connected. A by-id path is safer than `/dev/ttyUSB0` because numeric device names may change after a reboot.

## RS485, USB transports, and inverter protocols

The app automatically treats `/dev/ttyUSB*`, `/dev/ttyACM*`, and `/dev/serial/by-id/*` as serial connections and `/dev/hidraw*` as USB HID. On serial adapters it first tries Growatt Off-Grid Modbus RTU v0.14 at 9600 baud/address 1. This is the correct mode for the SPF 5000 ES RJ45 RS485-to-USB cable. It then tries the direct-USB PI profiles when Auto is selected.

Auto discovery supports Modbus v0.14 plus the Growatt SPF-compatible PI30 family: PI30, PI30MAX, PI30M044, PI30M045, PI30REVO, and PI41. Use a manual protocol only when Auto cannot decode the inverter correctly.

## Reading and changing settings

The dashboard reads the current inverter profile periodically and on demand. Modbus configuration read-back includes priorities, input/output mode, battery profile, voltage thresholds, charge-current limits, communication address, and protocol version. Modbus register writes remain locked until the exact installed inverter revision is verified on hardware. Direct-USB PI setting changes are disabled by default and use the guarded process below.

1. Record the current Growatt display and dashboard values.
2. In the app Configuration page, set `read_only: false` and `allow_setting_changes: true`, then restart the app.
3. Open **Configuration & settings** in the sidebar dashboard.
4. Change one whitelisted setting, review its impact warning, and enter `APPLY`.
5. Confirm the automatic read-back and check the Growatt front display.
6. When commissioning is complete, restore `read_only: true` and `allow_setting_changes: false`.

The editor supports output and charger priorities, input range, standard battery profile, output frequency, recharge/redischarge, bulk, float and cutoff voltages, total and utility charge-current limits, PV balance/parallel condition, buzzer, power saving, automatic restart flags, backlight, source alarm, and fault recording where the active firmware accepts the corresponding command.

Factory reset, calibration, date/time adjustment, arbitrary raw commands, and undocumented battery types are intentionally excluded. Battery voltage and current changes can damage equipment when incorrectly chosen; use only the approved Felicity/Growatt values.

## Firmware maintenance

The dashboard can read the installed CPU firmware versions, export a dated JSON backup of the inverter identity and current settings, and validate an official Growatt update package before it is handed to an approved updater.

1. Keep the inverter USB connection online and select **Read versions**.
2. Select **Download settings backup** and retain the file away from the Home Assistant host.
3. Obtain the package and its SHA-256 directly from Growatt or the authorized installer for the exact **SPF 5000 ES** hardware revision.
4. Select the package, enter its independently verified 64-character SHA-256, type `SPF 5000 ES`, and select **Validate and stage package**.
5. Confirm the dashboard reports the expected filename, size, model, and checksum.
6. Perform the actual update only with Growatt's approved updater, cable/driver, power conditions, and recovery procedure.

Staging stores a hash-named copy and audit metadata under the app's persistent data directory. It never executes the file and never transmits it to the inverter. Direct flashing is intentionally unavailable because the app does not have a published SPF 5000 ES USB bootloader protocol, signed-package specification, or model-specific recovery driver. Do not rename firmware from another Growatt model or bypass the checksum/model checks.

If Growatt or an authorized installer supplies a direct official download URL, the dashboard can fetch it without routing the file through another computer. The fetcher accepts only HTTPS URLs on `growatt.com` or one of its subdomains, rejects redirects outside that domain, enforces the package-size limit, and still requires the exact model confirmation and independent SHA-256 match. The official public download center is linked in the dashboard; as of this release, its SPF 3500/5000 ES materials include manuals and datasheets but no publicly listed inverter firmware package. Do not use forum, file-sharing, or reseller mirrors.

If a future official driver is added, flashing must remain a separate, explicit operation with another device/model check, AC/PV/battery preflight, configuration backup, update progress, version read-back, and a documented recovery path.

## Home Assistant entities

The app publishes the readings supported by the connected firmware. The normal entity set is:

- `binary_sensor.baiamonte_growatt_connected`
- `sensor.baiamonte_growatt_mode`
- `sensor.baiamonte_growatt_pv_input_power`
- `sensor.baiamonte_growatt_pv_input_voltage`
- `sensor.baiamonte_growatt_pv_input_current_for_battery`
- `sensor.baiamonte_growatt_ac_input_voltage`
- `sensor.baiamonte_growatt_ac_input_frequency`
- `sensor.baiamonte_growatt_ac_output_voltage`
- `sensor.baiamonte_growatt_ac_output_frequency`
- `sensor.baiamonte_growatt_ac_output_active_power`
- `sensor.baiamonte_growatt_ac_output_apparent_power`
- `sensor.baiamonte_growatt_ac_output_load`
- `sensor.baiamonte_growatt_battery_voltage`
- `sensor.baiamonte_growatt_battery_capacity`
- `sensor.baiamonte_growatt_battery_charging_current`
- `sensor.baiamonte_growatt_battery_discharge_current`
- `sensor.baiamonte_growatt_inverter_heat_sink_temperature`
- `sensor.baiamonte_growatt_energy_today`

The daily energy value is a local estimate integrated from the inverter's live PV power. Use the Growatt lifetime/certified meter reading for accounting where exact revenue-grade totals are required.

## Troubleshooting

### No RS485/USB adapter visible

- Confirm the Growatt is powered.
- Reconnect the Growatt RJ45 RS485 plug and the USB plug.
- Connect directly to the Home Assistant host while commissioning; temporarily remove unpowered hubs.
- Reconnect both ends and press **Rescan connection**.
- Confirm the app configuration still grants USB, UART, and udev access.
- Try a different host port and a short, shielded cable.

### Adapter visible, but the inverter does not reply

- Stop the old manual Growatt service. Two processes cannot share the device reliably.
- Stop any other inverter app that may open the same `/dev/ttyUSB*` or `/dev/hidraw*` path.
- For the RJ45 RS485 cable, use Modbus v0.14, address 1, and 9600 baud.
- Confirm the RJ45 plug is in the inverter **RS485** port, not its BMS/CAN port.
- Check the Growatt display to confirm the inverter is operating.
- Select the exact stable device path if multiple serial adapters are present.
- If using a direct USB interface instead, try `PI30`, then `PI30MAX`, then `PI41` explicitly.

### Values update and then stop

- Replace a long, damaged, or unshielded USB cable.
- Avoid routing USB beside inverter AC output or PV DC wiring.
- Use a powered hub if a hub is unavoidable.
- Increase the update interval from 10 to 15 or 20 seconds if the interface times out under frequent polling.
- Review the dashboard event log for repeated timeouts or device renumbering.
- Select the by-id device path so a restart cannot move the inverter from `ttyUSB0` to `ttyUSB1`.

### Some readings are unavailable

Growatt firmware variants expose different fields. The app only creates a live measurement when the inverter returns it. Battery cell values, BMS limits, and individual pack data come from the separate Baiamonte CAN Monitor and its CANable adapter, not from the direct inverter USB protocol.

### Readings look wrong

- Compare output voltage, battery voltage, load percentage, and mode with the Growatt front panel.
- Confirm the selected protocol. A partially compatible protocol can decode an unexpected response incorrectly.
- Return to Auto and rescan.
- Confirm no RS232/USB converter is changing the expected electrical interface or baud rate.
- Do not compensate for incorrect values in dashboard templates until the physical connection and protocol are verified.

### App will not start

- **Read-only safety lock** must be enabled. The app intentionally refuses inverter access when it is disabled.
- Review the app log for configuration parsing or image startup errors.
- Update the app to the latest available version and restart it.
- If the app image cannot be installed, refresh the Baiamonte repository and verify the Home Assistant host can reach GitHub Container Registry.

### Firmware package will not stage

- Confirm **Enable firmware maintenance tools** is on in the app Configuration page.
- Use an unmodified `.bin`, `.hex`, `.fw`, or `.zip` obtained for the exact SPF 5000 ES hardware revision.
- Copy the complete 64-character SHA-256 without spaces; a mismatch means the file is different and must not be used.
- Enter the target model exactly as `SPF 5000 ES`.
- Confirm the package is below the configured size limit and is not an empty or truncated download.
- A successfully staged package is not an authorization to flash it. Use only Growatt's approved update and recovery procedure.
- For direct fetching, use the final HTTPS link supplied by Growatt. Links outside the official `growatt.com` domain and redirects to third-party storage are deliberately blocked.

### After a Home Assistant reboot

- Enable **Start on boot** and **Watchdog**.
- Prefer `/dev/serial/by-id/...` over a numeric serial path.
- Allow up to one minute for the host to enumerate USB hardware.
- Press **Rescan USB** if the inverter was powered after Home Assistant.

## Safety and privacy

Normal operation uses inquiry commands only. Write commands are available only when both safety options are deliberately changed, the USB connection is online, the active protocol is an approved PI30-family profile, the requested setting passes its whitelist/range validation, and the operator enters `APPLY`. Every accepted change is logged and followed by an inverter settings read-back. Arbitrary raw commands, factory reset, and calibration remain unavailable.

Data stays on the Home Assistant host and is sent only to Home Assistant's internal API.

Disconnect power and follow Growatt installation guidance before changing electrical wiring. USB troubleshooting does not require opening the inverter or touching PV, battery, or AC terminals.
