# Baiamonte Tuya

Baiamonte Tuya is the estate operations view for Tuya-family equipment. Home Assistant integrations own device communication; this app discovers their entities, presents one local-first route for each device, and optionally retries a command through the official Tuya cloud entity.

## Supported connection paths

Use the first available path in this order:

1. Pair Matter devices with Home Assistant's official Matter integration.
2. Pair Tuya Zigbee devices directly with ZHA instead of a Tuya gateway.
3. Install `make-all/tuya-local` through HACS for compatible legacy Wi-Fi devices.
4. Keep Home Assistant's official Tuya integration only for fallback or equipment that cannot operate locally.

The app does not install HACS, extract local keys, provision Tuya cloud accounts, or turn a cloud-only device into a LAN device.

## Pilot setup

1. Back up Home Assistant.
2. Add the official Tuya integration and confirm the pilot devices appear.
3. Install `make-all/tuya-local` through HACS and configure one noncritical Wi-Fi device. The local key stays in that integration's protected Home Assistant configuration; never enter it in this app.
4. Install **Baiamonte Tuya** from the Baiamonte app repository and enable **Show in sidebar**.
5. Local Tuya and LocalTuya entities are discovered automatically. Add Matter or ZHA entity IDs under **Additional local entities**.
6. If automatic name matching is ambiguous, add a **Manual route pair** such as:

   `Courtyard Lights|light.courtyard_lights_local|light.courtyard_lights`

7. Confirm each important device shows **LOCAL** before testing an internet outage.

## On-site and remote use

On site, open Home Assistant using its LAN address and select **Tuya** in the sidebar. The browser, Home Assistant, automations, and locally paired devices remain on the estate network when the WAN is down.

For remote use, connect through the existing Baiamonte CloudConnexa route and open the same Home Assistant interface. Do not forward this app or Home Assistant directly from the public internet.

## Discovery boundaries

Automatic local discovery intentionally defaults to the `tuya_local` and `localtuya` entity platforms. Matter and ZHA can contain unrelated estate equipment, so those entities require explicit enrollment in **Additional local entities** or a manual pair.

Automatic pairing compares entity domain and a normalized friendly name. Use manual pairs for duplicate names, multi-channel devices, or any critical load. Manual pairs take priority.

## Controls and safety

The dashboard permits a restricted set of Home Assistant services for lights, switches, fans, covers, climate devices, humidifiers, valves, vacuums, sirens, selects, and numbers. Locks, alarms, generic scripts, arbitrary services, area targets, and device-wide targets are not accepted.

Disable **Dashboard controls** to use the app as a read-only migration and health view. Treat gates, pumps, heaters, irrigation, alarms, and other consequential loads as separate commissioning reviews with physical fail-safe controls.

## Network preparation

- Reserve a stable DHCP address for every Tuya Wi-Fi device.
- Allow Home Assistant to initiate traffic to the IoT network.
- Validate local operation before applying WAN or DNS restrictions.
- Do not configure two local Tuya integrations for the same device; many products accept only one local connection.
- Re-pairing a Tuya device normally changes its local key and requires updating its local integration.

## Offline acceptance test

For every important device:

1. Record the active route and current state.
2. Disconnect the WAN while leaving the LAN, Wi-Fi, Home Assistant, and network switches powered.
3. Send a command from the on-site dashboard and operate the physical control.
4. Confirm state changes propagate both ways.
5. Restart the device and Home Assistant, then repeat the test.
6. Restore the WAN and confirm the cloud copy recovers without replacing the local route.

An **OFFLINE** result means neither configured Home Assistant entity is available. A **CLOUD** result is functional but does not meet the estate's internet-outage requirement.

## Home Assistant entity

The app publishes `sensor.baiamonte_tuya_route`. Its state is `local`, `cloud`, `offline`, or `error`, with route counts and the last refresh time as attributes. It does not publish local keys, cloud credentials, IP addresses, or raw device attributes.
