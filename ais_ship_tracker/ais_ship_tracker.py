import json
import math
import time
import os
import mimetypes
import socket
import re
from functools import lru_cache
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.request
import urllib.error
import urllib.parse
import signal
import sys
import threading
import subprocess
import secrets
import glob
import select
import termios
import fcntl
from datetime import datetime, timedelta, timezone
from pyais import decode as decode_ais_nmea
from pyais.exceptions import AISBaseException

try:
    from global_land_mask import globe as global_land_globe
except ImportError:
    global_land_globe = None

print("🚀 Starting Baiamonte AIS...", flush=True)
VERSION = "2.7.31"
receiver_logs = deque(maxlen=80)

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

BAIAMONTE_BOUNDS = {
    "latitude_south": 35.85,
    "longitude_west": 12.93,
    "latitude_north": 39.85,
    "longitude_east": 16.93,
}
LEGACY_TEST_BOUNDS = {
    "latitude_south": 50.90,
    "longitude_west": 1.20,
    "latitude_north": 51.20,
    "longitude_east": 1.80,
}
WEATHER_TILE_PATTERN = re.compile(
    r"v2/radar/[A-Za-z0-9_-]+/256/\d+/\d+/\d+/\d+/[\d_]+\.png"
)


def valid_weather_tile_path(path):
    return WEATHER_TILE_PATTERN.fullmatch(path) is not None

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    receiver_logs.appendleft({"time": datetime.now().isoformat(timespec="seconds"), "message": str(message)})
    try:
        print(f"[{timestamp}] {message}", flush=True)
    except UnicodeEncodeError:
        # Fallback for Docker environments that don't support UTF-8 emojis
        print(f"[{timestamp}] {message.encode('ascii', 'ignore').decode('ascii')}", flush=True)

watchlist_mmsis = []

try:
    # --- Load Configuration from Home Assistant UI ---
    options_path = os.environ.get("BAIAMONTE_AIS_OPTIONS", "/data/options.json")
    with open(options_path) as f:
        config = json.load(f)

    def get_safe_int(key, default):
        val = config.get(key)
        if val is None or val == "": return default
        try: return int(val)
        except: return default

    # AISHub is reciprocal: contributors send raw NMEA data and receive access
    # to the aggregated vessel API.
    AISHUB_DATA_SOURCE = str(config.get('aishub_data_source', 'rahamin_proxy')).strip().lower()
    if AISHUB_DATA_SOURCE not in {'rahamin_proxy', 'direct_aishub'}:
        AISHUB_DATA_SOURCE = 'rahamin_proxy'
    AISHUB_USERNAME = str(config.get('aishub_username', '')).strip()
    AISHUB_API_URL = str(config.get('aishub_api_url', 'https://data.aishub.net/ws.php')).strip()
    AISHUB_POLL_INTERVAL = max(60, get_safe_int('aishub_poll_interval', 60))
    AISHUB_FEED_HOST = str(config.get('aishub_feed_host', '')).strip()
    AISHUB_FEED_PORT = get_safe_int('aishub_feed_port', 0)
    AISHUB_SHARING_ENABLED = str(config.get('aishub_sharing_enabled', False)).lower() in ['true', '1', 't', 'y', 'yes']
    BAIAMONTE_API_ENABLED = str(config.get('baiamonte_api_enabled', True)).lower() in ['true', '1', 't', 'y', 'yes']
    RAHAMIN_MIAMI_ENABLED = str(config.get('rahamin_miami_enabled', True)).lower() in ['true', '1', 't', 'y', 'yes']
    RAHAMIN_PROXY_ENABLED = AISHUB_DATA_SOURCE == 'rahamin_proxy'
    RAHAMIN_PROXY_URL = str(config.get('rahamin_proxy_url', 'http://192.168.86.196:8999/api/status')).strip()
    RAHAMIN_PROXY_INTERVAL = max(10, min(300, get_safe_int('rahamin_proxy_interval', 15)))
    MIAMI_APPROACH_KM = max(5, min(200, get_safe_int('miami_approach_km', 45)))
    BAIAMONTE_APPROACH_KM = max(5, min(200, get_safe_int('baiamonte_approach_km', 45)))
    DEFAULT_MAP_AREA = str(config.get('default_map_area', 'baiamonte')).strip().lower()
    if DEFAULT_MAP_AREA not in {'baiamonte', 'miami'}:
        DEFAULT_MAP_AREA = 'baiamonte'
    TV_DEFAULT_MAP_AREA = str(config.get('tv_default_map_area', 'baiamonte')).strip().lower()
    if TV_DEFAULT_MAP_AREA not in {'baiamonte', 'miami'}:
        TV_DEFAULT_MAP_AREA = 'baiamonte'
    DASHBOARD_MAP_VESSELS = str(config.get('dashboard_map_vessels', True)).lower() in ['true', '1', 't', 'y', 'yes']
    TV_MAP_VESSELS = str(config.get('tv_map_vessels', True)).lower() in ['true', '1', 't', 'y', 'yes']
    TV_LIVE_TRAFFIC_ONLY = str(config.get('tv_live_traffic_only', True)).lower() in ['true', '1', 't', 'y', 'yes']
    TV_TARGET_SIZE = max(30, min(180, get_safe_int('tv_target_size', 100)))
    RECEIVER_UDP_PORT = 10110
    RECEIVER_NAME = str(config.get('receiver_name', 'Baiamonte AIS receiver')).strip() or 'Baiamonte AIS receiver'
    RECEIVER_MODE = str(config.get('receiver_mode', 'sdr')).strip().lower()
    if RECEIVER_MODE not in {'sdr', 'udp', 'tcp', 'serial'}:
        RECEIVER_MODE = 'sdr'
    RECEIVER_HOST = str(config.get('receiver_host', '')).strip()
    RECEIVER_PORT = max(1, min(65535, get_safe_int('receiver_port', 10110)))
    RECEIVER_SERIAL_DEVICE = str(config.get('receiver_serial_device', 'auto')).strip() or 'auto'
    RECEIVER_SERIAL_BAUD = get_safe_int('receiver_serial_baud', 38400)
    RECEIVER_CHANNEL = str(config.get('receiver_channel', 'dual')).strip().lower()
    if RECEIVER_CHANNEL not in {'dual', 'channel_a', 'channel_b'}:
        RECEIVER_CHANNEL = 'dual'
    SDR_DEVICE = str(config.get('sdr_device', '0')).strip() or '0'
    SDR_GAIN = str(config.get('sdr_gain', 'auto')).strip().lower() or 'auto'
    if SDR_GAIN != 'auto':
        try:
            SDR_GAIN = str(max(0.0, min(50.0, float(SDR_GAIN))))
        except ValueError:
            SDR_GAIN = 'auto'
    SDR_PPM = max(-150, min(150, get_safe_int('sdr_ppm', 0)))
    SDR_RTL_AGC = str(config.get('sdr_rtl_agc', True)).lower() in ['true', '1', 't', 'y', 'yes']
    SDR_BIAS_TEE = str(config.get('sdr_bias_tee', False)).lower() in ['true', '1', 't', 'y', 'yes']
    SDR_BANDWIDTH = str(config.get('sdr_bandwidth', '192K')).strip().upper()
    if SDR_BANDWIDTH not in {'OFF', '192K', '288K'}:
        SDR_BANDWIDTH = '192K'
    MARINE_VHF_ENABLED = str(config.get('marine_vhf_enabled', False)).lower() in ['true', '1', 't', 'y', 'yes']
    MARINE_VHF_DEVICE = str(config.get('marine_vhf_device', '1')).strip() or '1'
    MARINE_VHF_USB_RESET_ENABLED = str(config.get('marine_vhf_usb_reset_enabled', False)).lower() in ['true', '1', 't', 'y', 'yes']
    MARINE_VHF_AUTO_USB_RESET = str(config.get('marine_vhf_auto_usb_reset', False)).lower() in ['true', '1', 't', 'y', 'yes']
    MARINE_VHF_USB_RESET_ATTEMPTS = max(0, min(5, get_safe_int('marine_vhf_usb_reset_attempts', 2)))
    try:
        MARINE_VHF_GAIN = max(0.0, min(50.0, float(config.get('marine_vhf_gain', 28))))
    except (TypeError, ValueError):
        MARINE_VHF_GAIN = 28.0
    MARINE_VHF_PPM = max(-150, min(150, get_safe_int('marine_vhf_ppm', 0)))
    MARINE_VHF_AUTO_SQUELCH = str(config.get('marine_vhf_auto_squelch', True)).lower() in ['true', '1', 't', 'y', 'yes']
    MARINE_VHF_SQUELCH = get_safe_int('marine_vhf_squelch', -28)
    if MARINE_VHF_SQUELCH > 0:
        MARINE_VHF_SQUELCH = -MARINE_VHF_SQUELCH
    MARINE_VHF_SQUELCH = max(-100, min(0, MARINE_VHF_SQUELCH))
    marine_frequency_values = []
    for item in str(config.get('marine_vhf_frequencies', '156.800,156.450,156.600,156.700,156.625')).split(','):
        try:
            frequency = float(item.strip())
        except ValueError:
            continue
        if 156.0 <= frequency <= 163.0 and len(marine_frequency_values) < 12:
            marine_frequency_values.append(frequency)
    if not marine_frequency_values:
        marine_frequency_values = [156.800]
    MARINE_VHF_FREQUENCIES = marine_frequency_values
    marine_labels = [item.strip()[:60] for item in str(config.get('marine_vhf_labels', '')).split(',')]
    MARINE_VHF_CHANNELS = [
        {
            "frequency": f"{frequency:.3f}",
            "label": marine_labels[index] if index < len(marine_labels) and marine_labels[index] else f"Channel {index + 1}",
        }
        for index, frequency in enumerate(MARINE_VHF_FREQUENCIES)
    ]
    GPS_USE_USB = str(config.get('GPS_USE_USB', True)).lower() in ['true', '1', 't', 'y', 'yes']
    GPS_DEVICE = str(config.get('GPS_DEVICE', 'auto')).strip() or 'auto'
    GPS_BAUD = get_safe_int('GPS_BAUD', 9600)
    WEATHER_OVERLAY_DASHBOARD = str(config.get('WEATHER_OVERLAY_DASHBOARD', True)).lower() in ['true', '1', 't', 'y', 'yes']
    FLIGHTAWARE_WEATHER_ENABLED = str(config.get('FLIGHTAWARE_WEATHER_ENABLED', False)).lower() in ['true', '1', 't', 'y', 'yes']
    FLIGHTAWARE_AEROAPI_KEY = str(config.get('FLIGHTAWARE_AEROAPI_KEY', '')).strip()
    FLIGHTAWARE_AIRPORT = re.sub(r'[^A-Z0-9]', '', str(config.get('FLIGHTAWARE_AIRPORT', 'LICC')).upper())[:4] or 'LICC'
    weather_val = config.get('tv_weather_overlay', False)
    TV_WEATHER_OVERLAY = str(weather_val).lower() in ['true', '1', 't', 'y', 'yes'] if weather_val is not None else False
    TV_WEATHER_OPACITY = max(10, min(100, get_safe_int('tv_weather_opacity', 65)))
    MAP_STYLE = str(config.get('map_style', 'standard')).strip().lower()
    if MAP_STYLE not in {'standard', 'humanitarian', 'topographic', 'dark', 'satellite'}:
        MAP_STYLE = 'standard'
    configured_bounds = {
        "latitude_south": float(config.get('latitude_south', BAIAMONTE_BOUNDS["latitude_south"])),
        "longitude_west": float(config.get('longitude_west', BAIAMONTE_BOUNDS["longitude_west"])),
        "latitude_north": float(config.get('latitude_north', BAIAMONTE_BOUNDS["latitude_north"])),
        "longitude_east": float(config.get('longitude_east', BAIAMONTE_BOUNDS["longitude_east"])),
    }
    # Version 2.2.0 inherited an upstream UK example box. Existing installations
    # that still have those exact defaults should follow the estate automatically.
    if configured_bounds == LEGACY_TEST_BOUNDS:
        configured_bounds = BAIAMONTE_BOUNDS.copy()
        log("📍 Replaced the legacy test watch area with the Baiamonte/Sicily watch area")
    lat_south = configured_bounds["latitude_south"]
    lon_west = configured_bounds["longitude_west"]
    lat_north = configured_bounds["latitude_north"]
    lon_east = configured_bounds["longitude_east"]
    BOUNDING_BOX = [[[lat_south, lon_west], [lat_north, lon_east]]]
    MIAMI_BOUNDS = {
        "south": float(config.get('miami_latitude_south', 25.55)),
        "west": float(config.get('miami_longitude_west', -80.45)),
        "north": float(config.get('miami_latitude_north', 26.15)),
        "east": float(config.get('miami_longitude_east', -79.85)),
    }
    MAP_AREAS = {
        "baiamonte": {
            "id": "baiamonte", "name": "Baiamonte Sicily", "station": "Baiamonte AIS",
            "enabled": BAIAMONTE_API_ENABLED,
            "bounds": {"south": lat_south, "west": lon_west, "north": lat_north, "east": lon_east},
            "approach_km": BAIAMONTE_APPROACH_KM,
        },
        "miami": {
            "id": "miami", "name": "Rahamin Miami", "station": "Rahamin AIS Miami",
            "enabled": RAHAMIN_MIAMI_ENABLED, "bounds": MIAMI_BOUNDS,
            "approach_km": MIAMI_APPROACH_KM,
        },
    }
    
    dev_val = config.get('dev_mode', False)
    DEV_MODE = str(dev_val).lower() in ['true', '1', 't', 'y', 'yes'] if dev_val is not None else False

    # Version 1.2.0 Additions
    map_val = config.get('enable_map_entities', False)
    ENABLE_MAP_ENTITIES = str(map_val).lower() in ['true', '1', 't', 'y', 'yes'] if map_val is not None else False
    
    MAP_TIMEOUT_MINUTES = get_safe_int('map_timeout_minutes', 30)
    
    clear_val = config.get('clear_map_on_startup', False)
    CLEAR_MAP_ON_STARTUP = str(clear_val).lower() in ['true', '1', 't', 'y', 'yes'] if clear_val is not None else False
    
    class_b_val = config.get('include_class_b', True)
    INCLUDE_CLASS_B = str(class_b_val).lower() in ['true', '1', 't', 'y', 'yes'] if class_b_val is not None else True

    vessel_watchlist_raw = config.get('vessel_watchlist', '')
    if vessel_watchlist_raw:
        for item in vessel_watchlist_raw.split(','):
            if not item.strip():
                continue
            # Sanitisation: strip all whitespace
            cleaned = "".join(item.split())
            # Remove any non-numeric characters
            numeric_only = "".join(c for c in cleaned if c.isdigit())
            # Validation
            if len(numeric_only) == 9:
                watchlist_mmsis.append(numeric_only)
            else:
                log(f"⚠️ Invalid MMSIs in filter box ignored: '{item.strip()}' (Must be 9 digits)")

