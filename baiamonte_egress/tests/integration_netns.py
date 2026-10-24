#!/usr/bin/env python3
"""Packet-level fail-closed checks inside an ephemeral privileged container."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


ROOT = Path(tempfile.mkdtemp(prefix="baiamonte-netns-"))
os.environ["EGRESS_DATA_DIR"] = str(ROOT / "data")
os.environ["EGRESS_OPTIONS_FILE"] = str(ROOT / "options.json")
os.environ["EGRESS_PROFILE_FILE"] = str(ROOT / "client.ovpn")
os.environ["EGRESS_RUNTIME_PROFILE_FILE"] = str(ROOT / "runtime.ovpn")

SPEC = importlib.util.spec_from_file_location("manager", Path(__file__).parents[1] / "manager.py")
manager_module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(manager_module)


def run(command, check=True):
    return subprocess.run(command, check=check, text=True, capture_output=True, timeout=15)


def ip(*args, check=True):
    return run(["ip", *args], check=check)


SERVER = r"""
import socket,sys,time
address=sys.argv[1]; port=int(sys.argv[2]); deadline=time.time()+20
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind((address,port)); s.settimeout(.2)
while time.time()<deadline:
    try:
        data,peer=s.recvfrom(2048); s.sendto(data,peer)
    except socket.timeout: pass
"""

CLIENT = r"""
import socket,sys
source,destination,port=sys.argv[1],sys.argv[2],int(sys.argv[3])
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind((source,0)); s.settimeout(.35)
s.sendto(b'probe',(destination,port))
try: print('PASS' if s.recvfrom(64)[0]==b'probe' else 'BAD')
except socket.timeout: print('DROP')
"""

FLOOD_CLIENT = r"""
import socket,sys,time
source,destination,port=sys.argv[1],sys.argv[2],int(sys.argv[3]); successes=0
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind((source,0)); s.settimeout(.03)
for _ in range(80):
    s.sendto(b'denied',(destination,port))
    try:
        if s.recvfrom(64)[0]==b'denied': successes+=1
    except socket.timeout: pass
    time.sleep(.01)
print(successes)
"""


def ns_python(namespace, source, *args):
    return run(["ip", "netns", "exec", namespace, "python3", "-c", source, *args], check=False)


def assert_delivery(source_ip, destination_ip, port, expected):
    result = ns_python("srcns", CLIENT, source_ip, destination_ip, str(port))
    actual = result.stdout.strip()
    if actual != expected:
        raise AssertionError(f"expected {expected}, got {actual!r}; stderr={result.stderr!r}")


def setup_network():
    ip("netns", "add", "srcns")
    ip("netns", "add", "dstns")
    ip("link", "add", "tun9", "type", "veth", "peer", "name", "src9")
    ip("link", "set", "src9", "netns", "srcns")
    ip("addr", "add", "10.200.0.1/24", "dev", "tun9")
    ip("link", "set", "tun9", "up")
    ip("netns", "exec", "srcns", "ip", "link", "set", "lo", "up")
    ip("netns", "exec", "srcns", "ip", "addr", "add", "10.200.0.2/24", "dev", "src9")
    ip("netns", "exec", "srcns", "ip", "addr", "add", "10.200.0.3/24", "dev", "src9")
    ip("netns", "exec", "srcns", "ip", "link", "set", "src9", "up")
    ip("netns", "exec", "srcns", "ip", "route", "add", "default", "via", "10.200.0.1")

    ip("link", "add", "wan9", "type", "veth", "peer", "name", "dst9")
    ip("link", "set", "dst9", "netns", "dstns")
    ip("addr", "add", "198.51.100.1/24", "dev", "wan9")
    ip("link", "set", "wan9", "up")
    ip("netns", "exec", "dstns", "ip", "link", "set", "lo", "up")
    ip("netns", "exec", "dstns", "ip", "addr", "add", "198.51.100.2/24", "dev", "dst9")
    ip("netns", "exec", "dstns", "ip", "addr", "add", "198.51.100.3/24", "dev", "dst9")
    ip("netns", "exec", "dstns", "ip", "link", "set", "dst9", "up")
    ip("netns", "exec", "dstns", "ip", "route", "add", "default", "via", "198.51.100.1")
    Path("/proc/sys/net/ipv4/ip_forward").write_text("1\n")
    run(["iptables", "-P", "FORWARD", "ACCEPT"])


def cleanup_network(manager):
    try:
        manager.cleanup_firewall()
    except Exception:
        pass
    ip("netns", "del", "srcns", check=False)
    ip("netns", "del", "dstns", check=False)


def main():
    setup_network()
    server_4500 = subprocess.Popen(["ip", "netns", "exec", "dstns", "python3", "-c", SERVER, "198.51.100.2", "4500"])
    server_53 = subprocess.Popen(["ip", "netns", "exec", "dstns", "python3", "-c", SERVER, "198.51.100.2", "53"])
    server_wrong_destination = subprocess.Popen(["ip", "netns", "exec", "dstns", "python3", "-c", SERVER, "198.51.100.3", "4500"])
    time.sleep(.2)
    Path(os.environ["EGRESS_OPTIONS_FILE"]).write_text(json.dumps({
        "mode": "probe_only",
        "allowed_source_cidrs": ["10.200.0.2/32"],
    }))
    gateway = manager_module.GatewayManager()
    try:
        gateway.settings = {
            **gateway.settings,
            "mode": "tim_egress",
            "wan_interface": "wan9",
            "allowed_source_cidrs": ["10.200.0.2/32"],
            "allowed_udp_ports": [500, 4500],
            "tim_destination_ips": ["198.51.100.2"],
            "nat_enabled": True,
        }
        gateway.establish_guard()
        gateway.session_ready = True

        # Baseline has FORWARD ACCEPT and ip_forward=1, so only the app guard can block this.
        assert_delivery("10.200.0.2", "198.51.100.2", 4500, "DROP")

        denied_during_transition = subprocess.Popen(
            ["ip", "netns", "exec", "srcns", "python3", "-c", FLOOD_CLIENT,
             "10.200.0.2", "198.51.100.2", "53"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        gateway.apply_firewall()
        gateway.deactivate_forwarding()
        denied_count = int(denied_during_transition.communicate(timeout=10)[0].strip())
        if denied_count != 0:
            raise AssertionError(f"denied port passed {denied_count} packets during transitions")

        gateway.apply_firewall()
        assert_delivery("10.200.0.2", "198.51.100.2", 4500, "PASS")
        assert_delivery("10.200.0.2", "198.51.100.2", 53, "DROP")
        assert_delivery("10.200.0.3", "198.51.100.2", 4500, "DROP")
        assert_delivery("10.200.0.2", "198.51.100.3", 4500, "DROP")

        gateway.deactivate_forwarding()
        assert_delivery("10.200.0.2", "198.51.100.2", 4500, "DROP")
        gateway.cleanup_firewall()
        rules = run(["iptables", "-S", "FORWARD"]).stdout
        if manager_module.GUARD_CHAIN in rules or manager_module.FWD_CHAIN in rules:
            raise AssertionError("app-owned FORWARD references remained after cleanup")
        print("packet integration: PASS")
    finally:
        for process in (server_4500, server_53, server_wrong_destination):
            process.terminate()
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: process.kill()
        cleanup_network(gateway)


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("integration test requires root inside an isolated container")
    main()
