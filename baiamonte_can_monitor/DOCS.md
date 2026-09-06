# Baiamonte CAN Monitor

The app passively monitors the 500 kbit/s CAN link between the Growatt inverter and Felicity battery master. Open **CAN** from the Home Assistant sidebar for live status, decoded battery values, raw traffic, and troubleshooting.

## Safety

The default `listen_only` mode opens the adapter in firmware listen-only mode for tapping an active Growatt-to-battery bus. When the Growatt communication interface is unavailable, `standalone_ack` lets the CAN controller acknowledge battery frames while the application remains receive-only. The app exposes no transmit endpoint or battery control.

Keep the CANable 120Ω termination switch off when tapping the existing, already terminated inverter-to-battery bus.

For a direct battery-to-CANable connection with no inverter on the bus, select `standalone_ack` and use the CANable as the terminated endpoint. This mode emits only protocol-level CAN acknowledgements; it does not send data frames or commands.

If the battery waits for the inverter before publishing status, enable `growatt_heartbeat`. The app then transmits the Growatt low-voltage CAN V1.04 heartbeat—standard ID `0x301`, payload `11 22 33 44 55 66 77 88`—once per second. No other outbound CAN identifier or payload is implemented.

## Automatic updates

Home Assistant Supervisor owns installation and updates. After installing version 0.2.0, enable **Auto update** on the app's Info page. Future versions published through the Baiamonte app repository will then be installed by Supervisor automatically. The app never replaces its own files or bypasses Supervisor.

## No CAN traffic

If the adapter connects but the traffic count remains at zero:

1. Confirm the inverter and battery master are powered and communicating.
2. Keep the CANable termination switch off.
3. Verify CAN-H and CAN-L continuity to the correct RJ45 pins.
4. Confirm the configured bit rate is 500000 bit/s.
5. If the cable mapping is uncertain, power down the equipment and swap H/L only at the CANable, then retry.
