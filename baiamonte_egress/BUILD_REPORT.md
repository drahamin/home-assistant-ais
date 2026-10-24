# Build report — 0.1.0 security-remediation candidate

Built and verified locally on 2026-10-24. No Home Assistant installation,
CloudConnexa credential creation/import, connector cutover, route/firewall
change, or publication occurred.

## Deliverables

- Source: `baiamonte_egress/`
- Review archive: `rahamin-baiamonte-egress-ha-app-0.1.0.zip`
- Local image: `rahamin-baiamonte-egress:0.1.0`
- Image platform/ID:
  `linux/arm64 sha256:556b3efd80109a53651962c0c17e0a99a4e2b4e0d8c55aa4bb479bc4662937d8`

The archive SHA-256 is recorded alongside the completed archive and in its
private Library version metadata; an archive cannot safely contain its own
final digest.

## Verification performed

- Python syntax compilation: passed.
- Unit tests: 43 passed. Coverage includes strict profile allowlisting and
  canonicalization, rejection of command-line spellings and path/script/plugin
  directives, exact inline-block tags and strict PEM/static-key structure,
  comment/whitespace/nesting regressions for all supported inline blocks,
  initial mode-0600 secret writes, exact `/32` source and public
  destination validation, exact UDP 500/4500 production rules, separate exact
  TCP 443 probe rules, deny-first transition order, authenticated-session
  readiness, route ownership, idempotent cleanup, and read-only sysctl behavior.
- Two existing saved certificate-based connector-profile formats passed the
  strict parser without printing or packaging profile content.
- One resulting canonical saved profile was accepted by the actual OpenVPN 2.6
  option parser in the isolated container using its offline `--show-ciphers`
  exit path. It made no network connection and emitted no profile content.
- ARM64 Docker build using Alpine 3.22: passed.
- Production-capability check: passed with only `NET_ADMIN`. The isolated
  namespace reported `ip_forward=1`; the code left the read-only value
  untouched and created/deleted its test chain. No `NET_RAW`, `SYS_ADMIN`, host
  network, full access, or disabled AppArmor is present in the manifest.
- Packet-level integration: passed in disposable Linux network namespaces.
  Exact approved UDP 4500 passed only while active; UDP 53, wrong source, and
  wrong destination remained blocked, including during transitions. Stop and
  cleanup returned to DROP and removed app-owned FORWARD references.
- ARM64 no-profile smoke test: passed. The API truthfully reported
  `probe_only`, no forwarding, no connector, and no tunnel.
- Credential-pattern scan: no packaged credentials or private keys.

The packet integration container is privileged solely to create disposable
network namespaces and veth pairs. That test privilege is not present in the
production add-on. The harness tests IPv4 veth forwarding and rule transitions;
it does not simulate a live OpenVPN stop/crash/reconnect, Supervisor kill, or
IPv6 forwarding.

## Security-review remediation

- Replaced blacklist parsing with a strict directive allowlist and a generated,
  revalidated runtime profile. Inline blocks require exact tags and validated
  PEM/static-key bodies, closing OpenVPN's prefix-tag parsing discrepancy.
  Automatic pushed routes are disabled with
  `route-nopull`; only app-reviewed exact return routes are installed.
- Added a persistent deny-first guard before OpenVPN starts. Active rules/NAT
  are assembled before the guard is switched; session loss or failure restores
  DROP before removing active state and stops the connector if fail-closed
  handling cannot be verified.
- Limited production forwarding/NAT to reviewed source `/32`, destination
  public IP, UDP 500/4500, interfaces, and connection state.
- Separated process, interface, authenticated-session, local egress, forwarded
  egress, and handset-registration truth in status output.
- Removed packet capture, `NET_RAW`, and disabled AppArmor. Source identity must
  be learned through a separately approved diagnostic before production mode.

## Remaining stage gates

This is a review candidate, not deployment approval. Before installation or
cutover, independently review this version and then verify on isolated HAOS:

1. actual CloudConnexa connector entitlement and profile identity;
2. HAOS app namespace `ip_forward=1` with the existing narrow privileges;
3. actual CloudConnexa-presented source `/32` and exact current TIM ePDG IPs;
4. tunnel/WAN interface selection, app-owned counters, cleanup, and coexistence
   with the separate Dashboard VPN;
5. exact-host Baiamonte/Starlink egress; and
6. physical handset `TIM Wi-Fi Call` registration and a non-roaming call.

Do not widen privileges, source ranges, destinations, protocols, ports, or
CloudConnexa scope to make a failed stage test pass.
