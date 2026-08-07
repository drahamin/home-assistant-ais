import json
import time
import os
import mimetypes
import socket
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.request
import urllib.error
import urllib.parse
import signal
import sys
import threading
from datetime import datetime, timedelta

print("🚀 Starting Baiamonte AIS...", flush=True)
VERSION = "2.1.0"

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
    AISHUB_USERNAME = str(config.get('aishub_username', '')).strip()
    AISHUB_API_URL = str(config.get('aishub_api_url', 'https://data.aishub.net/ws.php')).strip()
    AISHUB_POLL_INTERVAL = max(60, get_safe_int('aishub_poll_interval', 60))
    AISHUB_FEED_HOST = str(config.get('aishub_feed_host', '')).strip()
    AISHUB_FEED_PORT = get_safe_int('aishub_feed_port', 0)
    RECEIVER_UDP_PORT = 10110
    RECEIVER_NAME = str(config.get('receiver_name', 'Baiamonte AIS receiver')).strip() or 'Baiamonte AIS receiver'
    lat_south = float(config.get('latitude_south', 50.90))
    lon_west = float(config.get('longitude_west', 1.20))
    lat_north = float(config.get('latitude_north', 51.20))
    lon_east = float(config.get('longitude_east', 1.80))
    BOUNDING_BOX = [[[lat_south, lon_west], [lat_north, lon_east]]]
    
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

# Read-only state used by the Home Assistant ingress dashboard.
dashboard_vessels = {}
dashboard_events = deque(maxlen=40)
dashboard_lock = threading.RLock()
DASHBOARD_ROOT = Path(__file__).resolve().parent / "web"
aishub_state = {
    "state": "Starting",
    "last_checked": None,
    "last_success": None,
    "records": 0,
    "error": None,
}
feed_state = {
    "state": "Waiting for receiver",
    "received": 0,
    "forwarded": 0,
    "last_received": None,
    "last_forwarded": None,
    "receiver_name": RECEIVER_NAME,
    "receiver_address": None,
    "datagrams": 0,
    "ignored_lines": 0,
    "error": None,
}

def remember_dashboard_vessel(ship_data):
    """Keep the latest safe telemetry for the ingress overview."""
    mmsi = str(ship_data.get("mmsi", ""))
    if not mmsi:
        return
    with dashboard_lock:
        previous = dashboard_vessels.get(mmsi, {})
        merged = {**previous, **ship_data, **static_ship_data.get(ship_data.get("mmsi"), {})}
        merged["mmsi"] = mmsi
        merged["last_seen"] = datetime.now().isoformat(timespec="seconds")
        dashboard_vessels[mmsi] = merged
        if not previous:
            dashboard_events.appendleft({
                "kind": "arrival",
                "message": f"{merged.get('name', 'Unknown vessel')} entered the estate watch area",
                "time": merged["last_seen"],
            })

