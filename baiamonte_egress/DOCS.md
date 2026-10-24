# Rahamin Baiamonte Egress

This experimental Home Assistant app is an isolated CloudConnexa Network
Connector and outbound NAT gateway for explicitly approved TIM Wi-Fi Calling
traffic. It is designed for the Baiamonte ODROID M1 but does not hardcode a
site interface or Home Assistant address.

The app is not a general Internet Gateway. Its default `probe_only` mode starts
the connector behind an app-owned deny guard. It installs no ACCEPT/NAT rules,
does not enable IPv4 forwarding, and adds no reviewed return routes. It may
remove stale app-owned chains during startup and OpenVPN still creates its
container-local tunnel/interface route. `tim_egress` accepts
only configured source CIDRs, exact public TIM destination IPs, and UDP
destination ports—initially the observed source, the currently resolved or
observed ePDG IPs, and IKE/NAT-T ports 500 and 4500. All other tunnel-forwarded
traffic is dropped inside the app container.

`https_probe` is a separate temporary mode. It forwards only TCP 443 from the
approved source to explicitly configured public `/32` destinations. This makes
the exact-host public-IP proof possible without broadening the TIM production
policy. Clear the probe destinations and return to `probe_only` after testing.

## Safety boundaries

- The app has only `/dev/net/tun` and `NET_ADMIN`; it has no `NET_RAW`, Docker,
  Supervisor API, host PID, or host-network access. It uses the Supervisor's
  default Docker AppArmor confinement rather than disabling AppArmor.
- Its profile is kept in the app's private `/data` volume with mode `0600` and
  is never returned by the API or written to logs.
- Uploaded profiles cannot execute scripts, load plugins, expose a management
  socket, reference a secondary password/key file, include another
  configuration, or install a profile-local route. The uploaded file is parsed
  with shell-compatible tokenization and regenerated from a strict directive
  allowlist at upload and again at start. Inline certificate/key blocks require
  exact unsuffixed OpenVPN tags and strictly validated PEM or OpenVPN static-key
  bodies; raw inline text is never copied through unchecked. OpenVPN runs with
  `route-nopull`; only
  reviewed exact return routes are added after authenticated session readiness.
- Firewall work is confined to `RHM_BAI_FWD` and `RHM_BAI_NAT`. Cleanup removes
  references to those chains only and never flushes a global table or chain.
- The app never asks for `SYS_ADMIN`, host networking, or full access to change
  a sysctl. If its namespaced IPv4 forwarding state is already enabled, it
  leaves the read-only value untouched. If HAOS exposes it disabled and
  read-only, activation fails closed and the connector is stopped.
- The running `Baiamonte Dashboard VPN` is a separate app and tenant. Do not
  stop, reconfigure, or reuse it for this gateway.

## Installation status

This source tree and its package are build artifacts only. Do not install it,
upload a connector profile, create credentials, stop/move the existing Italy
Connector, or repoint the TIM CloudConnexa Application without separate
action-time approval.

## Deployment sequence after approval

1. Verify CloudConnexa connector entitlement. `3 of 3 Connectors Online` is a
   health count, not confirmed subscription capacity.
2. Prefer an entitled temporary test connector. If unavailable, identify and
   schedule a controlled cutover of the existing Italy Connector identity.
3. Install the app but leave it stopped and in `probe_only` mode.
4. Import the approved connector profile through authenticated Ingress, start
   the app, and verify process, interface, and authenticated session health. No
   traffic forwarding or NAT occurs in this mode. Verify the app namespace
   reports IPv4 forwarding enabled; do not widen privileges if it does not.
   Determine CloudConnexa's
   presented source with a separately approved diagnostic outside this
   production build, then configure the exact reviewed `/32`.
   Confirm the tunnel has no IPv6 addressing or forwarding; this release is an
   IPv4-only gateway and its packet harness does not validate IPv6 behavior.
5. Resolve the exact probe hostname, configure only its current public IPs, use
   `https_probe`, and run the exact-host HTTPS egress test in
   `BAIAMONTE_EGRESS_PLAN.md`. Compare
   its returned IP with the add-on's local Starlink IP/country result.
6. After a second approval, select `tim_egress`, point only the TIM ePDG
   Application to Baiamonte, reconnect the phone to Miami Wi-Fi, and watch the
   app-owned rule counters.
7. A green tunnel, DNS lookup, or Italian public IP does not prove carrier
   registration. The final proof is `TIM Wi-Fi Call` on the handset followed by
   a controlled real call that does not use cellular roaming.

## Rollback

Stop the app to remove its owned rules, point TIM back to Barbadori, and restore
the Italy Connector to its original host if it was moved. Verify the original
three connectors and the separate Dashboard VPN are healthy.

