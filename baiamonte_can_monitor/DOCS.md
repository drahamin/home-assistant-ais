# Baiamonte CAN Monitor

The app passively monitors the 500 kbit/s CAN link between the Growatt inverter and Felicity battery master. Open **CAN** from the Home Assistant sidebar for live status, decoded battery values, raw traffic, and troubleshooting.

## Safety

The CAN adapter is opened in firmware listen-only mode. The app does not expose any transmit endpoint or control.

Keep the CANable 120Ω termination switch off when tapping the existing, already terminated inverter-to-battery bus.

## Automatic updates

Home Assistant Supervisor owns installation and updates. After installing version 0.2.0, enable **Auto update** on the app's Info page. Future versions published through the Baiamonte app repository will then be installed by Supervisor automatically. The app never replaces its own files or bypasses Supervisor.

## No CAN traffic

If the adapter connects but the traffic count remains at zero:

1. Confirm the inverter and battery master are powered and communicating.
2. Keep the CANable termination switch off.
3. Verify CAN-H and CAN-L continuity to the correct RJ45 pins.
4. Confirm the configured bit rate is 500000 bit/s.
5. If the cable mapping is uncertain, power down the equipment and swap H/L only at the CANable, then retry.
