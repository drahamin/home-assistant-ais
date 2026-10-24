# Baiamonte Italian Wi-Fi Calling Relay

This add-on is an experimental transport component, not proof that TIM Wi-Fi Calling will register outside Italy. TIM documents Voce WiFi as limited to Italy. The relay must remain stopped and disabled until a separately approved staged test.

## Intended packet path

For the initial TIM-only validation, CloudConnexa DNS would later map the exact TIM ePDG name `epdg.epc.mnc001.mcc222.pub.3gppnetwork.org` to the Home Assistant private address `192.168.0.10`. The phone would send UDP 500/4500 to Home Assistant through the existing private site VPN. The relay would copy each complete datagram, unchanged, to one pinned reviewed TIM public IPv4 address through Baiamonte's normal Italian WAN. Replies would be copied back through the same listener.

The relay does not parse, terminate, authenticate, or modify IKE, ESP-in-UDP, the four-zero IKE marker, or NAT keepalives. This follows the outer-address/NAT model described by RFC 7296 section 2.23 and RFC 3948.

## Provider boundary

Version 0.1.0 contains one reviewed provider profile: `tim`. The code is profile-oriented so another carrier can be added after its exact ePDG identity and upstream addresses are verified, but other Italian providers are unsupported and disabled.

One listener IP can serve only one enabled provider. DNS names disappear before the UDP relay sees a packet, and IKE initiation does not reliably identify the carrier. Mapping multiple carrier DNS names to the same Home Assistant IP would therefore be ambiguous and is rejected architecturally. Future multi-provider support requires separately approved, distinguishable listener endpoints; ordinary Home Assistant port mappings do not select a host IP and do not themselves solve this.

## Safe staged configuration

The add-on defaults to `enabled: false`, with empty allowlists. Before a staged start, all of these must be independently verified:

1. Host UDP 500 and 4500 are available on Home Assistant.
2. The phone's exact source IPv4 as observed at the relay is known; do not assume it remains `192.168.86.127` after site-to-site forwarding.
3. Each configured upstream is a currently resolved and independently verified public IPv4 for the exact TIM ePDG name.
4. The existing CloudConnexa Application/DNS precedence is tested without broadening the current carrier rule.
5. IPv6 for the exact test hostname is returned as NODATA for the trial, unless a separately reviewed IPv6 design exists.
6. Rollback is ready: remove the exact DNS override, stop the add-on, and confirm the original CloudConnexa application remains unchanged.

The `upstream_ips` option is not a DNS resolver and does not accept hostnames, networks, private addresses, or arbitrary destinations. The selected backend is pinned for the lifetime of a source-IP session and shared across UDP 500 and 4500.

Admission is bounded before scheduling: at most `max_pending_packets` total and `max_pending_per_source` per approved source can be queued or in flight. The two fixed service workers are the only downstream payload-processing tasks. Replies are delivered synchronously and pass both per-session and global rate buckets before reaching the client listener.

One observed source IP represents one handset session. UDP 500 and 4500 bind their client source ports independently. If an already-bound service unexpectedly arrives from a different source port, the relay drops it without changing the return peer or refreshing the lifetime; NAT rebinding requires expiry or a controlled relay restart. This prevents silent peer takeover but can make a legitimate rebinding fail closed during staging.

## Diagnostics and truth boundaries

The ingress status endpoint reports listener state, counters, configured public upstreams, and active-session count. It deliberately does not display packet bodies or client source ports.

- A listening process proves only local socket readiness.
- A relayed reply proves UDP round-trip transport to the configured upstream.
- It does not prove the endpoint belongs to TIM, validate a carrier certificate, establish a SIM-authenticated tunnel, or prove Wi-Fi Calling.
- Success requires the handset to display TIM Wi-Fi Calling and complete a real test call without cellular roaming.

## Activation is not authorized by this build

Do not publish, install, start, add DNS records, change the existing CloudConnexa application, change router routes, or expose the ports to a WAN under the offline-build approval. Those are separate actions requiring an exact staged-test approval.

