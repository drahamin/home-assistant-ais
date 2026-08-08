#!/usr/bin/env python3
"""Read an attached NMEA USB GPS and publish the latest valid position."""

import argparse
import glob
import json
import os
import select
import termios
import time
from pathlib import Path

PREFERRED_HINTS = ("gps", "gnss", "ublox", "u-blox", "nmea")
BAUD_RATES = {
    4800: termios.B4800, 9600: termios.B9600, 19200: termios.B19200,
    38400: termios.B38400, 57600: termios.B57600, 115200: termios.B115200,
}


def candidates(requested):
    if requested and requested.lower() != "auto":
        return [requested]
    by_id = sorted(glob.glob("/dev/serial/by-id/*"))
    preferred = [path for path in by_id if any(hint in path.lower() for hint in PREFERRED_HINTS)]
    generic = sorted(glob.glob("/dev/ttyACM*")) + sorted(glob.glob("/dev/ttyUSB*"))
    return preferred + [path for path in by_id + generic if path not in preferred]


def configure(fd, baud):
    attributes = termios.tcgetattr(fd)
    attributes[0] = attributes[1] = attributes[3] = 0
    attributes[2] = termios.CLOCAL | termios.CREAD | termios.CS8
    attributes[4] = attributes[5] = BAUD_RATES.get(baud, termios.B9600)
    attributes[6][termios.VMIN] = 0
    attributes[6][termios.VTIME] = 10
    termios.tcsetattr(fd, termios.TCSANOW, attributes)
    termios.tcflush(fd, termios.TCIFLUSH)


def checksum_valid(sentence):
    if not sentence.startswith("$") or "*" not in sentence:
        return False
    body, supplied = sentence[1:].split("*", 1)
    checksum = 0
    for character in body:
        checksum ^= ord(character)
    try:
        return checksum == int(supplied[:2], 16)
    except ValueError:
        return False


def coordinate(value, hemisphere):
    raw = float(value)
    degrees = int(raw // 100)
    result = degrees + (raw - degrees * 100) / 60
    return -result if hemisphere in {"S", "W"} else result


def parse_sentence(sentence, previous_altitude=None):
    sentence = sentence.strip()
    if not checksum_valid(sentence):
        return None
    fields = sentence[1:sentence.index("*")].split(",")
    message_type = fields[0][-3:]
    try:
        if message_type == "GGA" and len(fields) > 9 and int(fields[6] or 0) > 0:
            return {"lat": coordinate(fields[2], fields[3]), "lon": coordinate(fields[4], fields[5]),
                    "alt": float(fields[9]) if fields[9] else previous_altitude,
                    "satellites": int(fields[7] or 0), "quality": int(fields[6])}
        if message_type == "RMC" and len(fields) > 8 and fields[2] == "A":
            return {"lat": coordinate(fields[3], fields[4]), "lon": coordinate(fields[5], fields[6]),
                    "alt": previous_altitude, "speed_knots": float(fields[7] or 0),
                    "track": float(fields[8] or 0), "quality": 1}
    except (ValueError, IndexError):
        return None
    return None


def write_fix(output, fix, device):
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(fix, device=device, timestamp=time.time()), separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, output)


def watch_device(device, baud, output):
    fd = os.open(device, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        configure(fd, baud)
        buffer, altitude, first_fix = b"", None, False
        started = time.monotonic()
        print("Baiamonte GPS reading %s at %s baud" % (device, baud), flush=True)
        while True:
            if not first_fix and time.monotonic() - started > 10:
                raise OSError("no valid NMEA fix detected")
            readable, _, _ = select.select([fd], [], [], 2)
            if not readable:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                raise OSError("GPS device disconnected")
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                fix = parse_sentence(raw.decode("ascii", errors="ignore"), altitude)
                if fix:
                    altitude = fix.get("alt", altitude)
                    write_fix(output, fix, device)
                    first_fix = True
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="auto")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--output", type=Path, default=Path("/run/baiamonte/gps.json"))
    args = parser.parse_args()
    while True:
        found = False
        for device in candidates(args.device):
            if not Path(device).exists():
                continue
            found = True
            try:
                watch_device(device, args.baud, args.output)
            except (OSError, termios.error) as error:
                print("Baiamonte GPS could not use %s: %s" % (device, error), flush=True)
        if not found:
            print("Baiamonte GPS waiting for a USB serial receiver", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    main()
