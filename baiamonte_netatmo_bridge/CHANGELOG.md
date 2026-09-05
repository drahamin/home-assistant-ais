# Changelog

## 0.1.2

- Automatically pair local HomeKit and Netatmo cloud controls by their shared Legrand hardware serial number.
- Exclude non-control entities such as HomeKit Identify buttons and Netatmo telemetry sensors from the routing table.

## 0.1.1

- Use each target entity's own Home Assistant domain during local-to-cloud fallback, allowing HomeKit `light` entities to pair safely with Netatmo `switch` entities.

## 0.1.0

- Initial local-first HomeKit/Matter and Netatmo cloud routing.
- Automatic and manual entity pairing.
- Ingress route dashboard, health endpoint, control API, and Home Assistant summary sensor.
