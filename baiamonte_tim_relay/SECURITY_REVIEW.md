# Security review checklist

## Enforced by source

- Disabled by default and refuses an enabled configuration without exact source and upstream IPv4 allowlists.
- Accepts only reviewed provider identifiers; version 0.1.0 accepts only `tim`.
- Accepts exact IPv4 addresses, not CIDR ranges or hostnames; upstreams must be globally routable.
- Listens only on UDP 500 and 4500 service endpoints and forwards only to the same upstream service port.
- Pins one upstream address per source-IP session across IKE and NAT-T.
- Uses connected upstream UDP sockets and also checks the reply source address and port.
- Copies datagrams byte-for-byte; does not inspect or rewrite encrypted payloads.
- Enforces maximum datagram size, token-bucket rate limits, maximum sessions, and idle expiry.
- Performs admission before scheduling work, with fixed global and per-source packet bounds; one long-lived worker per service replaces per-datagram task creation.
- Applies per-session and global token buckets to replies before synchronous downstream delivery; upstream callbacks create no tasks or payload queues.
- Closes listener and upstream transports on orderly shutdown.
- Does not request host networking, TUN, NET_ADMIN, NET_RAW, SYS_ADMIN, AppArmor disablement, Supervisor API access, or credentials.
- Does not alter host routes, DNS, firewall state, sysctls, or any CloudConnexa connector.

## Staging gates not provable offline

- Confirm actual source address observed at the Baiamonte add-on boundary.
- Confirm host UDP 500/4500 are free and are not exposed on the WAN.
- Confirm Home Assistant Supervisor maps replies back from host UDP 500/4500 as expected.
- Confirm CloudConnexa's exact DNS-record precedence against the existing TIM Application.
- Confirm the chosen public upstream address set immediately before the test.
- Exercise stop, crash, restart, backend timeout, and DNS rollback on HAOS while verifying the existing Dashboard VPN and TP-Link connector remain unchanged.
- Verify no IPv6 bypass and no alternate hostname is used by the handset.
- Prove actual handset registration and a real TIM call; UDP transport alone is insufficient.

## Known design constraint

Sessions are keyed by observed source IPv4. UDP 500 and 4500 bind independently, but an unexpected source-port change on an already-bound service is dropped without replacing the peer or refreshing the session lifetime. NAT rebinding therefore fails closed until session expiry or a controlled restart. This is intentionally narrow for one approved handset source. Two phones collapsed behind the same observed source, or simultaneous same-service flows from one observed source, cannot be safely distinguished and are unsupported. Multiple carriers cannot share the same listener endpoint.