except Exception as e:
    print(f"❌ FATAL ERROR loading configuration: {e}", flush=True)
    import sys
    sys.exit(1)

# Home Assistant API Configuration
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
ENTITY_PREFIX = "baiamonte_ais"
API_URL = f"http://supervisor/core/api/states/sensor.{ENTITY_PREFIX}_last_passing_ship_dev" if DEV_MODE else f"http://supervisor/core/api/states/sensor.{ENTITY_PREFIX}_last_passing_ship"

# Dictionaries to track ships and rate limits
seen_ships = {}
last_map_update = {}
static_ship_data = {}
last_purge_time = datetime.now()
last_known_error = ""
current_conn_status = "Disconnected"
shutdown_in_progress = False
ais_catcher_process = None
ais_catcher_lock = threading.Lock()
marine_vhf_process = None
marine_icecast_process = None
marine_vhf_lock = threading.Lock()
receiver_ready = threading.Event()
nmea_fragment_buffer = {}

# Read-only state used by the Home Assistant ingress dashboard.
dashboard_vessels = {}
dashboard_events = deque(maxlen=40)
dashboard_lock = threading.RLock()
position_filter_state = {
    "land_mask": "enabled" if global_land_globe is not None else "unavailable",
    "rejected_total": 0,
    "rejected_by_reason": {},
    "last_rejected": None,
}
DASHBOARD_ROOT = Path(__file__).resolve().parent / "web"
aishub_state = {
    "state": "Starting",
    "last_checked": None,
    "last_success": None,
    "records": 0,
    "error": None,
}
aishub_area_states = {
    area_id: {"state": "Waiting", "last_checked": None, "last_success": None, "records": 0, "error": None}
    for area_id, area in MAP_AREAS.items() if area["enabled"]
}
rahamin_proxy_state = {
    "enabled": RAHAMIN_PROXY_ENABLED and any(area["enabled"] for area in MAP_AREAS.values()),
    "state": "Waiting" if RAHAMIN_PROXY_ENABLED and any(area["enabled"] for area in MAP_AREAS.values()) else "Disabled",
    "url": RAHAMIN_PROXY_URL,
    "last_checked": None,
    "last_success": None,
    "records": 0,
    "error": None,
    "areas": {
        area_id: {"state": "Waiting", "last_checked": None, "last_success": None, "records": 0, "error": None}
        for area_id, area in MAP_AREAS.items() if area["enabled"]
    },
}
aishub_area_cursor = 0
feed_state = {
    "state": "Waiting for receiver",
    "sharing_state": "Disabled" if not AISHUB_SHARING_ENABLED else "Not configured",
    "received": 0,
    "forwarded": 0,
    "locally_decoded": 0,
    "last_received": None,
    "last_forwarded": None,
    "receiver_name": RECEIVER_NAME,
    "receiver_address": None,
    "datagrams": 0,
    "ignored_lines": 0,
    "error": None,
    "sharing_error": None,
}
decoder_state = {
    "enabled": RECEIVER_MODE == "sdr",
    "state": "Waiting" if RECEIVER_MODE == "sdr" else "Not enabled",
    "version": None,
    "device": SDR_DEVICE,
    "configured_device": SDR_DEVICE,
    "gain": SDR_GAIN,
    "ppm": SDR_PPM,
    "rtl_agc": SDR_RTL_AGC,
    "bias_tee": SDR_BIAS_TEE,
    "bandwidth": SDR_BANDWIDTH,
    "restarts": 0,
    "last_message": None,
    "error": None,
}
marine_vhf_state = {
    "enabled": MARINE_VHF_ENABLED,
    "state": "Waiting" if MARINE_VHF_ENABLED else "Disabled",
    "ready": False,
    "device": MARINE_VHF_DEVICE,
    "configured_device": MARINE_VHF_DEVICE,
    "gain": MARINE_VHF_GAIN,
    "ppm": MARINE_VHF_PPM,
    "squelch": MARINE_VHF_SQUELCH,
    "auto_squelch": MARINE_VHF_AUTO_SQUELCH,
    "modulation": "NFM",
    "channels": MARINE_VHF_CHANNELS,
    "restarts": 0,
    "usb_reset_enabled": MARINE_VHF_USB_RESET_ENABLED,
    "auto_usb_reset": MARINE_VHF_AUTO_USB_RESET,
    "usb_reset_attempts": MARINE_VHF_USB_RESET_ATTEMPTS,
    "usb_resets": 0,
    "last_usb_reset": None,
    "usb_reset_error": None,
    "last_log": None,
    "error": None,
}
MARINE_VHF_MOUNT = "baiamonte-marine.mp3"
MARINE_VHF_PORT = 8000
MARINE_VHF_PASSWORD = secrets.token_urlsafe(24)
marine_vhf_recovery_requested = threading.Event()
GPS_LOCATION_FILE = Path(os.environ.get("BAIAMONTE_GPS_JSON", "/run/baiamonte/gps.json"))
flightaware_weather_cache = {"payload": None, "expires": 0.0, "error": None}
rtl_inventory_cache = {"expires": 0.0, "devices": []}
RTL_USB_IDS = {("0bda", "2832"), ("0bda", "2838")}
USBDEVFS_RESET = 21780


def rtl_sdr_inventory(refresh=False):
    """List RTL-SDRs in libusb order, including a stable physical USB-port selector."""
    now = time.monotonic()
    if not refresh and rtl_inventory_cache["expires"] > now:
        return [dict(item) for item in rtl_inventory_cache["devices"]]
    devices = []
    for entry in Path("/sys/bus/usb/devices").glob("*"):
        try:
            vendor = (entry / "idVendor").read_text(encoding="ascii").strip().lower()
            product = (entry / "idProduct").read_text(encoding="ascii").strip().lower()
            if (vendor, product) not in RTL_USB_IDS:
                continue
            bus = int((entry / "busnum").read_text(encoding="ascii").strip())
            address = int((entry / "devnum").read_text(encoding="ascii").strip())
            serial_file = entry / "serial"
            serial = serial_file.read_text(encoding="utf-8").strip() if serial_file.exists() else ""
            devices.append({
                "port": entry.name,
                "serial": serial,
                "bus": bus,
                "address": address,
                "device_node": f"/dev/bus/usb/{bus:03d}/{address:03d}",
            })
        except (OSError, ValueError):
            continue
    devices.sort(key=lambda item: (item["bus"], item["address"], item["port"]))
    for index, device in enumerate(devices):
        device["index"] = index
        device["selector"] = f"port:{device['port']}"
    rtl_inventory_cache.update({"expires": now + 20.0, "devices": devices})
    return [dict(item) for item in devices]


def resolve_rtl_sdr_selector(selector, role="ais", inventory=None, excluded_index=None):
    """Resolve auto, index, unique serial, or stable port:X selectors to an RTL index."""
    configured = str(selector or "auto").strip()
    devices = rtl_sdr_inventory() if inventory is None else inventory
    if not devices:
        if configured.lower() == "auto":
            return "1" if role == "marine" and excluded_index == "0" else "0"
        if configured.lower().startswith("serial:"):
            return configured.split(":", 1)[1]
        if configured.lower().startswith("port:"):
            raise ValueError(f"RTL-SDR physical port {configured!r} is unavailable")
        return str(int(configured)) if configured.isdigit() else configured
    if configured.lower() == "auto":
        for device in devices:
            if str(device["index"]) != str(excluded_index):
                return str(device["index"])
        raise ValueError(f"No separate RTL-SDR is available for {role}")
    if configured.isdigit():
        index = int(configured)
        if index >= len(devices):
            raise ValueError(f"RTL-SDR index {index} is not attached")
        return str(index)
    if configured.lower().startswith("port:"):
        port = configured.split(":", 1)[1]
        matches = [item for item in devices if item["port"] == port]
    else:
        serial = configured.split(":", 1)[1] if configured.lower().startswith("serial:") else configured
        matches = [item for item in devices if item["serial"] == serial]
    if len(matches) == 1:
        return str(matches[0]["index"])
    if len(matches) > 1:
        raise ValueError(f"RTL-SDR selector {configured!r} is duplicated; use port:<USB-port> or different serials")
    raise ValueError(f"RTL-SDR selector {configured!r} was not found")


def resolved_radio_devices(refresh=False):
    devices = rtl_sdr_inventory(refresh=refresh)
    ais = resolve_rtl_sdr_selector(SDR_DEVICE, "AIS", devices)
    marine = resolve_rtl_sdr_selector(MARINE_VHF_DEVICE, "marine VHF", devices, ais)
    return ais, marine, devices


def public_rtl_sdr_inventory():
    return [
        {key: value for key, value in device.items() if key in {"index", "port", "serial", "selector"}}
        for device in rtl_sdr_inventory()
    ]


def current_location():
    """Prefer a recent USB GPS fix, then use the configured watch-area centre."""
    fallback = {
        "latitude": (lat_south + lat_north) / 2,
        "longitude": (lon_west + lon_east) / 2,
        "altitude": None,
        "source": "Configured watch area",
        "device": "",
        "fix_age": None,
    }
    if not GPS_USE_USB:
        return fallback
    try:
        fix = json.loads(GPS_LOCATION_FILE.read_text(encoding="utf-8"))
        latitude, longitude = float(fix["lat"]), float(fix["lon"])
        age = max(0, time.time() - float(fix.get("timestamp", 0)))
        if -90 <= latitude <= 90 and -180 <= longitude <= 180 and age < 180:
            return {
                "latitude": latitude,
                "longitude": longitude,
                "altitude": clean_number(fix.get("alt")),
                "source": "USB GPS",
                "device": str(fix.get("device", "")),
                "fix_age": round(age, 1),
            }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    return fallback