def dashboard_snapshot():
    with dashboard_lock:
        vessels = sorted(
            (dict(vessel) for vessel in dashboard_vessels.values()),
            key=lambda vessel: vessel.get("last_seen", ""),
            reverse=True,
        )
        return {
            "brand": "Baiamonte AIS",
            "version": VERSION,
            "connection": current_conn_status,
            "service_status": aishub_state.get("state", "Unknown"),
            "last_error": last_known_error,
            "vessels": vessels,
            "events": list(dashboard_events),
            "config": {
                "bounds": {
                    "south": lat_south, "west": lon_west,
                    "north": lat_north, "east": lon_east,
                },
                "map_entities": ENABLE_MAP_ENTITIES,
                "include_class_b": INCLUDE_CLASS_B,
                "timeout_minutes": MAP_TIMEOUT_MINUTES,
                "watchlist_count": len(watchlist_mmsis),
                "source": "AISHub",
                "poll_interval": AISHUB_POLL_INTERVAL,
                "receiver_port": RECEIVER_UDP_PORT,
                "sharing_configured": bool(AISHUB_FEED_HOST and AISHUB_FEED_PORT),
            },
            "feed": dict(feed_state),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        request_path = self.path.split("?", 1)[0]
        if request_path.rstrip("/") == "/api/status":
            payload = json.dumps(dashboard_snapshot()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

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
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return

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

    state_value = status
    if status == "Connected":
        icon = "mdi:api"
    elif status in ["Connecting", "Polling"]:
        icon = "mdi:sync"
    else:
        icon = "mdi:cloud-off-outline"

    attributes = {
        "friendly_name": "Baiamonte AIS · Connection Status (Dev)" if DEV_MODE else "Baiamonte AIS · Connection Status",
        "provider": "AISHub",
        "last_update_attempt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_api_success": aishub_state["last_success"],
        "vessels_received": aishub_state["records"],
        "receiver_feed_status": feed_state["state"],
        "receiver_messages_received": feed_state["received"],
        "messages_shared": feed_state["forwarded"],
        "last_receiver_message": feed_state["last_received"],
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


def process_aishub_record(record):
    """Convert one human-readable AISHub vessel record into HA telemetry."""
    mmsi = str(record.get("MMSI", "")).strip()
    if len(mmsi) != 9 or not mmsi.isdigit():
        return
    if watchlist_mmsis and mmsi not in watchlist_mmsis:
        return

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

    ship_data = {
        "name": name,
        "mmsi": mmsi,
        "latitude": clean_number(record.get("LATITUDE")),
        "longitude": clean_number(record.get("LONGITUDE")),
        "sog": clean_number(record.get("SOG"), 102.4),
        "cog": clean_number(record.get("COG"), 360.0),
        "heading": clean_number(record.get("HEADING"), 511),
        "nav_status_string": NAV_STATUS_MAP.get(nav_status, "Not defined"),
        "vessel_class": "AISHub network",
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
        "imo_number": str(record.get("IMO")) if record.get("IMO") not in (None, "", 0, "0") else None,
        "call_sign": str(record.get("CALLSIGN") or "").strip() or None,
        "vessel_type": get_vessel_type_string(vessel_type_number),
    }
    static_ship_data[mmsi] = {key: value for key, value in static_data.items() if value is not None}
    remember_dashboard_vessel(ship_data)

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


def parse_aishub_payload(payload):
    if not isinstance(payload, list) or len(payload) < 2:
        raise ValueError("Unexpected AISHub response format")
    metadata, records = payload[0], payload[1]
    if isinstance(metadata, dict) and metadata.get("ERROR") not in (False, "false", 0, "0", None):
        raise ValueError(str(metadata.get("ERROR")))
    if not isinstance(records, list):
        raise ValueError("AISHub response did not include a vessel list")
    return records


def build_aishub_url():
    params = {
        "username": AISHUB_USERNAME,
        "format": 1,
        "output": "json",
        "compress": 0,
        "latmin": lat_south,
        "latmax": lat_north,
        "lonmin": lon_west,
        "lonmax": lon_east,
        "interval": MAP_TIMEOUT_MINUTES,
    }
    if watchlist_mmsis:
        params["mmsi"] = ",".join(watchlist_mmsis)
    return f"{AISHUB_API_URL}?{urllib.parse.urlencode(params)}"


def receiver_feed_worker():
    """Receive raw local NMEA over UDP and forward it to the assigned AISHub port."""
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    receiver.bind(("0.0.0.0", RECEIVER_UDP_PORT))
    receiver.settimeout(2)
    forwarder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    configured = bool(AISHUB_FEED_HOST and AISHUB_FEED_PORT)
    feed_state["state"] = "Waiting for receiver" if configured else "Receiver ready · AISHub destination needed"
    log(f"📡 AIS hardware logger started for '{RECEIVER_NAME}' on UDP {RECEIVER_UDP_PORT}")
    if configured:
        log(f"🤝 AISHub sharing enabled to {AISHUB_FEED_HOST}:{AISHUB_FEED_PORT}")
    else:
        log("ℹ️ Add the AISHub feed host and assigned UDP port to begin sharing.")

    last_source = None
    last_summary = time.monotonic()
    summary_received = 0
    summary_forwarded = 0
    summary_ignored = 0
    last_hardware_message = None
    offline_logged = False
    while not shutdown_in_progress:
        try:
            payload, source = receiver.recvfrom(65535)
        except socket.timeout:
            if last_hardware_message is not None and time.monotonic() - last_hardware_message >= 120:
                feed_state["state"] = "Receiver offline"
                if not offline_logged:
                    log(f"🔴 AIS hardware offline: '{RECEIVER_NAME}' has sent no NMEA data for 120 seconds")
                    offline_logged = True
            continue
        except OSError as exc:
            feed_state["state"] = "Receiver error"
            feed_state["error"] = str(exc)
            log(f"Local receiver UDP error: {exc}")
            time.sleep(5)
            continue

        feed_state["datagrams"] += 1
        last_hardware_message = time.monotonic()
        if offline_logged:
            log(f"🟢 AIS hardware restored: '{RECEIVER_NAME}' is sending NMEA data again")
            offline_logged = False
        source_label = f"{source[0]}:{source[1]}"
        feed_state["receiver_address"] = source_label
        if source_label != last_source:
            if last_source is None:
                log(f"🟢 AIS hardware online: '{RECEIVER_NAME}' detected at {source_label}")
            else:
                log(f"🔄 AIS hardware source changed from {last_source} to {source_label}")
            last_source = source_label

        valid_lines = []
        ignored_lines = 0
        for raw_line in payload.decode("ascii", errors="ignore").splitlines():
            line = raw_line.strip()
            if line.startswith(("!AIVDM", "!AIVDO", "!BSVDM", "!ABVDM")):
                valid_lines.append(line)
            elif line:
                ignored_lines += 1
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
        if not configured:
            feed_state["state"] = "Receiving · AISHub destination needed"
        if configured:
            try:
                outgoing = ("\r\n".join(valid_lines) + "\r\n").encode("ascii")
                forwarder.sendto(outgoing, (AISHUB_FEED_HOST, AISHUB_FEED_PORT))
                feed_state["forwarded"] += len(valid_lines)
                summary_forwarded += len(valid_lines)
                feed_state["last_forwarded"] = now
                feed_state["state"] = "Sharing"
                feed_state["error"] = None
            except OSError as exc:
                feed_state["state"] = "Sharing error"
                feed_state["error"] = str(exc)
                log(f"AISHub feed forwarding error: {exc}")

        if time.monotonic() - last_summary >= 60:
            sharing = f"{summary_forwarded} forwarded to AISHub" if configured else "AISHub destination not configured"
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


def start_tracker():
    global last_purge_time, last_known_error
    if not AISHUB_USERNAME:
        message = "Enter the AISHub username supplied after your contributor station is approved."
        aishub_state.update({"state": "Setup required", "error": message})
        update_conn_status("Setup required", new_error=message)
        log(f"⚠️ {message}")
        time.sleep(AISHUB_POLL_INTERVAL)
        return

    log(f"🌐 Baiamonte AIS {VERSION} polling the AISHub vessel network every {AISHUB_POLL_INTERVAL} seconds")
    update_conn_status("Connecting")
    try:
        aishub_state["last_checked"] = datetime.now().isoformat(timespec="seconds")
        request = urllib.request.Request(build_aishub_url(), headers={"User-Agent": f"Baiamonte-AIS/{VERSION}"})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        records = parse_aishub_payload(payload)
        for record in records:
            process_aishub_record(record)

        last_known_error = ""
        now = datetime.now().isoformat(timespec="seconds")
        aishub_state.update({
            "state": "Connected",
            "last_success": now,
            "records": len(records),
            "error": None,
        })
        update_conn_status("Connected")
        log(f"✅ AISHub update complete: {len(records)} vessels in the watch area")
    except Exception as exc:
        message = f"AISHub request failed: {exc}"
        aishub_state.update({"state": "Connection error", "error": str(exc)})
        update_conn_status("Connection error", new_error=message)
        log(f"⚠️ {message}")

    if (datetime.now() - last_purge_time).total_seconds() >= 60:
        purge_old_ships()
        last_purge_time = datetime.now()
    time.sleep(AISHUB_POLL_INTERVAL)

def graceful_shutdown(signum, frame):
    global shutdown_in_progress
    shutdown_in_progress = True
    log("🛑 Received stop signal from Home Assistant. Shutting down gracefully...")
    update_conn_status("Stopped", new_error="Add-on stopped by user or system.")
    log("🛑 Tracker safely stopped.")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)

    start_dashboard()
    sync_state_on_startup()
    start_receiver_feed()
        
    try:
        while True:
            start_tracker()
    except KeyboardInterrupt:
        log("🛑 Tracker stopped by user.")
        update_conn_status("Disconnected")
