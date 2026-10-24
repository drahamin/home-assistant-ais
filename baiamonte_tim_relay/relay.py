#!/usr/bin/env python3
"""Narrow, byte-preserving UDP relay for TIM IKEv2/NAT-T traffic."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import signal
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


APP_VERSION = os.environ.get("APP_VERSION", "0.1.0")
OPTIONS_FILE = Path(os.environ.get("RELAY_OPTIONS_FILE", "/data/options.json"))
HTTP_PORT = int(os.environ.get("RELAY_HTTP_PORT", "8104"))
LISTEN_PORTS = {500: 15000, 4500: 14500}
SERVICE_PORTS = (500, 4500)

# A provider profile must be added in source, reviewed, and tested before it can
# be selected.  Arbitrary hostnames and arbitrary UDP destinations are not
# accepted from add-on options.  Multiple providers cannot share one listener
# endpoint because an IKE packet does not reliably carry its original DNS name.
PROVIDER_PROFILES: dict[str, dict[str, Any]] = {
    "tim": {
        "display_name": "TIM Italy",
        "hostname": "epdg.epc.mnc001.mcc222.pub.3gppnetwork.org",
        "status": "initial-validation",
    }
}


class ConfigurationError(ValueError):
    """Unsafe or invalid relay configuration."""


@dataclass(frozen=True)
class Settings:
    enabled: bool
    provider_id: str
    allowed_source_ips: tuple[str, ...]
    upstream_ips: tuple[str, ...]
    max_sessions: int
    idle_timeout_seconds: float
    packets_per_second: float
    burst_packets: float
    max_datagram_bytes: int
    max_pending_packets: int
    max_pending_per_source: int


DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "provider_id": "tim",
    "allowed_source_ips": [],
    "upstream_ips": [],
    "max_sessions": 4,
    "idle_timeout_seconds": 180,
    "packets_per_second": 500,
    "burst_packets": 1000,
    "max_datagram_bytes": 65507,
    "max_pending_packets": 256,
    "max_pending_per_source": 64,
}


def read_settings(path: Path = OPTIONS_FILE) -> Settings:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError("options.json is unreadable or invalid") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("options must be a JSON object")
    unknown = sorted(set(raw) - set(DEFAULTS))
    if unknown:
        raise ConfigurationError(f"unknown option(s): {', '.join(unknown)}")
    values = dict(DEFAULTS)
    values.update(raw)

    provider_id = values.get("provider_id")
    if not isinstance(provider_id, str) or provider_id not in PROVIDER_PROFILES:
        raise ConfigurationError("provider_id is not a reviewed provider profile")

    sources_raw = values.get("allowed_source_ips")
    if not isinstance(sources_raw, list) or len(sources_raw) > 8:
        raise ConfigurationError("allowed_source_ips must contain at most eight exact IPv4 addresses")
    sources: list[str] = []
    for item in sources_raw:
        try:
            address = ipaddress.ip_address(str(item))
        except ValueError as exc:
            raise ConfigurationError(f"invalid source IPv4 address: {item}") from exc
        if address.version != 4 or address.is_unspecified or address.is_multicast:
            raise ConfigurationError(f"source must be one exact unicast IPv4 address: {item}")
        if str(address) not in sources:
            sources.append(str(address))

    upstreams_raw = values.get("upstream_ips")
    if not isinstance(upstreams_raw, list) or len(upstreams_raw) > 8:
        raise ConfigurationError("upstream_ips must contain at most eight exact public IPv4 addresses")
    upstreams: list[str] = []
    for item in upstreams_raw:
        try:
            address = ipaddress.ip_address(str(item))
        except ValueError as exc:
            raise ConfigurationError(f"invalid upstream IPv4 address: {item}") from exc
        if (
            address.version != 4
            or not address.is_global
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
            or address.is_loopback
            or address.is_link_local
        ):
            raise ConfigurationError(f"upstream must be one exact public IPv4 address: {item}")
        if str(address) not in upstreams:
            upstreams.append(str(address))

    def bounded_int(name: str, minimum: int, maximum: int) -> int:
        value = values.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigurationError(f"{name} must be an integer")
        parsed = value
        if parsed < minimum or parsed > maximum:
            raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
        return parsed

    enabled = values.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigurationError("enabled must be a boolean")
    if enabled and (not sources or not upstreams):
        raise ConfigurationError("enabled relay requires exact source and upstream IPv4 addresses")
    return Settings(
        enabled=enabled,
        provider_id=provider_id,
        allowed_source_ips=tuple(sources),
        upstream_ips=tuple(upstreams),
        max_sessions=bounded_int("max_sessions", 1, 32),
        idle_timeout_seconds=float(bounded_int("idle_timeout_seconds", 30, 3600)),
        packets_per_second=float(bounded_int("packets_per_second", 10, 5000)),
        burst_packets=float(bounded_int("burst_packets", 10, 10000)),
        max_datagram_bytes=bounded_int("max_datagram_bytes", 512, 65507),
        max_pending_packets=bounded_int("max_pending_packets", 8, 512),
        max_pending_per_source=bounded_int("max_pending_per_source", 4, 128),
    )


@dataclass
class TokenBucket:
    rate: float
    capacity: float
    tokens: float = field(init=False)
    updated_at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.tokens = self.capacity

    def allow(self, now: float) -> bool:
        elapsed = max(0.0, now - self.updated_at)
        self.updated_at = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True


@dataclass
class Session:
    client_ip: str
    upstream_ip: str
    last_activity: float
    client_peers: dict[int, tuple[str, int]] = field(default_factory=dict)
    upstream_transports: dict[int, asyncio.DatagramTransport] = field(default_factory=dict)
    reverse_buckets: dict[int, TokenBucket] = field(default_factory=dict)


class DownstreamProtocol(asyncio.DatagramProtocol):
    def __init__(self, relay: "Relay", service_port: int) -> None:
        self.relay = relay
        self.service_port = service_port

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.relay.enqueue_downstream(self.service_port, data, addr)

    def error_received(self, exc: Exception) -> None:
        self.relay.stats["downstream_errors"] += 1
        self.relay.last_error = f"downstream UDP error on {self.service_port}: {type(exc).__name__}"


class UpstreamProtocol(asyncio.DatagramProtocol):
    def __init__(self, relay: "Relay", session: Session, service_port: int) -> None:
        self.relay = relay
        self.session = session
        self.service_port = service_port

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.relay.handle_upstream(self.session, self.service_port, data, addr)

    def error_received(self, exc: Exception) -> None:
        self.relay.stats["upstream_errors"] += 1
        self.relay.last_error = f"upstream UDP error on {self.service_port}: {type(exc).__name__}"


class Relay:
    def __init__(
        self,
        settings: Settings,
        *,
        listen_ports: dict[int, int] | None = None,
        upstream_ports: dict[int, int] | None = None,
        bind_host: str = "0.0.0.0",
        clock: Any = time.monotonic,
    ) -> None:
        self.settings = settings
        self.listen_ports = dict(listen_ports or LISTEN_PORTS)
        self.upstream_ports = dict(upstream_ports or {500: 500, 4500: 4500})
        self.bind_host = bind_host
        self.clock = clock
        self.listeners: dict[int, asyncio.DatagramTransport] = {}
        self.sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None
        self._worker_tasks: dict[int, asyncio.Task[None]] = {}
        self._queues: dict[int, asyncio.Queue[tuple[bytes, tuple[str, int]]]] = {}
        self._pending_total = 0
        self._pending_by_source: dict[str, int] = {}
        self._admission_buckets: dict[tuple[str, int], TokenBucket] = {}
        self._reverse_global_buckets: dict[int, TokenBucket] = {
            service_port: TokenBucket(settings.packets_per_second, settings.burst_packets)
            for service_port in SERVICE_PORTS
        }
        self.accepting = False
        self.started_at: float | None = None
        self.last_error = ""
        self.stats: dict[str, int] = {
            "downstream_packets": 0,
            "upstream_packets": 0,
            "downstream_bytes": 0,
            "upstream_bytes": 0,
            "dropped_source": 0,
            "dropped_inactive": 0,
            "dropped_rate": 0,
            "dropped_oversize": 0,
            "dropped_capacity": 0,
            "dropped_no_peer": 0,
            "dropped_wrong_upstream": 0,
            "dropped_stale_session": 0,
            "dropped_peer_change": 0,
            "dropped_queue": 0,
            "dropped_reverse_rate": 0,
            "downstream_errors": 0,
            "upstream_errors": 0,
            "sessions_created": 0,
            "sessions_expired": 0,
        }

    async def start(self) -> None:
        if not self.settings.enabled or self.listeners:
            return
        loop = asyncio.get_running_loop()
        self._start_workers()
        try:
            for service_port in SERVICE_PORTS:
                transport, _ = await loop.create_datagram_endpoint(
                    lambda port=service_port: DownstreamProtocol(self, port),
                    local_addr=(self.bind_host, self.listen_ports[service_port]),
                    family=socket.AF_INET,
                )
                self.listeners[service_port] = transport
        except Exception:
            await self.stop()
            raise
        self.started_at = self.clock()
        self.accepting = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="relay-cleanup")

    def _start_workers(self) -> None:
        if self._worker_tasks:
            return
        per_queue = max(4, (self.settings.max_pending_packets + 1) // 2)
        for service_port in SERVICE_PORTS:
            self._queues[service_port] = asyncio.Queue(maxsize=per_queue)
            self._worker_tasks[service_port] = asyncio.create_task(
                self._worker(service_port), name=f"relay-udp-{service_port}"
            )

    def _admit(self, service_port: int, data: bytes, addr: tuple[str, int]) -> bool:
        now = self.clock()
        client_ip = addr[0]
        if not self.accepting:
            self.stats["dropped_inactive"] += 1
            return False
        if service_port not in SERVICE_PORTS or client_ip not in self.settings.allowed_source_ips:
            self.stats["dropped_source"] += 1
            return False
        if len(data) > self.settings.max_datagram_bytes:
            self.stats["dropped_oversize"] += 1
            return False
        bucket = self._admission_buckets.get((client_ip, service_port))
        if bucket is None:
            bucket = TokenBucket(self.settings.packets_per_second, self.settings.burst_packets)
            self._admission_buckets[(client_ip, service_port)] = bucket
        if not bucket.allow(now):
            self.stats["dropped_rate"] += 1
            return False
        if (
            self._pending_total >= self.settings.max_pending_packets
            or self._pending_by_source.get(client_ip, 0) >= self.settings.max_pending_per_source
        ):
            self.stats["dropped_queue"] += 1
            return False
        self._pending_total += 1
        self._pending_by_source[client_ip] = self._pending_by_source.get(client_ip, 0) + 1
        return True

    def _release_pending(self, client_ip: str) -> None:
        self._pending_total = max(0, self._pending_total - 1)
        remaining = self._pending_by_source.get(client_ip, 0) - 1
        if remaining > 0:
            self._pending_by_source[client_ip] = remaining
        else:
            self._pending_by_source.pop(client_ip, None)

    def enqueue_downstream(self, service_port: int, data: bytes, addr: tuple[str, int]) -> None:
        if not self._admit(service_port, data, addr):
            return
        queue = self._queues.get(service_port)
        if queue is None:
            self._release_pending(addr[0])
            self.stats["dropped_inactive"] += 1
            return
        try:
            queue.put_nowait((data, addr))
        except asyncio.QueueFull:
            self._release_pending(addr[0])
            self.stats["dropped_queue"] += 1

    async def _worker(self, service_port: int) -> None:
        queue = self._queues[service_port]
        try:
            while True:
                data, addr = await queue.get()
                try:
                    await self._process_downstream(service_port, data, addr)
                finally:
                    self._release_pending(addr[0])
                    queue.task_done()
        except asyncio.CancelledError:
            return

    def _choose_upstream(self, client_ip: str) -> str:
        packed = int(ipaddress.ip_address(client_ip))
        return self.settings.upstream_ips[packed % len(self.settings.upstream_ips)]

    def _new_session(self, client_ip: str, now: float) -> Session | None:
        if len(self.sessions) >= self.settings.max_sessions:
            self.stats["dropped_capacity"] += 1
            return None
        session = Session(client_ip, self._choose_upstream(client_ip), now)
        for service_port in SERVICE_PORTS:
            session.reverse_buckets[service_port] = TokenBucket(
                self.settings.packets_per_second, self.settings.burst_packets
            )
        self.sessions[client_ip] = session
        self.stats["sessions_created"] += 1
        return session

    async def _ensure_upstream(self, session: Session, service_port: int) -> asyncio.DatagramTransport:
        existing = session.upstream_transports.get(service_port)
        if existing and not existing.is_closing():
            return existing
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: UpstreamProtocol(self, session, service_port),
            remote_addr=(session.upstream_ip, self.upstream_ports[service_port]),
            family=socket.AF_INET,
        )
        session.upstream_transports[service_port] = transport
        return transport

    async def handle_downstream(
        self, service_port: int, data: bytes, addr: tuple[str, int]
    ) -> None:
        if not self._admit(service_port, data, addr):
            return
        try:
            await self._process_downstream(service_port, data, addr)
        finally:
            self._release_pending(addr[0])

    async def _process_downstream(
        self, service_port: int, data: bytes, addr: tuple[str, int]
    ) -> None:
        now = self.clock()
        client_ip = addr[0]
        async with self._lock:
            session = self.sessions.get(client_ip) or self._new_session(client_ip, now)
            if session is None:
                return
            prior_peer = session.client_peers.get(service_port)
            if prior_peer is not None and prior_peer != addr:
                self.stats["dropped_peer_change"] += 1
                return
            session.client_peers[service_port] = addr
            session.last_activity = now
        try:
            upstream = await self._ensure_upstream(session, service_port)
            if not self.accepting or self.sessions.get(client_ip) is not session:
                self.stats["dropped_stale_session"] += 1
                return
            upstream.sendto(data)
        except Exception as exc:
            self.stats["upstream_errors"] += 1
            self.last_error = f"could not send upstream on {service_port}: {type(exc).__name__}"
            return
        self.stats["downstream_packets"] += 1
        self.stats["downstream_bytes"] += len(data)

    def handle_upstream(
        self, session: Session, service_port: int, data: bytes, addr: tuple[str, int]
    ) -> None:
        if not self.accepting or self.sessions.get(session.client_ip) is not session:
            self.stats["dropped_stale_session"] += 1
            return
        expected = (session.upstream_ip, self.upstream_ports[service_port])
        if addr[0] != expected[0] or addr[1] != expected[1]:
            self.stats["dropped_wrong_upstream"] += 1
            return
        peer = session.client_peers.get(service_port)
        listener = self.listeners.get(service_port)
        if not peer or not listener or listener.is_closing():
            self.stats["dropped_no_peer"] += 1
            return
        if len(data) > self.settings.max_datagram_bytes:
            self.stats["dropped_oversize"] += 1
            return
        now = self.clock()
        if not self._reverse_global_buckets[service_port].allow(now):
            self.stats["dropped_reverse_rate"] += 1
            return
        if not session.reverse_buckets[service_port].allow(now):
            self.stats["dropped_reverse_rate"] += 1
            return
        listener.sendto(data, peer)
        session.last_activity = now
        self.stats["upstream_packets"] += 1
        self.stats["upstream_bytes"] += len(data)

    async def expire_idle(self) -> None:
        now = self.clock()
        async with self._lock:
            expired = [
                client_ip
                for client_ip, session in self.sessions.items()
                if now - session.last_activity >= self.settings.idle_timeout_seconds
            ]
            for client_ip in expired:
                session = self.sessions.pop(client_ip)
                for transport in session.upstream_transports.values():
                    transport.close()
                self.stats["sessions_expired"] += 1

    async def _cleanup_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(min(10.0, max(1.0, self.settings.idle_timeout_seconds / 3)))
                await self.expire_idle()
        except asyncio.CancelledError:
            return

    async def stop(self) -> None:
        self.accepting = False
        for transport in self.listeners.values():
            transport.close()
        self.listeners.clear()
        for task in self._worker_tasks.values():
            task.cancel()
        for task in self._worker_tasks.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._worker_tasks.clear()
        for queue in self._queues.values():
            while not queue.empty():
                try:
                    _, addr = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._release_pending(addr[0])
                queue.task_done()
        self._queues.clear()
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        async with self._lock:
            for session in self.sessions.values():
                for transport in session.upstream_transports.values():
                    transport.close()
            self.sessions.clear()
            self._pending_total = 0
            self._pending_by_source.clear()
            self._admission_buckets.clear()

    def status(self) -> dict[str, Any]:
        return {
            "app_version": APP_VERSION,
            "enabled": self.settings.enabled,
            "listening": bool(self.listeners),
            "provider": {
                "id": self.settings.provider_id,
                **PROVIDER_PROFILES[self.settings.provider_id],
            },
            "services": list(SERVICE_PORTS),
            "allowed_source_count": len(self.settings.allowed_source_ips),
            "upstream_ips": list(self.settings.upstream_ips),
            "active_sessions": len(self.sessions),
            "pending_packets": self._pending_total,
            "max_sessions": self.settings.max_sessions,
            "started_at_monotonic": self.started_at,
            "last_error": self.last_error,
            "stats": dict(self.stats),
            "truth": {
                "relay": "Byte-preserving UDP transport only",
                "upstream": "Pinned configured TIM IP; no DNS or certificate proof",
                "registration": "Requires physical handset TIM Wi-Fi Call status",
            },
        }


async def http_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, relay: Relay) -> None:
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=3)
        if len(request_line) > 4096:
            raise ValueError("request line too large")
        method, path, _ = request_line.decode("ascii", "replace").strip().split(" ", 2)
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=3)
            if line in (b"\r\n", b"\n", b""):
                break
            if len(line) > 8192:
                raise ValueError("header too large")
        if method != "GET" or path not in ("/", "/api/status", "/api/health"):
            code, reason, payload = 404, "Not Found", {"error": "not found"}
        else:
            code, reason, payload = 200, "OK", relay.status()
        body = json.dumps(payload, separators=(",", ":")).encode()
        writer.write(
            f"HTTP/1.1 {code} {reason}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
        )
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def main() -> None:
    settings = read_settings()
    relay = Relay(settings)
    await relay.start()
    server = await asyncio.start_server(
        lambda reader, writer: http_handler(reader, writer, relay), "0.0.0.0", HTTP_PORT
    )
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass
    await stop_event.wait()
    server.close()
    await server.wait_closed()
    await relay.stop()


if __name__ == "__main__":
    asyncio.run(main())