def flightaware_weather():
    """Return a cached AeroAPI airport observation without exposing the API key."""
    if not FLIGHTAWARE_WEATHER_ENABLED:
        return {"enabled": False, "provider": "FlightAware", "airport": FLIGHTAWARE_AIRPORT}
    if not FLIGHTAWARE_AEROAPI_KEY:
        return {"enabled": True, "provider": "FlightAware", "airport": FLIGHTAWARE_AIRPORT, "error": "AeroAPI key required"}
    if flightaware_weather_cache["payload"] and time.time() < flightaware_weather_cache["expires"]:
        return flightaware_weather_cache["payload"]
    request = urllib.request.Request(
        f"https://aeroapi.flightaware.com/aeroapi/airports/{FLIGHTAWARE_AIRPORT}/weather/observations",
        headers={"x-apikey": FLIGHTAWARE_AEROAPI_KEY, "User-Agent": f"Baiamonte-AIS/{VERSION}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = json.loads(response.read().decode("utf-8"))
        observations = raw.get("observations", []) if isinstance(raw, dict) else []
        latest = observations[0] if observations else {}
        payload = {
            "enabled": True,
            "provider": "FlightAware",
            "airport": FLIGHTAWARE_AIRPORT,
            "temperature_c": latest.get("temp_air"),
            "weather": latest.get("weather"),
            "wind_direction": latest.get("wind_direction"),
            "wind_speed_kts": latest.get("wind_speed"),
            "visibility_miles": latest.get("visibility"),
            "clouds": latest.get("cloud_friendly"),
            "observed_at": latest.get("time") or latest.get("observation_time"),
        }
        flightaware_weather_cache.update({"payload": payload, "expires": time.time() + 600, "error": None})
        return payload
    except (OSError, ValueError, urllib.error.URLError) as exc:
        payload = {"enabled": True, "provider": "FlightAware", "airport": FLIGHTAWARE_AIRPORT, "error": str(exc)}
        flightaware_weather_cache.update({"payload": payload, "expires": time.time() + 120, "error": str(exc)})
        return payload

@lru_cache(maxsize=8192)
def _position_is_confidently_inland(latitude_key, longitude_key, clearance_key):
    """Use a conservative land mask that keeps harbours and coastal traffic."""
    if global_land_globe is None:
        return False
    latitude = float(latitude_key)
    longitude = float(longitude_key)
    clearance_km = float(clearance_key)
    samples = [(latitude, longitude)]
    for radius_km in (clearance_km / 2, clearance_km):
        latitude_delta = radius_km / 111.32
        longitude_delta = radius_km / max(20.0, 111.32 * math.cos(math.radians(latitude)))
        for bearing_degrees in range(0, 360, 45):
            bearing = math.radians(bearing_degrees)
            samples.append((
                latitude + math.cos(bearing) * latitude_delta,
                longitude + math.sin(bearing) * longitude_delta,
            ))
    try:
        return all(bool(global_land_globe.is_land(sample_lat, sample_lon)) for sample_lat, sample_lon in samples)
    except (TypeError, ValueError, IndexError):
        return False


def position_is_confidently_inland(latitude, longitude, clearance_km=3.0):
    return _position_is_confidently_inland(
        round(float(latitude), 4), round(float(longitude), 4), round(float(clearance_km), 1)
    )


def validate_dashboard_position(latitude, longitude, preferred_area_id=None, trust_preferred_area=False):
    """Return the matching operating area, or a reason the AIS fix is implausible."""
    latitude = clean_number(latitude)
    longitude = clean_number(longitude)
    if latitude is None or longitude is None:
        return None, "missing_position"
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None, "invalid_coordinates"

    candidates = []
    preferred = MAP_AREAS.get(preferred_area_id)
    if preferred and preferred.get("enabled"):
        candidates.append(preferred)
    candidates.extend(
        area for area_id, area in MAP_AREAS.items()
        if area.get("enabled") and area_id != preferred_area_id
    )
    area = preferred if trust_preferred_area and preferred and preferred.get("enabled") else next(
        (candidate for candidate in candidates if point_inside_bounds(latitude, longitude, expanded_area_bounds(candidate))),
        None,
    )
    if area is None:
        return None, "outside_operating_area"
    if position_is_confidently_inland(latitude, longitude):
        return None, "inland_position"
    return area, None


def record_position_rejection(mmsi, latitude, longitude, reason, source=None):
    with dashboard_lock:
        counts = position_filter_state["rejected_by_reason"]
        counts[reason] = counts.get(reason, 0) + 1
        position_filter_state["rejected_total"] += 1
        position_filter_state["last_rejected"] = {
            "mmsi": str(mmsi or ""),
            "latitude": latitude,
            "longitude": longitude,
            "reason": reason,
            "source": source,
            "time": datetime.now().isoformat(timespec="seconds"),
        }


def remember_dashboard_vessel(ship_data):
    """Keep only positioned telemetry that is plausible for a configured map area."""
    mmsi = str(ship_data.get("mmsi", ""))
    if not mmsi:
        return False
    latitude = clean_number(ship_data.get("latitude"))
    longitude = clean_number(ship_data.get("longitude"))
    area, rejection_reason = validate_dashboard_position(
        latitude, longitude, ship_data.get("area_id"), bool(ship_data.get("_trusted_area_scope"))
    )
    if rejection_reason:
        record_position_rejection(
            mmsi, latitude, longitude, rejection_reason, ship_data.get("source")
        )
        with dashboard_lock:
            dashboard_vessels.pop(mmsi, None)
        return False

    normalized = dict(ship_data)
    normalized.pop("_trusted_area_scope", None)
    normalized.update({
        "area_id": area["id"],
        "area_name": area["name"],
        "station": area["station"],
        "area_status": vessel_area_status(
            latitude, longitude, ship_data.get("sog"), ship_data.get("cog"), area
        ),
    })
    with dashboard_lock:
        previous = dashboard_vessels.get(mmsi, {})
        merged = {**previous, **normalized, **static_ship_data.get(ship_data.get("mmsi"), {})}
        merged["mmsi"] = mmsi
        merged["last_seen"] = utc_now_iso()
        dashboard_vessels[mmsi] = merged
        if not previous:
            dashboard_events.appendleft({
                "kind": "arrival",
                "message": f"{merged.get('name', 'Unknown vessel')} entered {merged.get('area_name', 'the AIS watch area')}",
                "area_id": merged["area_id"],
                "area_name": merged["area_name"],
                "time": merged["last_seen"],
            })
    return True

def distance_km(lat1, lon1, lat2, lon2):
    """Return the great-circle distance between two WGS84 positions."""
    radius_km = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    a = min(1.0, max(0.0, a))
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def area_center(area):
    bounds = area["bounds"]
    return ((bounds["south"] + bounds["north"]) / 2, (bounds["west"] + bounds["east"]) / 2)


def point_inside_bounds(latitude, longitude, bounds):
    return bounds["south"] <= latitude <= bounds["north"] and bounds["west"] <= longitude <= bounds["east"]


def expanded_area_bounds(area):
    """Expand an area's API query box to include approaching traffic."""
    bounds = area["bounds"]
    center_lat, _ = area_center(area)
    latitude_delta = area["approach_km"] / 111.0
    longitude_delta = area["approach_km"] / max(20.0, 111.0 * math.cos(math.radians(center_lat)))
    return {
        "south": max(-90, bounds["south"] - latitude_delta),
        "west": max(-180, bounds["west"] - longitude_delta),
        "north": min(90, bounds["north"] + latitude_delta),
        "east": min(180, bounds["east"] + longitude_delta),
    }


def vessel_area_status(latitude, longitude, sog, cog, area):
    """Classify a contact as in-area, inbound, or nearby from AIS motion data."""
    if latitude is None or longitude is None:
        return "unknown"
    if point_inside_bounds(latitude, longitude, area["bounds"]):
        return "in_area"
    speed = clean_number(sog)
    course = clean_number(cog)
    if speed is None or course is None or speed < 0.5:
        return "nearby"
    # Project 30 minutes along the AIS course and compare distance to the map centre.
    nautical_miles = speed * 0.5
    bearing = math.radians(course)
    projected_lat = latitude + math.cos(bearing) * nautical_miles / 60.0
    lon_scale = max(0.2, math.cos(math.radians(latitude)))
    projected_lon = longitude + math.sin(bearing) * nautical_miles / (60.0 * lon_scale)
    center_lat, center_lon = area_center(area)
    now_distance = distance_km(latitude, longitude, center_lat, center_lon)
    projected_distance = distance_km(projected_lat, projected_lon, center_lat, center_lon)
    return "inbound" if projected_distance < now_distance - 0.5 else "nearby"


def marine_stream_ready():
    if not MARINE_VHF_ENABLED or marine_vhf_state["state"] not in {"Running", "Streaming"}:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{MARINE_VHF_PORT}/status-json.xsl", timeout=0.35) as response:
            payload = json.loads(response.read().decode("utf-8"))
        source = payload.get("icestats", {}).get("source", [])
        sources = source if isinstance(source, list) else [source]
        return any(
            str(item.get("listenurl", "")).rstrip("/").endswith("/" + MARINE_VHF_MOUNT)
            for item in sources if isinstance(item, dict)
        )
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return False


def marine_vhf_snapshot():
    snapshot = dict(marine_vhf_state)
    snapshot["ready"] = marine_stream_ready()
    snapshot["stream_url"] = "api/marine-radio" if snapshot["ready"] else None
    snapshot["recovery_allowed"] = bool(MARINE_VHF_ENABLED and MARINE_VHF_USB_RESET_ENABLED)
    if snapshot["ready"]:
        snapshot["state"] = "Streaming"
    return snapshot


def receiver_path_operational():
    """Return whether a local/private AIS source is healthy without consulting AISHub."""
    return (
        feed_state.get("state") == "Receiving"
        or decoder_state.get("state") == "Running"
        or rahamin_proxy_state.get("state") == "Connected"
    )

TV_VESSEL_FIELDS = {
    "mmsi", "name", "latitude", "longitude", "sog", "cog", "heading",
    "area_id", "area_status", "distance_km", "destination",
    "nav_status_string", "vessel_type", "vessel_class", "station", "source",
    "last_seen", "source_last_seen",
}


def dashboard_snapshot(area_id=None, compact=False):
    selected_area_id = area_id if area_id in MAP_AREAS else None
    airport_weather = None if compact else flightaware_weather()
    with dashboard_lock:
        location = current_location()
        gps_reference_lat = location["latitude"]
        gps_reference_lon = location["longitude"]
        vessels = []
        rejected_cached = []
        for vessel_key, vessel in dashboard_vessels.items():
            item = dict(vessel)
            item_area_id = str(item.get("area_id") or "baiamonte")
            if selected_area_id and item_area_id != selected_area_id:
                continue
            latitude = clean_number(item.get("latitude"))
            longitude = clean_number(item.get("longitude"))
            if latitude is not None and longitude is not None:
                area, rejection_reason = validate_dashboard_position(
                    latitude, longitude, item.get("area_id")
                )
                if rejection_reason:
                    rejected_cached.append((vessel_key, item, rejection_reason))
                    continue
                item.update({
                    "area_id": area["id"],
                    "area_name": area["name"],
                    "station": area["station"],
                })
                reference_lat, reference_lon = (
                    (gps_reference_lat, gps_reference_lon)
                    if area["id"] == "baiamonte"
                    else area_center(area)
                )
                item["distance_km"] = round(distance_km(reference_lat, reference_lon, latitude, longitude), 2)
            else:
                item["distance_km"] = None
            vessels.append(item)
        for vessel_key, item, rejection_reason in rejected_cached:
            dashboard_vessels.pop(vessel_key, None)
            record_position_rejection(
                item.get("mmsi", vessel_key), item.get("latitude"), item.get("longitude"),
                rejection_reason, item.get("source"),
            )
        vessels.sort(key=lambda vessel: vessel.get("last_seen", ""), reverse=True)
        vessels.sort(key=lambda vessel: (
            vessel.get("distance_km") is None,
            vessel.get("distance_km") if vessel.get("distance_km") is not None else math.inf,
        ))
        nearest_vessels = [vessel for vessel in vessels if vessel.get("distance_km") is not None]
        events = list(dashboard_events)
        if selected_area_id:
            events = [event for event in events if str(event.get("area_id") or "baiamonte") == selected_area_id]
        config_snapshot = {
            "bounds": {
                "south": lat_south, "west": lon_west,
                "north": lat_north, "east": lon_east,
            },
            "area_id": selected_area_id,
            "map_areas": dashboard_map_areas(),
            "default_map_area": DEFAULT_MAP_AREA,
            "tv_default_map_area": TV_DEFAULT_MAP_AREA,
            "dashboard_map_vessels": DASHBOARD_MAP_VESSELS,
            "tv_map_vessels": TV_MAP_VESSELS,
            "tv_live_traffic_only": TV_LIVE_TRAFFIC_ONLY,
            "tv_target_size": TV_TARGET_SIZE,
            "rahamin_proxy_enabled": RAHAMIN_PROXY_ENABLED,
            "aishub_data_source": AISHUB_DATA_SOURCE,
            "aishub_username_in_use": bool(AISHUB_DATA_SOURCE == 'direct_aishub' and AISHUB_USERNAME),
            "rahamin_proxy_interval": RAHAMIN_PROXY_INTERVAL,
            "reference_location": location,
            "map_entities": ENABLE_MAP_ENTITIES,
            "include_class_b": INCLUDE_CLASS_B,
            "timeout_minutes": MAP_TIMEOUT_MINUTES,
            "watchlist_count": len(watchlist_mmsis),
            "source": (
                "Local AIS-catcher + Rahamin single-key proxy"
                if RECEIVER_MODE == "sdr" and RAHAMIN_PROXY_ENABLED
                else "Network AIS input + Rahamin single-key proxy"
                if RAHAMIN_PROXY_ENABLED
                else "Local AIS-catcher + optional AISHub"
                if RECEIVER_MODE == "sdr"
                else "Network AIS input + optional AISHub"
            ),
            "poll_interval": AISHUB_POLL_INTERVAL,
            "receiver_mode": RECEIVER_MODE,
            "receiver_port": RECEIVER_PORT,
            "receiver_channel": RECEIVER_CHANNEL,
            "sdr_device": SDR_DEVICE,
            "marine_vhf_device": MARINE_VHF_DEVICE,
            "sharing_enabled": AISHUB_SHARING_ENABLED,
            "sharing_configured": bool(AISHUB_SHARING_ENABLED and AISHUB_FEED_HOST and AISHUB_FEED_PORT),
            "weather_overlay_dashboard": WEATHER_OVERLAY_DASHBOARD,
            "tv_weather_overlay": TV_WEATHER_OVERLAY,
            "tv_weather_opacity": TV_WEATHER_OPACITY,
            "map_style": MAP_STYLE,
        }
        snapshot = {
            "brand": "Baiamonte AIS",
            "version": VERSION,
            "connection": current_conn_status,
            "service_status": aishub_state.get("state", "Unknown"),
            "last_error": last_known_error,
            "vessels": vessels,
            "nearest_vessels": nearest_vessels,
            "events": events,
            "position_filter": {
                "land_mask": position_filter_state["land_mask"],
                "rejected_total": position_filter_state["rejected_total"],
                "rejected_by_reason": dict(position_filter_state["rejected_by_reason"]),
                "last_rejected": dict(position_filter_state["last_rejected"])
                if position_filter_state["last_rejected"] else None,
            },
            "config": config_snapshot,
            "rahamin_proxy": dict(rahamin_proxy_state),
            "generated_at": utc_now_iso(),
        }
        if compact:
            compact_config_keys = {
                "bounds", "area_id", "map_areas", "tv_default_map_area",
                "tv_map_vessels", "tv_live_traffic_only", "tv_target_size", "tv_weather_overlay",
                "tv_weather_opacity", "map_style", "timeout_minutes",
            }
            return {
                "brand": snapshot["brand"],
                "version": snapshot["version"],
                "connection": snapshot["connection"],
                "vessels": [
                    {key: value for key, value in vessel.items() if key in TV_VESSEL_FIELDS}
                    for vessel in vessels
                ],
                "config": {key: value for key, value in config_snapshot.items() if key in compact_config_keys},
                "rahamin_proxy": snapshot["rahamin_proxy"],
                "generated_at": snapshot["generated_at"],
            }
        snapshot.update({
            "feed": dict(feed_state),
            "area_feeds": {area_id: dict(area_state) for area_id, area_state in aishub_area_states.items()},
            "decoder": dict(decoder_state),
            "marine_vhf": marine_vhf_snapshot(),
            "rtl_sdr_devices": public_rtl_sdr_inventory(),
            "receiver_log": list(receiver_logs),
            "flightaware_weather": airport_weather,
        })
        return snapshot

class DashboardHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        request_path = self.path.split("?", 1)[0].rstrip("/")
        if request_path == "/api/marine-radio/recover":
            accepted, message = request_marine_vhf_recovery()
            payload = json.dumps({"accepted": accepted, "message": message}, separators=(",", ":")).encode("utf-8")
            self.send_response(202 if accepted else 409)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_error(404)

    def do_GET(self):
        request_path = self.path.split("?", 1)[0]
        if request_path.rstrip("/") == "/api/marine-radio":
            if not marine_stream_ready():
                self.send_error(503, "Marine VHF stream is not ready")
                return
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{MARINE_VHF_PORT}/{MARINE_VHF_MOUNT}", timeout=15
                ) as stream:
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/mpeg")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    while not shutdown_in_progress:
                        chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError, urllib.error.URLError):
                pass
            return
        if request_path.rstrip("/") == "/api/weather-maps":
            try:
                payload = fetch_weather_metadata()
            except (OSError, urllib.error.URLError):
                self.send_error(502, "Weather radar temporarily unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "public, max-age=60")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if request_path.startswith("/api/weather-tile/"):
            suffix = request_path.removeprefix("/api/weather-tile/")
            if not valid_weather_tile_path(suffix):
                self.send_error(400, "Invalid weather tile")
                return
            try:
                payload = fetch_weather_tile(suffix)
            except (ValueError, OSError, urllib.error.URLError):
                self.send_error(502, "Weather radar temporarily unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "public, max-age=300")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if request_path.startswith("/api/map-tile/"):
            try:
                _, _, _, style, zoom_text, x_text, y_file = request_path.split("/")
                zoom, x, y = int(zoom_text), int(x_text), int(y_file.removesuffix(".png"))
                limit = 2 ** zoom
                if style not in MAP_TILE_PROVIDERS or not (0 <= zoom <= 19 and 0 <= x < limit and 0 <= y < limit):
                    raise ValueError("tile outside supported range")
                payload, content_type = fetch_map_tile(style, zoom, x, y)
            except (ValueError, OSError, urllib.error.URLError):
                self.send_error(502, "Map tile temporarily unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "public, max-age=21600")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if request_path.rstrip("/") == "/api/status":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            requested_area = str(query.get("area", [""])[0]).strip().lower()
            compact = str(query.get("view", [""])[0]).strip().lower() == "tv"
            payload = json.dumps(dashboard_snapshot(requested_area, compact=compact), separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if request_path.rstrip("/").endswith(("/tv", "/t")):
            relative = "tv.html"
        else:
            relative = request_path.lstrip("/") or "index.html"
        target = (DASHBOARD_ROOT / relative).resolve()
        if DASHBOARD_ROOT not in target.parents and target != DASHBOARD_ROOT:
            self.send_error(403)
            return
        if not target.is_file():
            target = DASHBOARD_ROOT / "index.html"
        try:
            payload = target.read_bytes()
        except OSError:
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        tv_asset = relative in {"tv.html", "tv.js", "tv.css", "maritime-flags.js"}
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0" if tv_asset else "no-cache")
        if tv_asset:
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


MAP_TILE_PROVIDERS = {
    "standard": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "humanitarian": "https://a.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
    "topographic": "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
    "dark": "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
    "satellite": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
}


@lru_cache(maxsize=128)
def fetch_map_tile(style, zoom, x, y):
    """Fetch and briefly cache OSM tiles so restrictive TV browsers use one origin."""
    url = MAP_TILE_PROVIDERS[style].format(z=zoom, x=x, y=y)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"Tenuta-Baiamonte-AIS/{VERSION} (+https://github.com/drahamin/home-assistant-ais)"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = response.read()
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        content_type = "image/png"
    elif payload.startswith(b"\xff\xd8\xff"):
        content_type = "image/jpeg"
    else:
        raise ValueError("invalid map tile response")
    return payload, content_type


weather_metadata_cache = {"payload": None, "expires": 0}


def fetch_weather_metadata():
    if weather_metadata_cache["payload"] and time.time() < weather_metadata_cache["expires"]:
        return weather_metadata_cache["payload"]
    request = urllib.request.Request(
        "https://api.rainviewer.com/public/weather-maps.json",
        headers={"User-Agent": f"Tenuta-Baiamonte-AIS/{VERSION}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = response.read()
    json.loads(payload.decode("utf-8"))
    weather_metadata_cache.update({"payload": payload, "expires": time.time() + 300})
    return payload


@lru_cache(maxsize=64)
def fetch_weather_tile(suffix):
    request = urllib.request.Request(
        f"https://tilecache.rainviewer.com/{suffix}",
        headers={"User-Agent": f"Tenuta-Baiamonte-AIS/{VERSION}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = response.read()
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("invalid weather tile response")
    return payload

def start_dashboard():
    dashboard_host = os.environ.get("BAIAMONTE_AIS_HOST", "0.0.0.0")
    dashboard_port = int(os.environ.get("BAIAMONTE_AIS_PORT", "8099"))
    server = ThreadingHTTPServer((dashboard_host, dashboard_port), DashboardHandler)
    threading.Thread(target=server.serve_forever, name="baiamonte-ais-dashboard", daemon=True).start()
    log(f"🌊 Baiamonte AIS ingress dashboard ready on port {dashboard_port}")

# Map of AIS Navigational Status integers to human-readable strings
NAV_STATUS_MAP = {
    0: "Under way using engine", 1: "At anchor", 2: "Not under command",
    3: "Restricted manoeuvrability", 4: "Constrained by her draught",
    5: "Moored", 6: "Aground", 7: "Engaged in fishing",
    8: "Under way sailing", 14: "AIS-SART active", 15: "Not defined"
}

# Map of Navigational Status integers to MDI Icons
ICON_MAP = {
    0: "mdi:ferry",
    1: "mdi:anchor",
    2: "mdi:lifebuoy",
    3: "mdi:lifebuoy",
    4: "mdi:lifebuoy",
    5: "mdi:pier",
    6: "mdi:lifebuoy",
    7: "mdi:fish",
    8: "mdi:sail-boat",
    14: "mdi:lifebuoy"
}

def get_vessel_type_string(type_int):
    if not isinstance(type_int, int): return None
    if 20 <= type_int <= 29: return "Wing in ground (WIG)"
    if type_int == 30: return "Fishing"
    if type_int in (31, 32): return "Towing"
    if type_int == 33: return "Dredging"
    if type_int == 34: return "Diving Ops"
    if type_int == 35: return "Military Ops"
    if type_int == 36: return "Sailing"
    if type_int == 37: return "Pleasure Craft"
    if 40 <= type_int <= 49: return "High-Speed Craft"
    if type_int == 50: return "Pilot Vessel"
    if type_int == 51: return "Search and Rescue"
    if type_int == 52: return "Tug"
    if type_int == 53: return "Port Tender"
    if type_int == 54: return "Anti-pollution Equipment"
    if type_int == 55: return "Law Enforcement"
    if 60 <= type_int <= 69: return "Passenger Ship"
    if 70 <= type_int <= 79: return "Cargo Ship"
    if 80 <= type_int <= 89: return "Tanker"
    if 90 <= type_int <= 99: return "Other"
    return None

def sync_state_on_startup():
    if not SUPERVISOR_TOKEN:
        log("⚠️ SUPERVISOR_TOKEN is not set. Home Assistant API integration is disabled (running in standalone mode?).")
        return
        
    log("🔄 Synchronising Add-on memory with Home Assistant database...")
    api_url = "http://supervisor/core/api/states"
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    
    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            states = json.loads(response.read().decode('utf-8'))
            
        restored_count = 0
        purged_count = 0
        for state in states:
            entity_id = state.get("entity_id", "")
            is_dev_entity = entity_id.endswith("_dev")
            
            # 1. Purge mismatched static entities
            if entity_id.startswith(f"sensor.{ENTITY_PREFIX}_last_passing_ship") or entity_id.startswith(f"sensor.{ENTITY_PREFIX}_connection_status"):
                if is_dev_entity != DEV_MODE:
                    purge_url = f"http://supervisor/core/api/states/{entity_id}"
                    payload = {"state": "unavailable", "attributes": {}}
                    data = json.dumps(payload).encode('utf-8')
                    req = urllib.request.Request(purge_url, data=data, headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}, method='POST')
                    try:
                        urllib.request.urlopen(req, timeout=5)
                        log(f"   ↳ Purged obsolete environment entity: {entity_id}")
                    except: pass
                continue
                
            # Target dynamic map entities, but rigorously protect last_passing_ship
            if entity_id.startswith(f"sensor.{ENTITY_PREFIX}_ship_") and "last_passing_ship" not in entity_id:
                attrs = state.get("attributes", {})
                vessel_class = attrs.get("vessel_class", "Unknown")
                mmsi = str(attrs.get("mmsi")) if attrs.get("mmsi") else entity_id.replace(f"sensor.{ENTITY_PREFIX}_ship_", "").replace("_dev", "")
                spotted_time = attrs.get("spotted_time")
                
                if not spotted_time:
                    continue
                    
                try:
                    parsed_time = datetime.strptime(spotted_time, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                    
                age_seconds = (datetime.now() - parsed_time).total_seconds()
                
                if (
                    not ENABLE_MAP_ENTITIES or 
                    CLEAR_MAP_ON_STARTUP or 
                    age_seconds > (MAP_TIMEOUT_MINUTES * 60) or
                    (not INCLUDE_CLASS_B and vessel_class == "Class B") or
                    (is_dev_entity != DEV_MODE) or
                    (watchlist_mmsis and mmsi not in watchlist_mmsis)
                ):
                    purge_url = f"http://supervisor/core/api/states/{entity_id}"
                    payload = {"state": "unavailable", "attributes": {}}
                    data = json.dumps(payload).encode('utf-8')
                    purge_req = urllib.request.Request(purge_url, data=data, headers=headers, method='POST')
                    
                    try:
                        urllib.request.urlopen(purge_req, timeout=5)
                        purged_count += 1
                        log(f"   ↳ Purged MMSI {mmsi} (Age: {int(age_seconds/60)}m)")
                    except Exception as purge_err:
                        log(f"Failed to purge entity {entity_id}: {purge_err}")
                else:
                    seen_ships[mmsi] = parsed_time
                    last_map_update[mmsi] = parsed_time
                    
                    static_data = {}
                    for key in ["destination", "eta", "ship_length", "imo_number", "call_sign", "vessel_type"]:
                        if key in attrs and attrs[key] is not None and attrs[key] != "":
                            static_data[key] = attrs[key]
                            
                    if static_data:
                        static_ship_data[mmsi] = static_data
                        
                    restored_count += 1
                    log(f"   ↳ Restored MMSI {mmsi} to memory (Age: {int(age_seconds/60)}m)")
                
        log(f"✅ Sync complete. Restored: {restored_count} active ships. Purged: {purged_count} stale ships.")
    except Exception as e:
        log(f"⚠️ Failed to complete startup sync: {e}")

def update_map_entity(ship_data, remove=False):
    if not SUPERVISOR_TOKEN:
        return
    if not ENABLE_MAP_ENTITIES and not remove:
        return

    mmsi = str(ship_data.get("mmsi", ""))
    if not mmsi:
        return

    entity_id = f"sensor.{ENTITY_PREFIX}_ship_{mmsi}_dev" if DEV_MODE else f"sensor.{ENTITY_PREFIX}_ship_{mmsi}"
    api_url = f"http://supervisor/core/api/states/{entity_id}"
    
    headers = {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json",
    }
    
    if remove:
        payload = {
            "state": "unavailable",
            "attributes": {}
        }
    else:
        # Prevent "None" from becoming the state string if sog is missing
        speed = ship_data.get("sog")
        payload = {
            "state": str(speed) if speed is not None else "0",
            "attributes": {
                "friendly_name": ship_data.get("name", "Unknown Ship"),
                "ship_name": ship_data.get("name", "Unknown Ship"),
                "mmsi": str(ship_data.get("mmsi", "")),
                "spotted_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "latitude": ship_data.get("latitude"),
                "longitude": ship_data.get("longitude"),
                "speed_knots": ship_data.get("sog"),
                "course": ship_data.get("cog"),
                "heading": ship_data.get("heading"),
                "navigational_status": ship_data.get("nav_status_string"),
                "vessel_class": ship_data.get("vessel_class", "Unknown"),
                "icon": ship_data.get("icon", "mdi:ferry")
            }
        }
        
        static_info = static_ship_data.get(ship_data.get("mmsi"))
        if static_info:
            if "destination" in static_info and static_info["destination"]:
                payload["attributes"]["destination"] = static_info["destination"]
            if "eta" in static_info and static_info["eta"]:
                payload["attributes"]["eta"] = static_info["eta"]
            if "ship_length" in static_info and static_info["ship_length"] is not None:
                payload["attributes"]["ship_length"] = static_info["ship_length"]
            if "imo_number" in static_info and static_info["imo_number"] is not None:
                payload["attributes"]["imo_number"] = static_info["imo_number"]
            if "call_sign" in static_info and static_info["call_sign"]:
                payload["attributes"]["call_sign"] = static_info["call_sign"]
            if "vessel_type" in static_info and static_info["vessel_type"]:
                payload["attributes"]["vessel_type"] = static_info["vessel_type"]
        
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(api_url, data=data, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=10) as response:
            if remove:
                log(f"   ↳ HA API: Removed map entity {entity_id}")
            else:
                log(f"   ↳ HA API: Updated map entity {entity_id} (State: {payload['state']} SOG)")
    except Exception as e:
        log(f"Failed to update entity for MMSI {mmsi}: {e}")

def purge_old_ships():
   # Removes ships from memory and the map that haven't been seen recently 
    now = datetime.now()
    expired_mmsi = [
        mmsi for mmsi, last_seen in seen_ships.items() 
        if now - last_seen > timedelta(minutes=MAP_TIMEOUT_MINUTES)
    ]
    for mmsi in expired_mmsi:
        del seen_ships[mmsi]
        if mmsi in last_map_update:
            del last_map_update[mmsi]
        if mmsi in static_ship_data:
            del static_ship_data[mmsi]
        with dashboard_lock:
            dashboard_vessels.pop(str(mmsi), None)
            
        # Strip entity from the map
        update_map_entity({"mmsi": mmsi}, remove=True)
    
    if expired_mmsi:
        log(f"🧹 Purged {len(expired_mmsi)} stale ships from memory and any maps.")

def update_ha_entity(ship_data):
    if not SUPERVISOR_TOKEN:
        log("⚠️ SUPERVISOR_TOKEN not found. Are you running this inside a HA Add-on?")
        return

    headers = {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "state": ship_data["name"],
        "attributes": {
            "friendly_name": "Baiamonte AIS · Last Passing Ship (Dev)" if DEV_MODE else "Baiamonte AIS · Last Passing Ship",
            "ship_name": ship_data["name"],
            "mmsi": str(ship_data["mmsi"]), 
            "spotted_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "icon": ship_data.get("icon", "mdi:ferry"),
            "latitude": ship_data.get("latitude"),
            "longitude": ship_data.get("longitude"),
            "speed_knots": ship_data.get("sog"),
            "course": ship_data.get("cog"),
            "heading": ship_data.get("heading"),
            "navigational_status": ship_data.get("nav_status_string"),
            "vessel_class": ship_data.get("vessel_class", "Unknown")
        }
    }
    
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(API_URL, data=data, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=10) as response:
            entity_id = f"sensor.{ENTITY_PREFIX}_last_passing_ship_dev" if DEV_MODE else f"sensor.{ENTITY_PREFIX}_last_passing_ship"
            log(f"   ↳ HA API: Updated last passing ship entity {entity_id} (State: {payload['state']})")
            
    except urllib.error.URLError as e:
        log(f"Failed to update Home Assistant API: {e}")

def update_conn_status(status, new_error=None):
    global last_known_error
    global current_conn_status
    
    current_conn_status = status
    
    if not SUPERVISOR_TOKEN:
        return

    entity_id = f"sensor.{ENTITY_PREFIX}_connection_status_dev" if DEV_MODE else f"sensor.{ENTITY_PREFIX}_connection_status"
    api_url = f"http://supervisor/core/api/states/{entity_id}"
    
    headers = {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json",
    }

    if new_error is not None:
        last_known_error = new_error

    sanitised_error = ""
    if last_known_error:
        if AISHUB_USERNAME:
            sanitised_error = str(last_known_error).replace(AISHUB_USERNAME, "[REDACTED]")
        else:
            sanitised_error = str(last_known_error)
    raw_aishub_error = aishub_state.get("error")
    sanitised_aishub_error = str(raw_aishub_error).replace(AISHUB_USERNAME, "[REDACTED]") if raw_aishub_error and AISHUB_USERNAME else raw_aishub_error

    state_value = status
    if status == "Connected":
        icon = "mdi:api"
    elif status in ["Connecting", "Polling"]:
        icon = "mdi:sync"
    else:
        icon = "mdi:cloud-off-outline"

    attributes = {
        "friendly_name": "Baiamonte AIS · Connection Status (Dev)" if DEV_MODE else "Baiamonte AIS · Connection Status",
        "provider": "AIS receiver + optional AISHub",
        "last_update_attempt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_api_success": aishub_state["last_success"],
        "aishub_status": aishub_state["state"],
        "aishub_error": sanitised_aishub_error,
        "vessels_received": aishub_state["records"],
        "receiver_feed_status": feed_state["state"],
        "receiver_messages_received": feed_state["received"],
        "local_vessels_decoded": feed_state["locally_decoded"],
        "messages_shared": feed_state["forwarded"],
        "last_receiver_message": feed_state["last_received"],
        "decoder_status": decoder_state["state"],
        "decoder_version": decoder_state["version"],
        "decoder_restarts": decoder_state["restarts"],
        "marine_vhf_status": marine_vhf_state["state"],
        "marine_vhf_device": MARINE_VHF_DEVICE if MARINE_VHF_ENABLED else None,
        "marine_vhf_channels": len(MARINE_VHF_CHANNELS) if MARINE_VHF_ENABLED else 0,
        "error_message": sanitised_error,
        "icon": icon,
    }

    payload = {
        "state": state_value,
        "attributes": attributes
    }
    
    def send_status_update():
        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(api_url, data=data, headers=headers, method='POST')
            # 5-second timeout requirement to ensure loop doesn't hang
            with urllib.request.urlopen(req, timeout=5) as response:
                pass
        except Exception as e:
            log(f"Failed to update connection status entity: {e}")

    threading.Thread(target=send_status_update, daemon=True).start()

def clean_number(value, unavailable=None):
    try:
        number = float(value)
        if unavailable is not None and number == unavailable:
            return None
        return number
    except (TypeError, ValueError):
        return None


def source_timestamp_is_fresh(value, reference_value=None, timeout_minutes=None):
    """Reject replayed proxy contacts while allowing sources without timestamps."""
    if value in (None, ""):
        return True
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if reference_value not in (None, ""):
            reference = datetime.fromisoformat(str(reference_value).strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None and reference.tzinfo is not None:
                parsed = parsed.replace(tzinfo=reference.tzinfo)
            elif parsed.tzinfo is not None and reference.tzinfo is None:
                reference = reference.replace(tzinfo=parsed.tzinfo)
            age_seconds = reference.timestamp() - parsed.timestamp()
        else:
            age_seconds = time.time() - parsed.timestamp()
        limit_seconds = max(1, timeout_minutes or MAP_TIMEOUT_MINUTES) * 60
        return -300 <= age_seconds <= limit_seconds
    except (TypeError, ValueError, OverflowError):
        return False


def source_timestamp_has_timezone(value):
    """Return whether a source timestamp can be compared to the host clock."""
    if value in (None, ""):
        return False
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except (TypeError, ValueError, OverflowError):
        return False


def proxy_value(record, *names):
    """Read a Rahamin dashboard or standard AIS field without case assumptions."""
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return value
    return None


def rahamin_proxy_records(payload):
    """Return vessel records from current and earlier Rahamin status formats."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("Rahamin AIS proxy did not return an object or vessel list")
    records = payload.get("vessels")
    if records is None:
        records = payload.get("VESSELS")
    if records is None:
        records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("Rahamin AIS proxy did not return a vessel list")
    return records


def valid_map_bounds(bounds):
    """Return normalized geographic bounds, or None for an invalid shape."""
    if not isinstance(bounds, dict):
        return None
    normalized = {key: clean_number(bounds.get(key)) for key in ("south", "west", "north", "east")}
    if any(value is None for value in normalized.values()):
        return None
    if not (-90 <= normalized["south"] < normalized["north"] <= 90):
        return None
    if not (-180 <= normalized["west"] < normalized["east"] <= 180):
        return None
    return normalized


def rahamin_proxy_map_bounds(payload, area_id, records):
    """Keep the configured local watch area; targets must not enlarge the TV map."""
    return dict(MAP_AREAS[area_id]["bounds"])


def dashboard_map_areas():
    """Expose fixed configured watch areas; feed contents never change map scale."""
    return [{**configured, "bounds": dict(configured["bounds"])} for configured in MAP_AREAS.values()]


def union_map_bounds(first, second):
    """Expand coverage without shrinking it during ordinary feed refreshes."""
    first = valid_map_bounds(first)
    second = valid_map_bounds(second)
    if not first:
        return second
    if not second:
        return first
    return {
        "south": min(first["south"], second["south"]),
        "west": min(first["west"], second["west"]),
        "north": max(first["north"], second["north"]),
        "east": max(first["east"], second["east"]),
    }


def process_aishub_record(record, area_id="baiamonte"):
    """Convert one human-readable AISHub vessel record into HA telemetry."""
    mmsi = str(record.get("MMSI", "")).strip()
    if len(mmsi) != 9 or not mmsi.isdigit():
        return False
    if watchlist_mmsis and mmsi not in watchlist_mmsis:
        return False

    nav_status = record.get("NAVSTAT")
    try:
        nav_status = int(nav_status)
    except (TypeError, ValueError):
        nav_status = 15

    name = str(record.get("NAME") or "Unknown Ship Name").strip()
    vessel_type_number = record.get("TYPE")
    try:
        vessel_type_number = int(vessel_type_number)
    except (TypeError, ValueError):
        vessel_type_number = 0

    area = MAP_AREAS.get(area_id, MAP_AREAS["baiamonte"])
    latitude = clean_number(record.get("LATITUDE"))
    longitude = clean_number(record.get("LONGITUDE"))
    sog = clean_number(record.get("SOG"), 102.4)
    cog = clean_number(record.get("COG"), 360.0)
    ship_data = {
        "name": name,
        "mmsi": mmsi,
        "latitude": latitude,
        "longitude": longitude,
        "sog": sog,
        "cog": cog,
        "heading": clean_number(record.get("HEADING"), 511),
        "nav_status_string": NAV_STATUS_MAP.get(nav_status, "Not defined"),
        "vessel_class": "AISHub network",
        "source": "AISHub API",
        "station": area["station"],
        "area_id": area_id,
        "area_name": area["name"],
        "area_status": vessel_area_status(latitude, longitude, sog, cog, area),
        "icon": ICON_MAP.get(nav_status, "mdi:ferry"),
    }

    dimensions = []
    for key in ("A", "B"):
        value = clean_number(record.get(key))
        if value is not None:
            dimensions.append(value)
    static_data = {
        "destination": str(record.get("DEST") or "").strip() or None,
        "eta": str(record.get("ETA") or "").strip() or None,
        "ship_length": sum(dimensions) if dimensions else None,
        "ship_width": sum(value for value in (clean_number(record.get("C")), clean_number(record.get("D"))) if value is not None) or None,
        "draught": clean_number(record.get("DRAUGHT")),
        "imo_number": str(record.get("IMO")) if record.get("IMO") not in (None, "", 0, "0") else None,
        "call_sign": str(record.get("CALLSIGN") or "").strip() or None,
        "vessel_type": get_vessel_type_string(vessel_type_number),
    }
    static_ship_data[mmsi] = {key: value for key, value in static_data.items() if value is not None}
    if not remember_dashboard_vessel(ship_data):
        return False

    now = datetime.now()
    is_new = mmsi not in seen_ships
    if is_new:
        log(f"🚢 NEW SHIP: {name} (AISHub | MMSI: {mmsi})")
        update_ha_entity(ship_data)
    seen_ships[mmsi] = now

    last_updated = last_map_update.get(mmsi)
    if last_updated is None or (now - last_updated).total_seconds() >= 60:
        update_map_entity(ship_data)
        last_map_update[mmsi] = now
    return True


def process_rahamin_proxy_record(record, area_id="miami", source_generated_at=None):
    """Import one positioned vessel from the private Rahamin AIS status API."""
    if not isinstance(record, dict):
        return False
    attributes = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
    record = {**attributes, **record}
    mmsi = str(proxy_value(record, "mmsi", "MMSI") or "").strip()
    if len(mmsi) != 9 or not mmsi.isdigit() or (watchlist_mmsis and mmsi not in watchlist_mmsis):
        return False
    latitude = clean_number(proxy_value(record, "latitude", "LATITUDE", "lat", "LAT"))
    longitude = clean_number(proxy_value(record, "longitude", "LONGITUDE", "lon", "LON", "lng"))
    area = MAP_AREAS.get(area_id)
    if not area or not area.get("enabled"):
        return False
    source_seen_at = proxy_value(record, "source_last_seen", "last_seen", "timestamp", "TIME")
    # A cached proxy payload can contain internally consistent timestamps long
    # after the upstream receiver has stopped updating.  Verify the payload
    # clock against the current time before comparing an individual contact to
    # that clock, otherwise an old snapshot can repeatedly replace live local
    # receiver positions.
    # Only compare a proxy snapshot with this host's clock when the source
    # supplied an explicit UTC offset. The Rahamin proxy currently emits local
    # wall-clock timestamps without an offset; interpreting that value in the
    # Home Assistant host timezone makes a healthy Miami feed appear hours old.
    # Individual contacts are still checked against the snapshot clock below.
    if (
        source_timestamp_has_timezone(source_generated_at)
        and not source_timestamp_is_fresh(source_generated_at)
    ):
        return False
    if not source_timestamp_is_fresh(source_seen_at, source_generated_at):
        return False
    # The private source already scopes /api/status?area=... to its configured
    # area. Do not apply Baiamonte's potentially different bounds a second time;
    # that previously discarded valid Rahamin contacts without an error.
    if latitude is None or longitude is None or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return False
    sog = clean_number(proxy_value(record, "sog", "SOG", "speed_knots"), 102.4)
    cog = clean_number(proxy_value(record, "cog", "COG", "course"), 360.0)
    ship_data = {
        "name": str(proxy_value(record, "name", "NAME", "ship_name") or f"AIS {mmsi}").strip(" @"),
        "mmsi": mmsi,
        "latitude": latitude,
        "longitude": longitude,
        "sog": sog,
        "cog": cog,
        "heading": clean_number(proxy_value(record, "heading", "HEADING"), 511),
        "nav_status_string": str(proxy_value(record, "nav_status_string", "navigational_status") or "Not defined"),
        "vessel_class": str(proxy_value(record, "vessel_class", "class") or "Rahamin AIS network"),
        "source": f"Rahamin AIS private proxy · {area['name']}",
        "station": area["station"],
        "area_id": area_id,
        "area_name": area["name"],
        "area_status": vessel_area_status(latitude, longitude, sog, cog, area),
        "icon": str(proxy_value(record, "icon") or "mdi:ferry"),
        "source_last_seen": source_seen_at,
        "_trusted_area_scope": True,
    }
    static = {
        "destination": proxy_value(record, "destination", "DEST"),
        "eta": proxy_value(record, "eta", "ETA"),
        "ship_length": clean_number(proxy_value(record, "ship_length", "length")),
        "ship_width": clean_number(proxy_value(record, "ship_width", "width")),
        "draught": clean_number(proxy_value(record, "draught", "DRAUGHT")),
        "imo_number": proxy_value(record, "imo_number", "IMO"),
        "call_sign": str(proxy_value(record, "call_sign", "CALLSIGN") or "").strip() or None,
        "vessel_type": proxy_value(record, "vessel_type", "TYPE"),
    }
    static_ship_data.setdefault(mmsi, {}).update({key: value for key, value in static.items() if value not in (None, "")})
    if not remember_dashboard_vessel(ship_data):
        return False
    now = datetime.now()
    seen_ships[mmsi] = now
    last_updated = last_map_update.get(mmsi)
    if last_updated is None or (now - last_updated).total_seconds() >= 60:
        update_map_entity(ship_data)
        last_map_update[mmsi] = now
    return True


def rahamin_proxy_area_url(area_id):
    """Add or replace the area query while preserving private proxy URL options."""
    parsed = urllib.parse.urlparse(RAHAMIN_PROXY_URL)
    query = [(key, value) for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if key != "area"]
    query.append(("area", area_id))
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def rahamin_proxy_worker():
    """Poll both cached map areas from the private Miami Pi API."""
    if not rahamin_proxy_state["enabled"] or not RAHAMIN_PROXY_URL:
        return
    last_reported_state = None
    while not shutdown_in_progress:
        checked_at = datetime.now().isoformat(timespec="seconds")
        rahamin_proxy_state["last_checked"] = checked_at
        total_imported = 0
        area_counts = {}
        errors = []
        for area_id, area in MAP_AREAS.items():
            if not area["enabled"]:
                continue
            area_proxy_state = rahamin_proxy_state["areas"].setdefault(
                area_id, {"state": "Waiting", "last_checked": None, "last_success": None, "records": 0, "error": None}
            )
            area_proxy_state["last_checked"] = checked_at
            try:
                request = urllib.request.Request(rahamin_proxy_area_url(area_id), headers={"User-Agent": f"Baiamonte-AIS/{VERSION}"})
                with urllib.request.urlopen(request, timeout=12) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                records = rahamin_proxy_records(payload)
                response_area = str(payload.get("config", {}).get("area_id", area_id)).lower() if isinstance(payload, dict) else area_id
                if response_area != area_id:
                    raise ValueError(f"Rahamin AIS returned {response_area} data for the {area_id} request")
                source_generated_at = payload.get("generated_at") if isinstance(payload, dict) else None
                imported = sum(int(process_rahamin_proxy_record(record, area_id, source_generated_at)) for record in records)
                map_bounds = union_map_bounds(
                    area_proxy_state.get("map_bounds"),
                    rahamin_proxy_map_bounds(payload, area_id, records),
                )
                now = datetime.now().isoformat(timespec="seconds")
                area_proxy_state.update({
                    "state": "Connected", "last_success": now, "records": imported,
                    "map_bounds": map_bounds, "error": None,
                })
                if area_id in aishub_area_states:
                    aishub_area_states[area_id].update({"state": "Private proxy", "last_checked": checked_at, "last_success": now, "records": imported, "error": None})
                total_imported += imported
                area_counts[area_id] = imported
            except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
                area_proxy_state.update({"state": "Connection error", "records": 0, "error": str(exc)})
                if area_id in aishub_area_states:
                    aishub_area_states[area_id].update({"state": "Proxy error", "last_checked": checked_at, "error": str(exc)})
                errors.append(f"{area['name']}: {exc}")
        proxy_state = "Connected" if not errors else ("Degraded" if area_counts else "Connection error")
        rahamin_proxy_state.update({
            "state": proxy_state,
            "last_success": datetime.now().isoformat(timespec="seconds") if area_counts else rahamin_proxy_state.get("last_success"),
            "records": total_imported,
            "error": "; ".join(errors) if errors else None,
        })
        if last_reported_state != proxy_state:
            if area_counts:
                summary = " · ".join(f"{MAP_AREAS[key]['name']} {count}" for key, count in area_counts.items())
                log(f"✅ Rahamin AIS private area proxy {proxy_state.lower()}: {summary}")
            else:
                log(f"⚠️ Rahamin AIS private area proxy failed: {rahamin_proxy_state['error']}")
        last_reported_state = proxy_state
        time.sleep(RAHAMIN_PROXY_INTERVAL)


def parse_aishub_payload(payload):
    if isinstance(payload, dict):
        metadata = payload
        records = payload.get("VESSELS", payload.get("vessels", payload.get("records")))
    elif isinstance(payload, list) and payload:
        metadata = payload[0] if isinstance(payload[0], dict) else {}
        records = payload[1] if len(payload) > 1 else None
        # AISHub may return only its metadata object when the query contains no
        # current positions. That is a successful empty result, not an outage.
        if records is None and metadata.get("RECORDS") in (0, "0"):
            return []
        # Be tolerant of a direct list of vessel dictionaries from proxies.
        if not metadata and all(isinstance(item, dict) for item in payload):
            return payload
    else:
        raise ValueError("AISHub returned an empty or unsupported response")

    error_value = metadata.get("ERROR") if isinstance(metadata, dict) else None
    if error_value not in (False, "false", 0, "0", None, ""):
        detail = metadata.get("ERROR_MESSAGE") or metadata.get("MESSAGE") or error_value
        raise ValueError(str(detail))
    if records is None and isinstance(metadata, dict) and metadata.get("RECORDS") in (0, "0"):
        return []
    if not isinstance(records, list):
        raise ValueError("AISHub response did not include a vessel list")
    return records


def build_aishub_url(area_id="baiamonte"):
    area = MAP_AREAS.get(area_id, MAP_AREAS["baiamonte"])
    query_bounds = expanded_area_bounds(area)
    params = {
        "username": AISHUB_USERNAME,
        "format": 1,
        "output": "json",
        "compress": 0,
        "latmin": round(query_bounds["south"], 6),
        "latmax": round(query_bounds["north"], 6),
        "lonmin": round(query_bounds["west"], 6),
        "lonmax": round(query_bounds["east"], 6),
        "interval": MAP_TIMEOUT_MINUTES,
    }
    if watchlist_mmsis:
        params["mmsi"] = ",".join(watchlist_mmsis)
    return f"{AISHUB_API_URL}?{urllib.parse.urlencode(params)}"


def build_ais_catcher_command(binary="AIS-catcher"):
    """Build a validated AIS-catcher command for the attached RTL-SDR."""
    resolved_device = resolve_rtl_sdr_selector(SDR_DEVICE, "AIS")
    decoder_state["device"] = resolved_device
    command = [binary]
    if resolved_device.isdigit():
        command.append(f"-d:{int(resolved_device)}")
    else:
        command.extend(["-d", resolved_device])
    command.extend([
        "-gr", "RTLAGC", "on" if SDR_RTL_AGC else "off",
        "TUNER", SDR_GAIN,
        "BIASTEE", "on" if SDR_BIAS_TEE else "off",
    ])
    if SDR_BANDWIDTH != "OFF":
        command.extend(["-a", SDR_BANDWIDTH])
    command.extend([
        "-p", str(SDR_PPM),
        "-q", "-v", "10",
        "-u", "127.0.0.1", str(RECEIVER_PORT), "JSON_FULL", "on",
    ])
    return command


def local_ais_vessel(message):
    """Normalize one AIS-catcher JSON_FULL message for the Baiamonte UI."""
    if not isinstance(message, dict) or str(message.get("class", "")).upper() != "AIS":
        return None
    mmsi = str(message.get("mmsi", "")).strip()
    if len(mmsi) != 9 or not mmsi.isdigit():
        return None
    if watchlist_mmsis and mmsi not in watchlist_mmsis:
        return None
    raw_message_type = clean_number(message.get("type"))
    message_type = int(raw_message_type) if raw_message_type is not None else 0
    if not INCLUDE_CLASS_B and message_type in {18, 19, 24}:
        return None

    static = {}
    name = str(message.get("shipname") or message.get("name") or "").strip(" @")
    if name:
        static["name"] = name
    destination = str(message.get("destination") or "").strip(" @")
    if destination:
        static["destination"] = destination
    callsign = str(message.get("callsign") or "").strip(" @")
    if callsign:
        static["call_sign"] = callsign
    imo = message.get("imo")
    if imo not in (None, "", 0, "0"):
        static["imo_number"] = str(imo)
    ship_type = int(clean_number(message.get("shiptype")) or 0)
    if ship_type:
        static["vessel_type"] = get_vessel_type_string(ship_type)
    bow = clean_number(message.get("to_bow"))
    stern = clean_number(message.get("to_stern"))
    if bow is not None or stern is not None:
        static["ship_length"] = (bow or 0) + (stern or 0)
    if static:
        static_ship_data.setdefault(mmsi, {}).update(static)

    latitude = clean_number(message.get("lat"), 91)
    longitude = clean_number(message.get("lon"), 181)
    if latitude is None or longitude is None or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    raw_status = clean_number(message.get("status"))
    status_number = int(raw_status) if raw_status is not None else 15
    return {
        "name": static_ship_data.get(mmsi, {}).get("name", f"AIS {mmsi}"),
        "mmsi": mmsi,
        "latitude": latitude,
        "longitude": longitude,
        "sog": clean_number(message.get("speed"), 102.3),
        "cog": clean_number(message.get("course"), 360),
        "heading": clean_number(message.get("heading"), 511),
        "nav_status_string": str(message.get("status_text") or NAV_STATUS_MAP.get(status_number, "Not defined")),
        "vessel_class": str(message.get("_vessel_class") or ("Local AIS · Class B" if message_type in {18, 19, 24} else "Local AIS · Class A")),
        "source": str(message.get("_source") or "Local AIS-catcher"),
        "icon": ICON_MAP.get(status_number, "mdi:ferry"),
    }


def process_local_ais_message(message):
    """Publish a locally decoded vessel without requiring AISHub access."""
    ship_data = local_ais_vessel(message)
    if not ship_data:
        return False
    mmsi = ship_data["mmsi"]
    is_new = mmsi not in seen_ships
    if not remember_dashboard_vessel(ship_data):
        return False
    now = datetime.now()
    if is_new:
        log(f"🚢 NEW LOCAL SHIP: {ship_data['name']} ({ship_data['source']} | MMSI: {mmsi})")
        update_ha_entity(ship_data)
    seen_ships[mmsi] = now
    last_updated = last_map_update.get(mmsi)
    if last_updated is None or (now - last_updated).total_seconds() >= 60:
        update_map_entity(ship_data)
        last_map_update[mmsi] = now
    return True


def pyais_to_local_message(decoded):
    """Translate a pyais message into the existing AIS-catcher JSON shape."""
    values = decoded.asdict()

    def enum_number(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return value

    message_type = int(values.get("msg_type") or 0)
    return {
        "class": "AIS",
        "type": message_type,
        "mmsi": values.get("mmsi"),
        "lat": values.get("lat"),
        "lon": values.get("lon"),
        "speed": values.get("speed"),
        "course": values.get("course"),
        "heading": values.get("heading"),
        "status": enum_number(values.get("status")),
        "shipname": values.get("shipname") or values.get("name") or values.get("full_name"),
        "destination": values.get("destination"),
        "callsign": values.get("callsign"),
        "imo": values.get("imo"),
        "shiptype": enum_number(values.get("ship_type")),
        "to_bow": values.get("to_bow"),
        "to_stern": values.get("to_stern"),
        "_source": f"Network AIS · {RECEIVER_NAME}",
        "_vessel_class": "Network AIS · Class B" if message_type in {18, 19, 24} else "Network AIS · Class A",
    }


def decode_network_nmea(line):
    """Decode one single- or multipart NMEA sentence received over the private feed."""
    now = time.monotonic()
    for key, entry in list(nmea_fragment_buffer.items()):
        if now - entry["updated"] > 30:
            nmea_fragment_buffer.pop(key, None)
    fields = line.split(",")
    if len(fields) < 7:
        return False
    try:
        fragment_total = int(fields[1])
        fragment_number = int(fields[2])
    except ValueError:
        return False
    sentences = [line]
    if fragment_total > 1:
        sequence = fields[3] or "unsequenced"
        channel = fields[4] or "none"
        key = (fields[0], sequence, channel)
        entry = nmea_fragment_buffer.setdefault(key, {"total": fragment_total, "parts": {}, "updated": now})
        if entry["total"] != fragment_total or fragment_number == 1:
            entry.update({"total": fragment_total, "parts": {}, "updated": now})
        entry["parts"][fragment_number] = line
        entry["updated"] = now
        if len(entry["parts"]) < fragment_total:
            return False
        try:
            sentences = [entry["parts"][number] for number in range(1, fragment_total + 1)]
        except KeyError:
            return False
        finally:
            if all(number in entry["parts"] for number in range(1, fragment_total + 1)):
                nmea_fragment_buffer.pop(key, None)
    try:
        decoded = decode_ais_nmea(*(sentence.encode("ascii") for sentence in sentences))
    except (AISBaseException, UnicodeEncodeError, ValueError):
        return False
    return process_local_ais_message(pyais_to_local_message(decoded))


def decode_receiver_payload(payload):
    """Return NMEA lines and locally decoded vessels from receiver data."""
    valid_lines = []
    ignored = 0
    local_updates = 0
    for raw_line in payload.decode("utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("!AIVDM", "!AIVDO", "!BSVDM", "!ABVDM")):
            valid_lines.append(line)
            if RECEIVER_MODE != "sdr":
                local_updates += int(decode_network_nmea(line))
            continue
        if line.startswith("{"):
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                ignored += 1
                continue
            nmea = message.get("nmea", []) if isinstance(message, dict) else []
            valid_lines.extend(item.strip() for item in nmea if isinstance(item, str) and item.strip().startswith(("!AIVDM", "!AIVDO", "!BSVDM", "!ABVDM")))
            local_updates += int(process_local_ais_message(message))
            continue
        ignored += 1
    return valid_lines, ignored, local_updates


def receiver_packets():
    """Yield AIS chunks from the selected SDR, UDP, TCP, or serial receiver."""
    if RECEIVER_MODE in {"sdr", "udp"}:
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        receiver.bind(("0.0.0.0", RECEIVER_PORT))
        receiver.settimeout(2)
        receiver_ready.set()
        while not shutdown_in_progress:
            try:
                yield receiver.recvfrom(65535)
            except socket.timeout:
                yield None, None
        return

    if RECEIVER_MODE == "tcp":
        while not shutdown_in_progress:
            if not RECEIVER_HOST:
                feed_state.update({"state": "Receiver setup required", "error": "TCP receiver host is empty"})
                yield None, None
                time.sleep(5)
                continue
            try:
                with socket.create_connection((RECEIVER_HOST, RECEIVER_PORT), timeout=10) as receiver:
                    receiver.settimeout(2)
                    log(f"🟢 AIS TCP receiver connected at {RECEIVER_HOST}:{RECEIVER_PORT}")
                    while not shutdown_in_progress:
                        try:
                            payload = receiver.recv(65535)
                            if not payload:
                                raise OSError("receiver closed the connection")
                            yield payload, (RECEIVER_HOST, RECEIVER_PORT)
                        except socket.timeout:
                            yield None, None
            except OSError as exc:
                feed_state.update({"state": "Receiver reconnecting", "error": str(exc)})
                log(f"⚠️ AIS TCP receiver connection failed: {exc}")
                yield None, None
                time.sleep(5)
        return

    while not shutdown_in_progress:
        devices = [RECEIVER_SERIAL_DEVICE] if RECEIVER_SERIAL_DEVICE.lower() != "auto" else (
            sorted(glob.glob("/dev/serial/by-id/*")) + sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))
        )
        device = next((item for item in devices if Path(item).exists()), None)
        if not device:
            feed_state.update({"state": "Waiting for serial receiver", "error": None})
            yield None, None
            time.sleep(5)
            continue
        fd = None
        try:
            fd = os.open(device, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
            attributes = termios.tcgetattr(fd)
            attributes[0] = attributes[1] = attributes[3] = 0
            attributes[2] = termios.CLOCAL | termios.CREAD | termios.CS8
            speed = getattr(termios, f"B{RECEIVER_SERIAL_BAUD}", termios.B38400)
            attributes[4] = attributes[5] = speed
            attributes[6][termios.VMIN] = 0
            attributes[6][termios.VTIME] = 10
            termios.tcsetattr(fd, termios.TCSANOW, attributes)
            log(f"🟢 AIS serial receiver opened at {device} ({RECEIVER_SERIAL_BAUD} baud)")
            while not shutdown_in_progress:
                readable, _, _ = select.select([fd], [], [], 2)
                if readable:
                    payload = os.read(fd, 65535)
                    if not payload:
                        raise OSError("serial receiver disconnected")
                    yield payload, (device, RECEIVER_SERIAL_BAUD)
                else:
                    yield None, None
        except (OSError, termios.error) as exc:
            feed_state.update({"state": "Serial receiver reconnecting", "error": str(exc)})
            log(f"⚠️ AIS serial receiver error: {exc}")
            yield None, None
            time.sleep(5)
        finally:
            if fd is not None:
                os.close(fd)


def receiver_feed_worker():
    """Receive local AIS, update the UI, and forward original NMEA to AISHub."""
    forwarder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    configured = bool(AISHUB_SHARING_ENABLED and AISHUB_FEED_HOST and AISHUB_FEED_PORT)
    feed_state.update({
        "state": "Waiting for receiver",
        "sharing_state": "Waiting for receiver" if configured else ("Not configured" if AISHUB_SHARING_ENABLED else "Disabled"),
        "sharing_error": None,
    })
    channel_label = {"dual": "AIS A+B (161.975/162.025 MHz)", "channel_a": "AIS A (161.975 MHz)", "channel_b": "AIS B (162.025 MHz)"}[RECEIVER_CHANNEL]
    mode_label = "AIS-catcher / RTL-SDR" if RECEIVER_MODE == "sdr" else RECEIVER_MODE.upper()
    log(f"📡 AIS hardware logger started for '{RECEIVER_NAME}' via {mode_label} · {channel_label}")
    if configured:
        log(f"🤝 AISHub sharing enabled to {AISHUB_FEED_HOST}:{AISHUB_FEED_PORT}")
    elif AISHUB_SHARING_ENABLED:
        log("ℹ️ AISHub sharing is enabled; add the assigned feed host and UDP port to begin sharing.")
    else:
        log("ℹ️ Optional AISHub NMEA sharing is disabled. Local receiving and private proxy decoding remain active.")

    last_source = None
    last_summary = time.monotonic()
    summary_received = 0
    summary_forwarded = 0
    summary_ignored = 0
    last_hardware_message = None
    offline_logged = False
    next_forward_attempt = 0.0
    last_forward_error_log = 0.0
    for payload, source in receiver_packets():
        if shutdown_in_progress:
            break
        if payload is None:
            if last_hardware_message is not None and time.monotonic() - last_hardware_message >= 120:
                feed_state["state"] = "Receiver offline"
                if not offline_logged:
                    log(f"🔴 AIS hardware offline: '{RECEIVER_NAME}' has sent no NMEA data for 120 seconds")
                    offline_logged = True
            continue
        try:
            source_label = f"{source[0]}:{source[1]}"
        except (TypeError, IndexError):
            source_label = str(source)
        feed_state["datagrams"] += 1
        last_hardware_message = time.monotonic()
        if offline_logged:
            log(f"🟢 AIS hardware restored: '{RECEIVER_NAME}' is sending NMEA data again")
            offline_logged = False
        feed_state["receiver_address"] = source_label
        if source_label != last_source:
            if last_source is None:
                log(f"🟢 AIS hardware online: '{RECEIVER_NAME}' detected at {source_label}")
            else:
                log(f"🔄 AIS hardware source changed from {last_source} to {source_label}")
            last_source = source_label

        valid_lines, ignored_lines, local_updates = decode_receiver_payload(payload)
        feed_state["locally_decoded"] += local_updates
        feed_state["ignored_lines"] += ignored_lines
        summary_ignored += ignored_lines
        if not valid_lines:
            if time.monotonic() - last_summary >= 60:
                log(f"📊 AIS hardware health · {source_label} · no valid AIS messages · {summary_ignored} ignored lines")
                last_summary = time.monotonic()
                summary_ignored = 0
            continue

        now = datetime.now().isoformat(timespec="seconds")
        feed_state["received"] += len(valid_lines)
        summary_received += len(valid_lines)
        feed_state["last_received"] = now
        feed_state["state"] = "Receiving"
        feed_state["error"] = None
        monotonic_now = time.monotonic()
        if configured and monotonic_now >= next_forward_attempt:
            try:
                outgoing = ("\r\n".join(valid_lines) + "\r\n").encode("ascii")
                forwarder.sendto(outgoing, (AISHUB_FEED_HOST, AISHUB_FEED_PORT))
                feed_state["forwarded"] += len(valid_lines)
                summary_forwarded += len(valid_lines)
                feed_state["last_forwarded"] = now
                feed_state["sharing_state"] = "Sharing"
                feed_state["sharing_error"] = None
                next_forward_attempt = 0.0
            except OSError as exc:
                feed_state["sharing_state"] = "Sharing error"
                feed_state["sharing_error"] = str(exc)
                next_forward_attempt = monotonic_now + 60
                if monotonic_now - last_forward_error_log >= 600:
                    log(f"⚠️ Optional AISHub sharing unavailable: {exc}. AIS receiving and decoding continue normally.")
                    last_forward_error_log = monotonic_now

        if time.monotonic() - last_summary >= 60:
            if configured:
                sharing = f"{summary_forwarded} forwarded to AISHub · sharing {feed_state['sharing_state'].lower()}"
            elif AISHUB_SHARING_ENABLED:
                sharing = "AISHub sharing destination not configured"
            else:
                sharing = "optional AISHub sharing disabled"
            log(
                f"📊 AIS hardware health · {RECEIVER_NAME} at {source_label} · "
                f"{summary_received} valid NMEA messages · {summary_ignored} ignored · {sharing}"
            )
            last_summary = time.monotonic()
            summary_received = 0
            summary_forwarded = 0
            summary_ignored = 0


def start_receiver_feed():
    threading.Thread(target=receiver_feed_worker, name="aishub-nmea-forwarder", daemon=True).start()


def ais_catcher_worker():
    """Run and supervise the bundled dual-channel RTL-SDR decoder."""
    global ais_catcher_process
    receiver_ready.wait(timeout=5)
    while not shutdown_in_progress:
        try:
            command = build_ais_catcher_command()
            decoder_state.update({"state": "Starting", "error": None})
            log(
                "📻 Starting AIS-catcher for RTL-SDR device "
                f"{decoder_state['device']} (configured {SDR_DEVICE}) · gain {SDR_GAIN} · PPM {SDR_PPM} · "
                f"bandwidth {SDR_BANDWIDTH} · RTL AGC {'on' if SDR_RTL_AGC else 'off'} · "
                f"bias tee {'on' if SDR_BIAS_TEE else 'off'}"
            )
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            with ais_catcher_lock:
                ais_catcher_process = process
            decoder_state["state"] = "Running"
            log("🟢 AIS-catcher decoder is running and listening on both AIS channels")
            for output in process.stdout or ():
                line = output.strip()
                if not line:
                    continue
                decoder_state["last_message"] = datetime.now().isoformat(timespec="seconds")
                version_match = re.search(r"AIS-catcher[^v]*(v\d+\.\d+)", line, re.IGNORECASE)
                if version_match:
                    decoder_state["version"] = version_match.group(1)
                log(f"AIS-catcher · {line[:300]}")
            return_code = process.wait()
            if shutdown_in_progress:
                break
            decoder_state.update({"state": "Restarting", "error": f"AIS-catcher exited with code {return_code}"})
            decoder_state["restarts"] += 1
            feed_state.update({"state": "Decoder restarting", "error": decoder_state["error"]})
            log(f"⚠️ {decoder_state['error']}; retrying in 5 seconds")
        except (OSError, ValueError) as exc:
            decoder_state.update({"state": "Start failed", "error": str(exc)})
            decoder_state["restarts"] += 1
            feed_state.update({"state": "Decoder unavailable", "error": str(exc)})
            log(f"🔴 AIS-catcher could not start: {exc}")
        finally:
            with ais_catcher_lock:
                ais_catcher_process = None
        for _ in range(10):
            if shutdown_in_progress:
                break
            time.sleep(0.5)


def start_ais_catcher():
    if RECEIVER_MODE != "sdr":
        log("ℹ️ Built-in AIS-catcher is available but disabled for the selected external receiver mode")
        return
    threading.Thread(target=ais_catcher_worker, name="ais-catcher-supervisor", daemon=True).start()


def config_string(value):
    """Quote a value for RTLSDR-Airband's libconfig format."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_marine_vhf_config(source_password):
    frequency_block = ", ".join(item["frequency"] for item in MARINE_VHF_CHANNELS)
    label_block = ", ".join(config_string(item["label"]) for item in MARINE_VHF_CHANNELS)
    ais_device, resolved_device, _ = resolved_radio_devices()
    if resolved_device == ais_device:
        raise ValueError("AIS and marine VHF resolved to the same RTL-SDR")
    marine_vhf_state["device"] = resolved_device
    selector = f"index = {int(resolved_device)};" if resolved_device.isdigit() else f"serial = {config_string(resolved_device)};"
    squelch_setting = "" if MARINE_VHF_AUTO_SQUELCH else f"      squelch_threshold = {MARINE_VHF_SQUELCH};\n"
    return f'''# Generated by Baiamonte AIS. Configure this in Home Assistant.
devices: ({{
  type = "rtlsdr";
  {selector}
  gain = {MARINE_VHF_GAIN};
  correction = {MARINE_VHF_PPM};
  mode = "scan";
  channels: (
    {{
      freqs = ( {frequency_block} );
      modulation = "nfm";
      labels = ( {label_block} );
{squelch_setting}      outputs: (
        {{
          type = "icecast";
          server = "127.0.0.1";
          port = {MARINE_VHF_PORT};
          mountpoint = "{MARINE_VHF_MOUNT}";
          username = "source";
          password = {config_string(source_password)};
          name = "Baiamonte Marine VHF";
          genre = "Marine";
          description = "Tenuta Baiamonte receive-only marine VHF scanner";
          send_scan_freq_tags = true;
        }}
      );
    }}
  );
}});
'''


def build_marine_icecast_config(source_password):
    return f'''<icecast>
  <location>Tenuta Baiamonte</location>
  <admin>local@baiamonte.invalid</admin>
  <limits><clients>20</clients><sources>2</sources><queue-size>524288</queue-size><client-timeout>30</client-timeout><header-timeout>15</header-timeout><source-timeout>10</source-timeout></limits>
  <authentication><source-password>{source_password}</source-password><relay-password>{source_password}</relay-password><admin-user>admin</admin-user><admin-password>{source_password}</admin-password></authentication>
  <hostname>127.0.0.1</hostname>
  <listen-socket><port>{MARINE_VHF_PORT}</port><bind-address>127.0.0.1</bind-address></listen-socket>
  <http-headers><header name="Access-Control-Allow-Origin" value="*" /></http-headers>
  <fileserve>1</fileserve>
  <paths><logdir>/tmp</logdir><webroot>/usr/share/icecast2/web</webroot><adminroot>/usr/share/icecast2/admin</adminroot><pidfile>/tmp/baiamonte-icecast.pid</pidfile></paths>
  <logging><accesslog>baiamonte-icecast-access.log</accesslog><errorlog>baiamonte-icecast-error.log</errorlog><loglevel>2</loglevel><logsize>10000</logsize></logging>
  <security><chroot>0</chroot><changeowner><user>icecast2</user><group>icecast</group></changeowner></security>
</icecast>
'''


def pipe_radio_logs(process, label):
    for output in process.stdout or ():
        line = output.strip()
        if not line:
            continue
        marine_vhf_state["last_log"] = datetime.now().isoformat(timespec="seconds")
        log(f"{label} · {line[:300]}")


def terminate_process(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


def marine_vhf_device_conflict():
    if not (MARINE_VHF_ENABLED and RECEIVER_MODE == "sdr"):
        return False
    try:
        ais_device, marine_device, _ = resolved_radio_devices()
        return ais_device == marine_device
    except ValueError:
        return MARINE_VHF_DEVICE == SDR_DEVICE


def reset_marine_vhf_usb():
    """Reset only the resolved marine RTL-SDR through Linux USBDEVFS_RESET."""
    if not MARINE_VHF_USB_RESET_ENABLED:
        raise PermissionError("Marine VHF USB reset is disabled in app configuration")
    _, resolved_device, devices = resolved_radio_devices(refresh=True)
    matches = [item for item in devices if str(item["index"]) == resolved_device]
    if len(matches) != 1:
        raise OSError("The marine VHF RTL-SDR USB device could not be identified safely")
    device = matches[0]
    descriptor = f"RTL-SDR {resolved_device} on USB port {device['port']}"
    fd = os.open(device["device_node"], os.O_WRONLY)
    try:
        fcntl.ioctl(fd, USBDEVFS_RESET, 0)
    finally:
        os.close(fd)
    marine_vhf_state.update({
        "usb_resets": marine_vhf_state["usb_resets"] + 1,
        "last_usb_reset": datetime.now().isoformat(timespec="seconds"),
        "usb_reset_error": None,
    })
    rtl_inventory_cache["expires"] = 0.0
    log(f"🔌 Reset {descriptor}; AIS RTL-SDR {decoder_state.get('device')} was not touched")
    return descriptor


def request_marine_vhf_recovery():
    if not MARINE_VHF_ENABLED:
        return False, "Marine VHF is disabled"
    if not MARINE_VHF_USB_RESET_ENABLED:
        return False, "Enable Marine VHF USB reset in the app configuration first"
    if marine_vhf_recovery_requested.is_set():
        return False, "Marine VHF recovery is already queued"
    marine_vhf_recovery_requested.set()
    marine_vhf_state.update({"state": "Recovery requested", "ready": False, "error": None})
    with marine_vhf_lock:
        terminate_process(marine_vhf_process)
    log("🔌 Manual marine VHF recovery requested; stopping the scanner before resetting its USB device")
    return True, "Marine VHF recovery queued"


def marine_vhf_worker():
    """Supervise the second Nooelec, NFM scanner, and private audio server."""
    global marine_vhf_process, marine_icecast_process
    runtime = Path("/run/baiamonte")
    runtime.mkdir(parents=True, exist_ok=True)
    radio_config = runtime / "rtl-marine-vhf.conf"
    icecast_config = runtime / "marine-icecast.xml"
    icecast_config.write_text(build_marine_icecast_config(MARINE_VHF_PASSWORD), encoding="utf-8")
    os.chmod(icecast_config, 0o600)
    automatic_resets = 0

    while not shutdown_in_progress:
        if marine_vhf_recovery_requested.is_set():
            marine_vhf_recovery_requested.clear()
            try:
                marine_vhf_state["state"] = "Resetting USB"
                reset_marine_vhf_usb()
                time.sleep(2)
            except (OSError, ValueError, PermissionError) as exc:
                marine_vhf_state.update({"usb_reset_error": str(exc), "error": str(exc)})
                log(f"⚠️ Manual marine VHF USB reset failed: {exc}")
        try:
            if marine_vhf_device_conflict():
                raise ValueError("AIS and marine VHF cannot use the same RTL-SDR; use device 0/1, unique serials, or port:<USB-port>")
            radio_config.write_text(build_marine_vhf_config(MARINE_VHF_PASSWORD), encoding="utf-8")
            os.chmod(radio_config, 0o600)
            marine_vhf_state.update({"state": "Starting", "ready": False, "error": None})
            labels = ", ".join(f"{item['label']} {item['frequency']} MHz" for item in MARINE_VHF_CHANNELS)
            log(
                f"📻 Starting marine VHF on RTL-SDR {marine_vhf_state['device']} "
                f"(configured {MARINE_VHF_DEVICE}) · gain {MARINE_VHF_GAIN:g} · "
                f"PPM {MARINE_VHF_PPM} · squelch {MARINE_VHF_SQUELCH} · {labels}"
            )
            icecast = subprocess.Popen(
                ["icecast2", "-c", str(icecast_config)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            with marine_vhf_lock:
                marine_icecast_process = icecast
            threading.Thread(target=pipe_radio_logs, args=(icecast, "Marine audio"), daemon=True).start()
            for _ in range(30):
                if icecast.poll() is not None or shutdown_in_progress:
                    break
                try:
                    with socket.create_connection(("127.0.0.1", MARINE_VHF_PORT), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            if icecast.poll() is not None:
                raise OSError(f"marine audio server exited with code {icecast.returncode}")
            radio = subprocess.Popen(
                ["rtl_airband", "-F", "-e", "-c", str(radio_config)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            with marine_vhf_lock:
                marine_vhf_process = radio
            threading.Thread(target=pipe_radio_logs, args=(radio, "Marine VHF"), daemon=True).start()
            marine_vhf_state["state"] = "Running"
            log("🟢 Marine VHF scanner is running; press Play on the Marine radio page to listen")
            while not shutdown_in_progress and radio.poll() is None and icecast.poll() is None:
                time.sleep(1)
            if not shutdown_in_progress:
                failure = radio.returncode if radio.poll() is not None else icecast.returncode
                raise OSError(f"marine radio service exited with code {failure}")
        except (OSError, ValueError) as exc:
            if not shutdown_in_progress:
                marine_vhf_state.update({"state": "Restarting", "ready": False, "error": str(exc)})
                marine_vhf_state["restarts"] += 1
                log(f"⚠️ Marine VHF error: {exc}; retrying in 5 seconds")
        finally:
            with marine_vhf_lock:
                radio, icecast = marine_vhf_process, marine_icecast_process
                marine_vhf_process = None
                marine_icecast_process = None
            terminate_process(radio)
            terminate_process(icecast)
        manual_reset = marine_vhf_recovery_requested.is_set()
        if manual_reset:
            marine_vhf_recovery_requested.clear()
            try:
                marine_vhf_state["state"] = "Resetting USB"
                reset_marine_vhf_usb()
                time.sleep(2)
            except (OSError, ValueError, PermissionError) as exc:
                marine_vhf_state.update({"usb_reset_error": str(exc), "error": str(exc)})
                log(f"⚠️ Manual marine VHF USB reset failed: {exc}")
        elif (
            not shutdown_in_progress
            and MARINE_VHF_USB_RESET_ENABLED
            and MARINE_VHF_AUTO_USB_RESET
            and automatic_resets < MARINE_VHF_USB_RESET_ATTEMPTS
        ):
            try:
                marine_vhf_state["state"] = "Resetting USB"
                reset_marine_vhf_usb()
                automatic_resets += 1
                time.sleep(2)
            except (OSError, ValueError, PermissionError) as exc:
                marine_vhf_state.update({"usb_reset_error": str(exc), "error": str(exc)})
                automatic_resets += 1
                log(f"⚠️ Automatic marine VHF USB reset failed: {exc}")
        for _ in range(10):
            if shutdown_in_progress:
                break
            time.sleep(0.5)


def start_marine_vhf():
    if not MARINE_VHF_ENABLED:
        log("ℹ️ Marine VHF is disabled; enable it after connecting the second Nooelec")
        return
    threading.Thread(target=marine_vhf_worker, name="marine-vhf-supervisor", daemon=True).start()


def start_gps():
    if not GPS_USE_USB:
        log("📍 USB GPS disabled; using the configured watch-area centre")
        return
    command = [sys.executable, "/gps_reader.py", "--device", GPS_DEVICE, "--baud", str(GPS_BAUD), "--output", str(GPS_LOCATION_FILE)]
    try:
        subprocess.Popen(command)
        log(f"📍 USB GPS logger started ({GPS_DEVICE} at {GPS_BAUD} baud)")
    except OSError as exc:
        log(f"⚠️ USB GPS could not start: {exc}")


def start_tracker():
    global last_purge_time, last_known_error, aishub_area_cursor
    # Cleanup must also run in private-proxy-only mode.  Previously both early
    # returns below skipped the only purge call, leaving stale vessels visible
    # indefinitely after an upstream outage.
    if (datetime.now() - last_purge_time).total_seconds() >= 60:
        purge_old_ships()
        last_purge_time = datetime.now()
    enabled_areas = [
        area for area in MAP_AREAS.values()
        if area["enabled"] and not (rahamin_proxy_state["enabled"] and RAHAMIN_PROXY_URL)
    ]
    if not enabled_areas:
        aishub_state.update({"state": "Private proxy", "error": None})
        if receiver_path_operational():
            update_conn_status("Connected", new_error="")
        time.sleep(AISHUB_POLL_INTERVAL)
        return
    if not AISHUB_USERNAME:
        message = "Enter the AISHub username supplied after your contributor station is approved."
        already_reported = aishub_state.get("state") == "Setup required" and aishub_state.get("error") == message
        aishub_state.update({"state": "Setup required", "error": message})
        if receiver_path_operational():
            update_conn_status("Connected", new_error="")
        else:
            update_conn_status("Setup required", new_error=message)
        if not already_reported:
            log(f"⚠️ Optional AISHub API setup: {message} Local and private AIS receiving remain available.")
        time.sleep(AISHUB_POLL_INTERVAL)
        return

    area = enabled_areas[aishub_area_cursor % len(enabled_areas)]
    aishub_area_cursor = (aishub_area_cursor + 1) % len(enabled_areas)
    area_state = aishub_area_states[area["id"]]
    log(f"🌐 Baiamonte AIS {VERSION} polling {area['name']} through AISHub")
    if not receiver_path_operational():
        update_conn_status("Connecting")
    retry_delay = AISHUB_POLL_INTERVAL
    try:
        checked_at = datetime.now().isoformat(timespec="seconds")
        aishub_state["last_checked"] = checked_at
        area_state["last_checked"] = checked_at
        request = urllib.request.Request(build_aishub_url(area["id"]), headers={"User-Agent": f"Baiamonte-AIS/{VERSION}"})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        records = parse_aishub_payload(payload)
        for record in records:
            process_aishub_record(record, area["id"])

        last_known_error = ""
        now = datetime.now().isoformat(timespec="seconds")
        aishub_state.update({
            "state": "Connected",
            "last_success": now,
            "records": len(records),
            "error": None,
        })
        area_state.update({"state": "Connected", "last_success": now, "records": len(records), "error": None})
        update_conn_status("Connected")
        log(f"✅ {area['name']} update complete: {len(records)} vessels including the approach area")
    except Exception as exc:
        message = f"AISHub request failed: {exc}"
        credential_error = "invalid username or password" in str(exc).lower()
        failure_state = "Credentials rejected" if credential_error else "Connection error"
        aishub_state.update({"state": failure_state, "error": str(exc)})
        area_state.update({"state": failure_state, "error": str(exc)})
        if receiver_path_operational():
            update_conn_status("Connected", new_error="")
        else:
            update_conn_status(failure_state, new_error=message)
        log(f"⚠️ {message}")
        if credential_error:
            retry_delay = max(900, AISHUB_POLL_INTERVAL)
            log("ℹ️ AISHub credentials were rejected; the next API retry is delayed for 15 minutes. Receiver and private proxy maps continue normally.")

    time.sleep(retry_delay)

def graceful_shutdown(signum, frame):
    global shutdown_in_progress
    shutdown_in_progress = True
    log("🛑 Received stop signal from Home Assistant. Shutting down gracefully...")
    with ais_catcher_lock:
        process = ais_catcher_process
    terminate_process(process)
    with marine_vhf_lock:
        radio, icecast = marine_vhf_process, marine_icecast_process
    terminate_process(radio)
    terminate_process(icecast)
    update_conn_status("Stopped", new_error="Add-on stopped by user or system.")
    log("🛑 Tracker safely stopped.")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)

    start_dashboard()
    sync_state_on_startup()
    start_gps()
    start_receiver_feed()
    threading.Thread(target=rahamin_proxy_worker, name="rahamin-ais-private-proxy", daemon=True).start()
    start_ais_catcher()
    start_marine_vhf()
        
    try:
        while True:
            start_tracker()
    except KeyboardInterrupt:
        log("🛑 Tracker stopped by user.")
        update_conn_status("Disconnected")
