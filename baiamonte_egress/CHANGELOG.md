# Changelog

## 0.1.0

- Initial offline build with probe-only startup, source/port-scoped forwarding,
  app-owned NAT chains, strict profile validation, and truth-labelled health.
- Security-review hardening: strict OpenVPN directive allowlist and canonical
  runtime profile, deny-first persistent guard, authenticated session readiness,
  `route-nopull` plus exact return routes, exact `/32` sources/TIM destinations,
  packet-tested fail-closed transitions, mode-0600 temporary files, and removal
  of `NET_RAW`, tcpdump, and disabled AppArmor.
- Closed the OpenVPN inline-block prefix-parsing gap by requiring exact tags and
  validated PEM/static-key structures before canonical output is generated.

