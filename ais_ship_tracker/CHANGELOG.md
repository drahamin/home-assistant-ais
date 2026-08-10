# Changelog

## 2.7.8

- Matches the AIS `/tv` vessel sidebar dimensions and target-row density to the Baiamonte ADS-B TV display.
- Uses the same 360 px adaptive sidebar, compact header, flag tile, typography, spacing, and responsive scaling while preserving vessel destination and type details.

## 2.7.7

- Adds Baiamonte AIS favicons, Apple touch icons, Android/PWA icons, and an installable web manifest to the dashboard and TV map.
- Replaces the generic Home Assistant radar/anchor-style sidebar glyph with a vessel icon while retaining the gold Baiamonte app artwork.

## 2.7.6

- Keeps the Operations Journal scoped to the selected Baiamonte Sicily or Rahamin Miami map area.
- Adds explicit area identity to every vessel-arrival event so Miami contacts cannot appear in Sicily summaries.

## 2.7.5

- Replaces the dashboard's oversized center labels with compact, collision-aware edge callouts and leader lines.
- Keeps every vessel marker visible while limiting labels to the cards that fit cleanly in the map.
- Adds collision-aware label placement to the TV map, using the sidebar for the complete live vessel list.
- Matches Rahamin AIS with heading-aware, type-specific vessel silhouettes for cargo, tanker, passenger, fishing, tug, sailing, rescue, and fixed AIS targets.

## 2.7.4

- Proxies both `/api/status?area=miami` and `/api/status?area=baiamonte` from the private Rahamin AIS Pi.
- Imports the Pi's separate cached Sicily vessels into the Baiamonte map with independent per-area health and record counts.
- Stops duplicate direct AISHub polling in Home Assistant while the private area proxy is enabled, so the Miami Pi remains the single reciprocal API client.
- Treats the private proxy as live on both the dashboard and TV area views.

## 2.7.3

- Separates receiver health from optional AISHub sharing, so a bad forwarding hostname can no longer label a working receiver as failed.
- Adds an explicit, default-off AISHub sharing switch; receiving, local decoding, and the Rahamin private proxy continue without it.
- Backs off and throttles optional forwarding failures instead of logging one error for every AIS packet.
- Clarifies network receiver and empty-area labels in the dashboard.

## 2.7.2

* Decodes raw single- and multipart AIS NMEA received through UDP, TCP, or serial network receiver modes so the private Rahamin Miami feed creates live map vessels.
* Adds a private Rahamin AIS status proxy, defaulting to the routed Miami Pi, for the same full local and inbound vessel dataset shown by the Miami dashboard.
* Labels privately received contacts with their configured network receiver and keeps map updates independent of AISHub API credentials.
* Pins pyais 3.2.1 in the Home Assistant image for standards-aware AIS message decoding.

## 2.7.1

* Adds independent Home Assistant options for showing live vessel markers on the dashboard and TV maps, with matching temporary map-toolbar toggles.
* Makes `/tv` open on the Baiamonte Sicily live map by default while preserving explicit `?area=miami` access.
* Defaults the TV count and compact side list to vessels actually inside the displayed map so Miami and off-map approach traffic cannot leak into the local view.

## 2.7.0

* Adds Rahamin Miami and Baiamonte Sicily as switchable map areas backed by the same server-side AISHub API proxy.
* Alternates area requests at the configured one-minute interval so the shared AISHub account is never polled faster than once per minute.
* Extends each API query by a configurable approach range and identifies contacts as In area, Inbound, or Nearby from AIS course and speed.
* Adds `/tv?area=miami` and `/tv?area=baiamonte`, plus touch-friendly area controls on the Home Assistant and Samsung TV maps.
* Adds station/source, vessel width, draught, destination, flag, and approach status to compact vessel details.

## 2.6.0

* Adds automatic browser light/dark mode to the Home Assistant AIS dashboard.
* Aligns cards, status badges, muted text, controls, and map chrome with ADS-B and Vineyard Operations.
* Keeps the fullscreen TV display dark for distance viewing while retaining the brighter basemap.

## 2.5.0

