# Baiamonte Dashboard VPN

This Home Assistant app keeps the Baiamonte HA host connected to the `baiamonte-dashboard` CloudConnexa cloud. Remote TV kiosks connect with their own CloudConnexa user profile, then open the private dashboard hostname. No Home Assistant or dashboard port should be forwarded from the public internet.

## CloudConnexa design

- Create a **Host** named `Baiamonte Home Assistant` with domain `dashboard.baiamonte`.
- Deploy its OpenVPN Host Connector and download the Connector `.ovpn` profile.
- Give the dashboard user group access only to the Home Assistant host/application.
- Create a separate user for each kiosk. Do not share the administrator or HA Connector profile with a TV.

## Home Assistant setup

1. Install **Baiamonte Dashboard VPN** from the Baiamonte app repository.
2. Start it and enable **Show in sidebar** and **Automatic updates**.
3. Open **Dashboard VPN** in the sidebar.
4. In CloudConnexa, select **Linux → Debian → Generate Token** for `ha-baiamonte`.
5. Paste the one-time setup token into the app and select **Install & connect**. The app follows OpenVPN's open-source connector setup format to download and decrypt the official `.ovpn` profile.
6. Wait for **Cloud tunnel online**. Confirm `sensor.baiamonte_dashboard_vpn` is `connected`.

The setup token is held only in memory for the profile download. The decrypted profile is stored at `/data/connector.ovpn` inside the app's protected persistent storage with mode `0600`. Neither value is included in this repository, Home Assistant entity attributes, browser status responses, or logs. Reinstalling the app may remove its protected data; generate a replacement token from CloudConnexa when needed.

## TV kiosk setup

1. Create or invite a dedicated CloudConnexa user for the TV.
2. Install OpenVPN Connect on the Android/Google TV device.
3. Import the profile from `https://baiamonte-dashboard.openvpn.com` and connect.
4. Open `http://dashboard.baiamonte:8123` in the kiosk browser. Change the app's **TV dashboard URL** setting if the CloudConnexa hostname differs.
5. Configure OpenVPN Connect and the kiosk browser to start after device reboot if supported by the TV platform.

The TV profile is a user profile; the HA profile is an unattended Host Connector profile. They are intentionally different.

## Security behavior

- The app requires `/dev/net/tun` and `NET_ADMIN` so the CloudConnexa tunnel can terminate inside its isolated container.
- A private reverse proxy on the connector's tunnel port `8123` forwards to Home Assistant and supports its WebSocket connection. The app does not publish a Home Assistant host port.
- A pushed `redirect-gateway` is ignored, preventing the connector from replacing Home Assistant's default internet route.
- Profiles that contain executable OpenVPN scripts or plugins are rejected.
- The app reconnects automatically when OpenVPN exits and starts automatically with Home Assistant.
- The CloudConnexa policy remains the enforcement point. Limit the TV group to the dashboard application and deny broader private-network access.

## Troubleshooting

- **Profile rejected:** confirm you downloaded an OpenVPN Host Connector `.ovpn` profile, not a TV user profile or token.
- **Connecting but not connected:** check the app log for an authentication or remote-endpoint error and deploy a fresh Connector profile if needed.
- **TV VPN connects but the dashboard does not open:** verify the Host domain is `dashboard.baiamonte`, the TV group can access it, and the HA dashboard URL/port is correct.
- **After HA reboot:** the app starts automatically and uses the stored profile. The status sensor should return to `connected` without manual action.
