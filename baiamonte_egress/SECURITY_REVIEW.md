# Security review checklist

Review date: 2026-10-24. Scope: offline source and tests only; no live deployment.

## Passed in source/tests

- [x] Separate slug, port, data volume, entity namespace, and firewall chains;
      no interaction with Baiamonte Dashboard VPN.
- [x] Manual boot and `probe_only` defaults; forwarding is opt-in.
- [x] Only `/dev/net/tun` and `NET_ADMIN`; no `NET_RAW`, Docker API, host
      networking/PID, Supervisor API, full access, or disabled AppArmor.
- [x] Sources must be individually reviewed IPv4 `/32` addresses; the default
      is the phone address placeholder `192.168.86.127/32`.
- [x] Forward path is restricted by tunnel interface, WAN interface, source,
      exact public destination, UDP protocol, port, and conntrack state. Return
      traffic is restricted by both interfaces, destination source CIDR, and
      established/related state.
- [x] NAT uses identical source/protocol/port boundaries.
- [x] Only `RHM_BAI_FWD` and `RHM_BAI_NAT` are flushed/deleted. Global chains
      and tables are never flushed. Repeated apply/cleanup is tested.
- [x] A persistent deny-first guard exists before OpenVPN starts. Active rules
      and NAT are complete before one guard-rule replacement and forwarding
      enablement. Loss of authenticated session or setup failure restores the
      guard before removing rules; unrecoverable guard failure stops OpenVPN.
- [x] Connector profiles are shell-tokenized and regenerated from a strict
      directive allowlist. Unknown, duplicated, conflicting, `--`-prefixed,
      quoted plugin, script, management, include/config, external secret/path,
      and local route directives are rejected. The canonical profile is
      revalidated at every start; temporary files are mode `0600` at creation.
- [x] Inline blocks accept only exact unsuffixed tags and strict PEM/static-key
      structures. Comment-suffixed, whitespace-altered, nested, mismatched, and
      tag-like content is rejected for every supported inline block. A saved
      profile canonicalized by this parser was accepted by OpenVPN 2.6's real
      option parser without exposing or connecting with the profile.
- [x] OpenVPN uses `route-nopull`; only reviewed exact source return routes are
      added after authenticated session readiness and removed on deactivation.
- [x] Ingress API restricted to the Home Assistant proxy/local process.
- [x] Health HTTP destinations are HTTPS and pinned to the two expected public
      diagnostic hosts, preventing arbitrary internal URL probes.
- [x] Logs redact common token/password/secret patterns.
- [x] Status explicitly separates tunnel, DNS, local WAN egress, forwarded
      egress, and physical-handset TIM registration.
- [x] `probe_only` retains the deny guard, installs no ACCEPT/NAT rules, does not
      enable forwarding, and has no packet-capture capability.
- [x] HTTPS proof uses a separate mode restricted to approved source, TCP 443,
      and exact public destination IPs; it does not broaden UDP production rules.

## Must be verified on an isolated Home Assistant deployment

- [ ] Add-on builds for `aarch64` under the installed HAOS/Supervisor builder.
- [ ] On HAOS, container `/proc/sys/net/ipv4/ip_forward` is already `1`. The app
      treats an identical read-only state as a no-op; disabled plus read-only
      fails closed. Do not grant `SYS_ADMIN`, host networking, or full access.
- [ ] Alpine/OpenVPN accepts the production CloudConnexa profile with
      `route-nopull`; reviewed exact return routes provide the required path.
- [ ] Actual tunnel and WAN interface names are selected correctly.
- [ ] A separately approved diagnostic establishes the real source address
      presented by CloudConnexa. Configure only that reviewed `/32`; never
      broaden speculatively.
- [ ] Stopping/crashing the app removes its jumps and chains.
- [ ] Dashboard VPN remains connected and its routing/rules are unchanged.
- [ ] Temporary exact-host remote test exits via the same Baiamonte/Starlink
      Italian public IP reported locally.
- [ ] TIM rule counters show bidirectional UDP 500/4500 traffic; any protocol
      expansion is evidence-driven and separately reviewed.
- [ ] TIM registration and a non-roaming call succeed on the physical handset.
- [ ] Confirm the connector receives no IPv6 tunnel address or route. This
      release is deliberately IPv4-only; the namespace harness does not test
      `ip6tables` or IPv6 forwarding.

## Not testable on the macOS build host

Actual `/dev/net/tun` OpenVPN negotiation, CloudConnexa propagation and source
preservation, live OpenVPN stop/crash/reconnect fault injection, IPv6 behavior,
Starlink geolocation/latency, Home Assistant Ingress, Supervisor lifecycle
cleanup, and TIM carrier registration require the staged
ODROID/CloudConnexa/handset test. Firewall transition and packet-boundary tests
do run in disposable Linux namespaces under Docker; that test container is
privileged, while the production manifest remains limited to `NET_ADMIN`.