* Adds an optional second-Nooelec marine VHF receiver using RTLSDR-Airband 5.2.0 in NFM scan mode.
* Adds a Baiamonte-styled Marine radio sidebar page with live in-app audio, receiver state, tuning profile, and configured channel cards.
* Adds matching device, frequency, label, gain, PPM, and squelch controls and prevents AIS and marine services from opening the same RTL-SDR.
* Supervises the scanner and private Icecast audio server, includes their output in the Watch Area receiver log, and stops both cleanly with the app.
* Exposes marine receiver health through the dashboard API and Home Assistant connection sensor attributes.

## 2.4.1

* Brightens the fullscreen AIS basemap to match the corrected Rahamin ADS-B TV profile and reduces the decorative map shade without changing vessels, labels, or rain-radar opacity.

## 2.4.0

* Bundles AIS-catcher v0.70 for end-to-end, dual-channel RTL-SDR AIS reception inside the Home Assistant app.
* Adds a Nooelec NESDR SMArt v5 starting profile with device, gain, PPM, AGC, bias-tee, and decoder-bandwidth controls.
* Displays locally decoded AIS-catcher vessels immediately while preserving and optionally forwarding their original NMEA messages to AISHub.
* Supervises and restarts the decoder, logs its radio profile and output, and exposes decoder health on the Watch Area page and status API.

## 2.3.2

* Adds compact vessel callouts directly on the Home Assistant overview map with flag, identity, vessel type, speed, destination, and last-seen time.
* Adds a touch-friendly selected-vessel map mode with an expanded detail panel.
* Links the map display selector directly to the Samsung-compatible TV split view and remembers the preferred dashboard mode per browser.

## 2.3.1

* Prevents hidden Home Assistant pages and zero-size layouts from clearing or misplacing overview-map vessels.
* Re-renders the map after page, visibility, resize, and saved-height changes while preserving the last good AIS state.
* Keeps a valid receiver status during brief dashboard refresh failures and reports an update delay instead.
* Aligns vessel markers with the same Mercator projection used by map and weather tiles.
* Expands MMSI flag-state recognition from the official ITU MID table and shows flags on the map, recent contacts, Live traffic, and TV view when identifiable.

## 2.3.0

* Adds UDP, TCP, and serial AIS receiver profiles with port, baud, and channel settings.
* Adds automatic USB GPS support for map centering and vessel distance ranking.
* Adds independently configurable live rain radar on the dashboard and TV map.
* Adds optional FlightAware AeroAPI airport observations for the Watch Area page.
* Adds receiver activity logs to Watch Area and compact flag, identity, vessel-type, and destination cards.
* Aligns navigation names and map controls with Baiamonte ADS-B and LTE styling.
* Adds pinch-to-zoom and Samsung/Tizen TV fallbacks while retaining same-origin map and weather proxies.

## 2.2.2

* Accepts RainViewer's current alphanumeric radar-frame identifiers so precipitation tiles render on dashboards and TV displays.

## 2.2.1

* Treats AISHub's metadata-only zero-vessel response as a healthy empty result.
* Accepts documented and common proxied AISHub JSON response shapes while preserving useful provider errors.
* Replaces the inherited UK example watch area with a Baiamonte-centred Sicily and surrounding-seas area.
* Automatically migrates installations that still use the exact legacy example bounds.
* Proxies and caches OpenStreetMap tiles through the app so TV and kiosk browsers can render the basemap without direct cross-origin tile access.
* Proxies RainViewer metadata and precipitation tiles through the same local app for reliable TV weather overlays.
* Adds a settings selector for Standard, Humanitarian, Topographic, Dark, and Satellite base maps.

## 2.2.0

* Includes the live RainViewer precipitation-radar overlay, timestamp and source attribution from version 2.1.2.
* Includes drag, zoom, reset and saved vertical-resizing controls for the Overview vessel map.
* Ranks positioned vessels by distance from the configured Baiamonte watch-area centre.
* Adds a compact `nearest_vessels` top-10 feed for Home Assistant dashboards.
* Moves the default TV/kiosk host port to 8999 while retaining internal ingress port 8099.

## 2.1.1

* Added a full-screen TV feed at `/tv` with a large geographic map and Baiamonte-styled live vessel list.
* Exposed the dashboard on host port 8099 for TV and kiosk access.
* Added automatic map fitting, vessel headings, vessel labels, maritime flags, vessel details, and ten-second display refreshes.

## 2.1.0

