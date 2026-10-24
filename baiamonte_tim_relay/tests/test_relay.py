import asyncio
import socket
import unittest

from relay import Relay, Settings


class EchoProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.received = []
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.received.append((data, addr))
        self.transport.sendto(data, addr)


class ClientProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.queue = asyncio.Queue()

    def datagram_received(self, data, addr):
        self.queue.put_nowait((data, addr))


class FakeListener:
    def __init__(self):
        self.sent = []

    def is_closing(self):
        return False

    def sendto(self, data, peer):
        self.sent.append((data, peer))


class RecordingTransport:
    def __init__(self):
        self.sent = []
        self.closed = False

    def is_closing(self):
        return self.closed

    def sendto(self, data, addr=None):
        self.sent.append((data, addr))

    def close(self):
        self.closed = True


def free_udp_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def settings(
    *, sources=("127.0.0.1",), max_sessions=4, idle=180, rate=500,
    burst=1000, size=65507, max_pending=256, max_pending_source=64
):
    return Settings(
        enabled=True,
        provider_id="tim",
        allowed_source_ips=tuple(sources),
        upstream_ips=("127.0.0.1",),
        max_sessions=max_sessions,
        idle_timeout_seconds=idle,
        packets_per_second=rate,
        burst_packets=burst,
        max_datagram_bytes=size,
        max_pending_packets=max_pending,
        max_pending_per_source=max_pending_source,
    )


class RelaySocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loop = asyncio.get_running_loop()
        self.echo_transports = {}
        self.echo_protocols = {}
        for service in (500, 4500):
            transport, protocol = await self.loop.create_datagram_endpoint(
                EchoProtocol, local_addr=("127.0.0.1", 0), family=socket.AF_INET
            )
            self.echo_transports[service] = transport
            self.echo_protocols[service] = protocol
        self.listen_ports = {500: free_udp_port(), 4500: free_udp_port()}
        self.upstream_ports = {
            service: self.echo_transports[service].get_extra_info("sockname")[1]
            for service in (500, 4500)
        }
        self.relays = []
        self.clients = []

    async def asyncTearDown(self):
        for relay in self.relays:
            await relay.stop()
        for transport in self.clients:
            transport.close()
        for transport in self.echo_transports.values():
            transport.close()
        await asyncio.sleep(0)

    async def start_relay(self, relay_settings=None):
        relay = Relay(
            relay_settings or settings(),
            listen_ports=self.listen_ports,
            upstream_ports=self.upstream_ports,
            bind_host="127.0.0.1",
        )
        await relay.start()
        self.relays.append(relay)
        return relay

    async def client(self, ip="127.0.0.1"):
        transport, protocol = await self.loop.create_datagram_endpoint(
            ClientProtocol, local_addr=(ip, 0), family=socket.AF_INET
        )
        self.clients.append(transport)
        return transport, protocol

    async def roundtrip(self, transport, protocol, service, payload):
        transport.sendto(payload, ("127.0.0.1", self.listen_ports[service]))
        data, _ = await asyncio.wait_for(protocol.queue.get(), 1)
        self.assertEqual(data, payload)

    async def test_bidirectional_payloads_are_byte_preserving(self):
        relay = await self.start_relay()
        client, protocol = await self.client()
        payloads = (
            (500, b"\x01\x02IKEv2\x00binary"),
            (4500, b"\x00\x00\x00\x00IKE-over-NAT-T"),
            (4500, b"\x12\x34\x56\x78ESP-like-ciphertext"),
            (4500, b"\xff"),
        )
        for service, payload in payloads:
            await self.roundtrip(client, protocol, service, payload)
        self.assertEqual(relay.stats["downstream_packets"], len(payloads))
        self.assertEqual(relay.stats["upstream_packets"], len(payloads))

    async def test_port_switch_keeps_one_pinned_backend(self):
        relay = await self.start_relay()
        client, protocol = await self.client()
        await self.roundtrip(client, protocol, 500, b"initial-ike")
        await self.roundtrip(client, protocol, 4500, b"natt-switch")
        self.assertEqual(len(relay.sessions), 1)
        session = relay.sessions["127.0.0.1"]
        self.assertEqual(session.upstream_ip, "127.0.0.1")
        self.assertEqual(set(session.upstream_transports), {500, 4500})

    async def test_concurrent_exact_source_clients_are_isolated(self):
        class RecordingRelay(Relay):
            async def _ensure_upstream(self, session, service_port):
                transport = session.upstream_transports.get(service_port)
                if not transport:
                    transport = RecordingTransport()
                    session.upstream_transports[service_port] = transport
                return transport

        relay = RecordingRelay(settings(sources=("127.0.0.2", "127.0.0.3")))
        relay.accepting = True
        await asyncio.gather(
            relay.handle_downstream(4500, b"client-one", ("127.0.0.2", 22000)),
            relay.handle_downstream(4500, b"client-two", ("127.0.0.3", 23000)),
        )
        self.assertEqual(set(relay.sessions), {"127.0.0.2", "127.0.0.3"})
        self.assertEqual(relay.sessions["127.0.0.2"].upstream_transports[4500].sent[0][0], b"client-one")
        self.assertEqual(relay.sessions["127.0.0.3"].upstream_transports[4500].sent[0][0], b"client-two")
        await relay.stop()

    async def test_unapproved_source_is_silently_dropped(self):
        relay = await self.start_relay()
        await relay.handle_downstream(500, b"not-approved", ("192.0.2.10", 23456))
        self.assertEqual(relay.stats["dropped_source"], 1)
        self.assertFalse(relay.sessions)

    async def test_capacity_and_rate_limits_fail_closed(self):
        class RecordingRelay(Relay):
            async def _ensure_upstream(self, session, service_port):
                return RecordingTransport()

        relay = RecordingRelay(
            settings(sources=("127.0.0.2", "127.0.0.3"), max_sessions=1, rate=1, burst=1)
        )
        relay.accepting = True
        await relay.handle_downstream(500, b"first", ("127.0.0.2", 22000))
        await relay.handle_downstream(500, b"rate-limited", ("127.0.0.2", 22000))
        await relay.handle_downstream(500, b"capacity-limited", ("127.0.0.3", 23000))
        self.assertEqual(relay.stats["dropped_rate"], 1)
        self.assertEqual(relay.stats["dropped_capacity"], 1)
        await relay.stop()

    async def test_bounded_admission_creates_no_task_per_datagram(self):
        blocker = asyncio.Event()

        class SlowRelay(Relay):
            async def _process_downstream(self, service_port, data, addr):
                await blocker.wait()

        relay = SlowRelay(
            settings(rate=5000, burst=10000, max_pending=16, max_pending_source=8)
        )
        relay.accepting = True
        baseline_tasks = set(asyncio.all_tasks())
        relay._start_workers()
        worker_ids = {id(task) for task in relay._worker_tasks.values()}
        for _ in range(2000):
            relay.enqueue_downstream(500, b"bounded", ("127.0.0.1", 22000))
        await asyncio.sleep(0)
        self.assertEqual({id(task) for task in relay._worker_tasks.values()}, worker_ids)
        new_tasks = set(asyncio.all_tasks()) - baseline_tasks
        self.assertEqual({id(task) for task in new_tasks}, worker_ids)
        self.assertLessEqual(relay._pending_total, 8)
        self.assertGreaterEqual(relay.stats["dropped_queue"], 1992)
        await relay.stop()
        self.assertEqual(relay._pending_total, 0)

    async def test_same_service_source_port_change_fails_closed(self):
        now = [10.0]

        class RecordingRelay(Relay):
            async def _ensure_upstream(self, session, service_port):
                transport = session.upstream_transports.get(service_port)
                if not transport:
                    transport = RecordingTransport()
                    session.upstream_transports[service_port] = transport
                return transport

        relay = RecordingRelay(settings(), clock=lambda: now[0])
        relay.accepting = True
        await relay.handle_downstream(500, b"first", ("127.0.0.1", 22000))
        session = relay.sessions["127.0.0.1"]
        first_activity = session.last_activity
        now[0] = 20.0
        await relay.handle_downstream(500, b"changed-port", ("127.0.0.1", 22001))
        self.assertEqual(session.client_peers[500], ("127.0.0.1", 22000))
        self.assertEqual(session.last_activity, first_activity)
        self.assertEqual(relay.stats["dropped_peer_change"], 1)
        self.assertEqual(len(session.upstream_transports[500].sent), 1)
        # The other service binds independently and keeps the same backend.
        await relay.handle_downstream(4500, b"first-natt", ("127.0.0.1", 24500))
        self.assertEqual(session.client_peers[4500], ("127.0.0.1", 24500))
        self.assertEqual(session.upstream_ip, "127.0.0.1")
        await relay.stop()

    async def test_oversized_datagram_is_dropped(self):
        relay = await self.start_relay(settings(size=8))
        client, protocol = await self.client()
        client.sendto(b"123456789", ("127.0.0.1", self.listen_ports[500]))
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(protocol.queue.get(), 0.1)
        self.assertEqual(relay.stats["dropped_oversize"], 1)

    async def test_idle_expiry_and_shutdown_cleanup(self):
        now = [100.0]
        relay = Relay(
            settings(idle=30),
            listen_ports=self.listen_ports,
            upstream_ports=self.upstream_ports,
            bind_host="127.0.0.1",
            clock=lambda: now[0],
        )
        await relay.start()
        self.relays.append(relay)
        client, protocol = await self.client()
        await self.roundtrip(client, protocol, 500, b"create")
        upstream = relay.sessions["127.0.0.1"].upstream_transports[500]
        now[0] += 31
        await relay.expire_idle()
        self.assertFalse(relay.sessions)
        self.assertTrue(upstream.is_closing())
        await relay.stop()
        self.assertFalse(relay.listeners)
        self.assertIsNone(relay._cleanup_task)

    async def test_wrong_upstream_source_and_missing_peer_are_rejected(self):
        relay = Relay(settings())
        relay.accepting = True
        fake = FakeListener()
        relay.listeners[500] = fake
        session = relay._new_session("127.0.0.1", relay.clock())
        session.client_peers[500] = ("127.0.0.1", 12345)
        relay.handle_upstream(session, 500, b"rogue", ("127.0.0.9", self.upstream_ports[500]))
        self.assertEqual(relay.stats["dropped_wrong_upstream"], 1)
        self.assertFalse(fake.sent)
        session.client_peers.clear()
        relay.handle_upstream(session, 500, b"no-peer", ("127.0.0.1", 500))
        self.assertEqual(relay.stats["dropped_no_peer"], 1)

    async def test_reverse_direction_is_rate_limited_before_delivery(self):
        relay = Relay(settings(rate=1, burst=1))
        relay.accepting = True
        fake = FakeListener()
        relay.listeners[500] = fake
        session = relay._new_session("127.0.0.1", relay.clock())
        session.client_peers[500] = ("127.0.0.1", 12345)
        for _ in range(100):
            relay.handle_upstream(session, 500, b"reply", ("127.0.0.1", 500))
        self.assertEqual(len(fake.sent), 1)
        self.assertEqual(relay.stats["dropped_reverse_rate"], 99)

    async def test_reverse_global_bucket_bounds_multiple_sessions(self):
        relay = Relay(settings(sources=("127.0.0.1", "127.0.0.2"), rate=1, burst=1))
        relay.accepting = True
        fake = FakeListener()
        relay.listeners[500] = fake
        first = relay._new_session("127.0.0.1", relay.clock())
        second = relay._new_session("127.0.0.2", relay.clock())
        first.client_peers[500] = ("127.0.0.1", 12345)
        second.client_peers[500] = ("127.0.0.2", 12346)
        relay.handle_upstream(first, 500, b"one", ("127.0.0.1", 500))
        relay.handle_upstream(second, 500, b"two", ("127.0.0.1", 500))
        self.assertEqual(len(fake.sent), 1)
        self.assertEqual(relay.stats["dropped_reverse_rate"], 1)

    async def test_expired_session_cannot_deliver_late_reply(self):
        relay = Relay(settings())
        relay.accepting = True
        fake = FakeListener()
        relay.listeners[500] = fake
        session = relay._new_session("127.0.0.1", relay.clock())
        session.client_peers[500] = ("127.0.0.1", 12345)
        relay.sessions.pop("127.0.0.1")
        relay.handle_upstream(session, 500, b"late", ("127.0.0.1", 500))
        self.assertEqual(relay.stats["dropped_stale_session"], 1)
        self.assertFalse(fake.sent)

    async def test_upstream_creation_failure_does_not_count_forwarded_packet(self):
        class BrokenRelay(Relay):
            async def _ensure_upstream(self, session, service_port):
                raise OSError("simulated unreachable backend")

        relay = BrokenRelay(settings())
        relay.accepting = True
        await relay.handle_downstream(500, b"payload", ("127.0.0.1", 12345))
        self.assertEqual(relay.stats["upstream_errors"], 1)
        self.assertEqual(relay.stats["downstream_packets"], 0)
        self.assertIn("OSError", relay.last_error)
        await relay.stop()

    async def test_disabled_or_stopped_relay_drops_without_session(self):
        relay = Relay(settings())
        await relay.handle_downstream(500, b"payload", ("127.0.0.1", 12345))
        self.assertEqual(relay.stats["dropped_inactive"], 1)
        self.assertFalse(relay.sessions)


if __name__ == "__main__":
    unittest.main()

