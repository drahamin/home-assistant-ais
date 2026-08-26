# Rahamin Pi TV Kiosk

This package turns the Rahamin Raspberry Pi into an always-on Baiamonte Home Assistant TV kiosk. It keeps a dedicated CloudConnexa client connected, waits for the private dashboard route, and launches Chromium full-screen at:

`http://ha.dashboard.baiamonte:8123`

The CloudConnexa profile is a device credential. It is never committed to GitHub and is installed as root-readable-only `/etc/openvpn/client/baiamonte-kiosk.conf`.

This device is dashboard-only at two layers:

- CloudConnexa grants the `Baiamonte TV Kiosks` group access only to the `Baiamonte HA Dashboard` application.
- Chromium receives a managed URL policy that blocks every destination except the direct Baiamonte HA address and its CloudConnexa-only hostname.

The Pi does not run the VPN at Baiamonte. Every 15 seconds it verifies the local Home Assistant manifest at `http://192.168.0.10:8123`. While that exact endpoint is available, it stops OpenVPN and uses the direct LAN path. Away from Baiamonte, it starts the dedicated CloudConnexa profile and switches Chromium to `http://ha.dashboard.baiamonte:8123`. A network change restarts only the kiosk browser, not the Pi.

## Install or update

Copy the dedicated `Rahamin-Pi-TV-Kiosk` `.ovpn` profile to the Pi, then run:

```sh
sudo ./install.sh /path/to/Rahamin-Pi-TV-Kiosk.ovpn
```

For later software-only updates, the protected profile can be reused:

```sh
sudo ./install.sh
```

The installer is idempotent. It installs OpenVPN, Chromium, Cage, and curl; enables automatic VPN recovery; installs the kiosk launcher; and starts both services.

## Operations

```sh
systemctl status openvpn-client@baiamonte-kiosk.service
systemctl status baiamonte-tv-kiosk.service
journalctl -u openvpn-client@baiamonte-kiosk.service -u baiamonte-tv-kiosk.service -f
```

The first dashboard visit may require signing in to Home Assistant with a non-administrator display account. Chromium preserves that session in `/home/rahamin/.config/chromium-baiamonte-kiosk`.