* Replaced AISStream with the reciprocal AISHub contributor network.
* Added local raw NMEA UDP input on port 10110 and forwarding to the dedicated AISHub feed destination.
* Added AIS hardware identification, source-change notices, message counts, forwarding confirmation, errors, and one-minute health summaries to app logs.
* Added AISHub and receiver-feed details to the Home Assistant connection entity and AIS sidebar.
* Removed the AISStream WebSocket dependency and anonymous uptime telemetry.

## 2.0.0
* Rebranded the app as Baiamonte AIS with an **AIS** Home Assistant sidebar entry.
* Added a responsive ingress dashboard matching Baiamonte LTE and Vineyard Overview styling.
* Added a live bounding-box vessel plot, fleet registry, receiver health, watch-area view, and operations journal.
* Added new Baiamonte maritime app-store and dashboard icon artwork.
* Namespaced Home Assistant entities under `sensor.baiamonte_ais_*`.
* Publishes supported `amd64` and `aarch64` Home Assistant images through GitHub Actions.

## 1.4.6
* [Fix] Enhanced reconnection logic after a couple of recent AISStream outages. The app should now gracefully reconnect when the service comes back up. 
* [Fix] Fixed accuracy of outage status for sensor.ais_connection_status entity

## 1.4.5
* [Feature] The connectivity status entity (sensor.ais_connection_status) is now integrated with Buttermilkgreen uptime monitor API (https://aisuptime.buttermilkgreen.fyi/). This can be disabled or you can add a custom URL if you are self hosting the uptime monitor. If disabled, the entity will show connectivity status relative to your connection to the AISStream websocket. Note: If you were using the specific responses sent by the API before for any automations, these will be different when using the Uptime API connection. See docs for responses. 

## 1.4.0
* [Feature] Filter ships by MMSI. Enter one or many MMSI numbers (comma separated) into the filter field in the config to only show those ships. 

## 1.3.0
* [Feature] Additional attributes added. Note these are all part of ShipStaticData and update every ~6 minutes:
  * ship_length: The total physical length of the vessel in metres
  * imo_number: The unique, permanent 7-digit identifier assigned to the hull
  * call_sign: The vessel's unique alphanumeric maritime radio call sign
  * vessel_type: The categorisation of the ship, such as "Cargo Ship", "Pleasure Craft", or "Search and Rescue".
  * destination: The intended port or location the vessel is sailing towards. Note this is manually updated by crew so may be inaccurate
  * eta: The projected arrival time at the destination, formatted as DD/MM HH:MM UTC. Note this is manually updated by crew so may be inaccurate
* [Feature] Added documentation tab to the add-on
* [Fix] Stale ships are now reliably removed in general and on restart
* [Fix] Config changes are properly applied on restart


## 1.2.1
* [Fix] Ordered bounding box fields in config to match values from bboxfinder.com for easier input

## 1.2.0
* [Feature] Ability to track multiple ships on a map card (auto-entities custom map card from HACS is recommended)
  * [Config] "Multi-Ship Tracking" - Enables this feature
  * All ships that enter the bounding box will have an entity created in the format sensor.ais_ship_{mmsi}
  * Ship entities that no longer exist in the bounding box will have the GPS co-ordinates cleared after 30 minutes of no updates (default)
  * Icons show the status of each ship. See documentation.
  * [Config] "Ship Timeout" - how long before ships that stop reporting are cleared from the map
  * [Config] "Clear Ships on Startup" - Remove all ship entities every time add on restarts 
* [Feature] Ability to track Class B vessels (smaller boats like yachts, sailing boats etc) along with attribute: vessel_class. 
  * [Config] "Enable Class B Vessels" - enables this feature
* [Feature] AISStream connectivity is now available in a new entity sensor.ais_connection_status along with attribute: error_message
* [Feature] Clearer logs to spot issues
* [Fix] Fixed an issue where the last_passing_ship entity attributes were not updated, despite getting updates from AISStream 

## 1.1.0
* [Feature] Simplified bounding box entry into the 4 co-ordinates needed. 
* [Feature] Additional attributes added to the Last Passing Ship entity:
  * latitude: The exact GPS latitude coordinate.
  * longitude: The exact GPS longitude coordinate.
  * speed_knots: The vessel's speed over ground.
  * course: The vessel's direction of travel in degrees.
  * heading: The direction the ship's bow is pointing in degrees.
  * navigational_status: A readable status of the ship (e.g., "Under way using engine").
* [Feature] Test Mode toggle which creates a separate entity called Dev - Last Passing Ship.

## 1.0.0
* Initial release
