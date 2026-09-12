# Baiamonte Battery Monitor

The app reads both installed Felicity LPBA48100-OL batteries independently over the dedicated USB RS485 adapter and combines them into the Baiamonte 200 Ah / 10.24 kWh battery bank. Open **Battery** from the Home Assistant sidebar for pack status, bank energy, all 32 cell voltages, temperatures, raw replies, and troubleshooting.

## Installed configuration

- Connection: `felicity_rs485`
- Stable USB device: `/dev/serial/by-id/usb-FTDI_FT231X_USB_UART_DU0E5D36-if00-port0`
- Serial format: 9600 baud, 8 data bits, no parity, 1 stop bit
- Battery addresses: `1,2`
- Battery 1 and Battery 2: 51.2 V, 100 Ah, 5.12 kWh each
- Combined bank: 51.2 V, 200 Ah, 10.24 kWh nominal

## Safety

RS485 monitoring sends only Modbus function 03 read requests for the three validated Felicity register blocks. No Modbus write function, raw-command endpoint, battery control, firmware update, or configuration function is implemented.

The optional CAN modes remain available for future diagnosis. `listen_only` opens a CAN adapter passively. `standalone_ack` permits protocol-level acknowledgements when connected directly to a battery while application data transmission remains disabled.

## RS485 wiring

- Battery RJ45 pin 6 / RS485-A connects to adapter CN1 `TXD+`.
- Battery RJ45 pin 5 / RS485-B connects to adapter CN2 `TXD-`.
- Battery RJ45 pin 1 / signal ground connects to adapter CN2 `GND`.
- Keep `RXD+`, `RXD-`, and `VCC` empty.
- Never connect battery RJ45 pin 2: it carries 12 V.

## Automatic updates

Home Assistant Supervisor owns installation and updates. **Auto update** is enabled for this installed app, so future versions from the Baiamonte repository are installed automatically.

## No battery replies

1. Confirm both batteries are powered and addresses 1 and 2 are configured.
2. Confirm the stable FTDI device exists under `/dev/serial/by-id`.
3. Verify pin 6 reaches `TXD+`, pin 5 reaches `TXD-`, and pin 1 reaches `GND`.
4. Confirm 9600 baud, 8N1.
5. If the adapter uses the opposite A/B naming convention, power down the batteries and swap only A/B at the adapter.
