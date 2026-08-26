# Baiamonte Netatmo

This Home Assistant app provides one local-first operating view for Legrand/Netatmo devices while retaining the official Netatmo cloud integration as a fallback.

## What it does

- Discovers entities created by Home Assistant's local **HomeKit Device** or **Matter** integrations.
- Discovers matching entities from the official **Netatmo** integration.
- Pairs entities with the same domain and friendly name.
- Reports whether each device is currently using a local, cloud, or offline path.
- Routes commands sent to the app API through the local entity first and retries through cloud when enabled.
- Publishes `sensor.baiamonte_netatmo_route` for automations and alerting.

## Before installing

Keep the existing Netatmo integration. It supplies the cloud path.

For local control, the physical gateway or device must expose HomeKit or Matter. In Home Assistant, go to **Settings → Devices & services** and look for a discovered **HomeKit Device** or **Matter** device. Pair it there. HomeKit Device is local and does not require an Apple home hub.

Some Netatmo products do not expose a local protocol. Those products remain cloud-only. This app cannot turn a proprietary cloud-only product into a LAN device.

## Automatic and manual pairing

Automatic pairing compares the Home Assistant domain (`switch`, `light`, and so on) and friendly name after removing words such as “Netatmo”, “HomeKit”, “local”, and “cloud”.

If a device does not match automatically, add an item under **Manual entity pairs**:

```text
Dishwasher Outlet|switch.dishwasher_outlet_local|switch.dishwasher_outlet
```

The format is `Display name|local entity ID|cloud entity ID`. Leave one entity blank for a deliberately local-only or cloud-only route.

## Control API

The ingress dashboard is read-only. Automations or an authenticated ingress client can send a command to `POST /api/control`:

```json
{
  "target": "Dishwasher Outlet",
  "service": "turn_on",
  "data": {}
}
```

The service is called in the entity's Home Assistant domain. Only entities already present in the bridge routing table can be controlled.

## Migration safety

Do not delete the Netatmo integration until every important device shows a green local route and you have tested it with the internet disconnected. Keeping both integrations may create duplicate entities; hide the cloud copies from dashboards after the bridge has been validated, but retain them for fallback.
