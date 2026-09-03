# Changelog

## 0.1.2

- Pair equivalent Tuya Local and official Tuya breaker features whose names differ by `Switch`, `Phase A`, or `Total` suffixes.
- Distinguish an absent counterpart from a configured endpoint that is genuinely unavailable.

## 0.1.1

- Add configurable name-based estate exclusions.
- Exclude Miami and Office Blinds entities from Baiamonte automatic discovery by default.

## 0.1.0

- Initial local-first routing for Tuya Local, LocalTuya, explicitly enrolled Matter/ZHA entities, and official Tuya cloud entities.
- Restricted dashboard controls, route diagnostics, health endpoint, and Home Assistant summary sensor.
- Authenticated Home Assistant ingress only; no host port or browser-visible Tuya credentials.
