# Offline build report

Version: 0.1.0

Status: revised source candidate awaiting independent rereview; not published, installed, started, or activated.

This is a new relay architecture distinct from the stopped `baiamonte_egress` connector add-on. It preserves all three production TP-Link CloudConnexa connectors and needs no fourth connector or OpenVPN profile.

The implementation has 23 passing tests. The integration portion uses real local UDP sockets to validate byte-preserving bidirectional transport for IKE-like, four-zero NAT-T, ESP-like, and keepalive payloads. Additional tests cover UDP 500/4500 backend pinning, concurrent approved source IPs, wrong-source rejection, stale-session reply rejection, upstream failure handling, limits, expiry, inactive-state rejection, and cleanup.

The first independent source review found four pre-staging blockers. The revised candidate now performs bounded admission before any work is scheduled, uses exactly two long-lived service workers instead of per-datagram tasks, applies per-source/global forward bounds and per-session/global reverse rate limits, explicitly rejects multicast/broadcast/reserved upstreams, and rejects same-service source-port changes without overwriting the return peer or refreshing lifetime. The 2,000-datagram stalled-worker regression proves fixed task count, bounded pending packets, and clean drain on stop. These changes still require independent rereview.

An ARM64 Home Assistant base image built successfully as local image `rahamin/baiamonte-tim-relay:0.1.0` (revised image ID `sha256:841e92b44a3fe2a3b4f73161a85538a5148abc61b12448baaa45975f05d9970a`). A default-state smoke run reported `enabled:false`, `listening:false`, no sessions or pending packets, no added Linux capabilities, no devices, and ordinary bridge networking. The temporary smoke container was stopped and removed. Container/HAOS host UDP mapping behavior and live carrier registration remain staging gates.

The build deliberately supports TIM only for initial validation. Its provider registry is extensible in reviewed source, but it does not guess other Italian carrier endpoints or claim multi-carrier support.

