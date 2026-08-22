# Changelog

## 2.7.31

- Rejects malformed, out-of-area, and confidently inland AIS positions before they enter the live vessel cache or Home Assistant entities.
- Uses a conservative coastline-aware land test so legitimate vessels in ports and close coastal waters remain visible.
- Purges older invalid cached targets and reports rejection diagnostics in the full status response.

## 2.7.29

- Rejects an entire private-proxy snapshot when its own generation time is stale, preventing old cached vessel positions from replacing the live Baiamonte receiver feed.
- Runs stale-vessel cleanup in private-proxy-only mode as well as direct AISHub mode, so the TV and dashboard maps recover cleanly after an upstream outage.

## 2.7.28

- Uses the AIS app's verified local ingestion time as the map freshness clock before consulting a proxy's source timestamp.
- Keeps live Sicily targets visible when an upstream proxy reports timezone-less timestamps in its own local timezone.

## 2.7.27

- Restores AIS vessel markers on the dashboard and TV map by treating timezone-less receiver timestamps as UTC instead of Samsung/Rome local time.
- Emits explicit UTC timestamps for new local AIS contacts and status snapshots so browsers apply the stale-contact window consistently.
- Preserves offset-aware and Unix timestamps from private proxy feeds.

## 2.7.26

- Fixes Marine VHF playback being disconnected by a browser-generated pause event while a new live source is starting.
- Uses the unambiguous Start listening and Stop listening controls instead of mixing native player controls with automatic disconnect behavior.
- Forwards 4 KB Icecast audio chunks immediately instead of waiting for a 16 KB block, substantially reducing first-audio delay on low-bitrate marine streams.
- Shows Connecting, Buffering, Listening, reconnecting, and connection-failure states beside the controls.

## 2.7.25

- Adds a Home Assistant **TV Map Target Size** setting from 30% to 180% for `/tv` vessel symbols.
- Uses a more readable 100% default for direct desktop and television browsers instead of the previous compact 70% fallback.
- Keeps the existing `target_size` URL parameter as a per-display override, so Vineyard and other embedded dashboards can retain their own scale.

## 2.7.24

- Keeps dashboard and TV vessel symbols on their true reported coordinates instead of moving them across land to avoid overlap.
- Filters stale contacts on the normal dashboard as well as the TV page and keeps symbols a fixed readable size while the dashboard map is zoomed.
- Uses a closer Catania-coast home view and a smaller TV vessel default so the Sicily display shows useful local traffic instead of an overcrowded whole-island view.

## 2.7.23

- Identifies the fresh vessels in the native TV rail so the Vineyard Operations kiosk can mirror exactly the contacts inside the current AIS viewport.
- Centers the Sicily TV map on the nearest fresh coastal contact, with a Catania-coast fallback when the receiver is quiet.
- Removes misleading decluttering vectors from the TV map and permits compact 30% vessel targets.

## 2.7.22

- Keeps Sicily and Miami TV maps fixed to their configured watch areas instead of expanding them around every cached target.
- Shows and lists only fresh vessels inside the current panned/zoomed TV viewport, with the stale timeout visible in the status line.
- Applies Vineyard Operations TV zoom and target-size settings directly in the AIS map.

## 2.7.21

- Separates AIS markers that project to the same few pixels, so harbor traffic no longer appears as only a handful of stacked vessels.
- Draws a subtle line from each separated marker back to its true reported position.
- Applies the same deterministic, stable decluttering to Overview, `/tv`, and `/t`, while leaving the full live count unchanged.

## 2.7.20

- Uses the Rahamin source area's own coverage bounds and expands them to contain every accepted target, so proxy vessels cannot remain off-screen behind Baiamonte's local map rectangle.
- Removes the remaining ten-vessel limit from the positioned/nearest collection used by dashboard consumers.
- Keeps the `/tv` map, live count, and scrollable vessel rail aligned with the complete positioned target set.
- Supports `/t` as a short alias for the same unlimited TV vessel view.

## 2.7.19

- Keeps valid Rahamin AIS area targets when the Miami and Baiamonte installations use different local watch-area bounds.
- Accepts both Baiamonte dashboard fields and standard uppercase AIS fields from the private Rahamin proxy.
- Supports current, legacy, and compact proxy vessel-list payloads while continuing to reject invalid identities and coordinates.

## 2.7.18

- Adds explicit Start listening and Stop listening controls to Marine radio.
- Makes Pause close the live MP3 request instead of leaving its endless Icecast connection running in the background.
- Automatically disconnects VHF audio when leaving the Marine radio page, hiding Home Assistant, closing the browser view, or navigating away.
- Requires a deliberate Start listening action for each new connection, preventing refreshes from silently reconnecting stopped audio.

## 2.7.17

- Fixes the marine receiver's solid-tone audio by explicitly selecting narrowband FM instead of RTLSDR-Airband's default AM demodulation.
- Uses RTLSDR-Airband's adaptive per-channel squelch by default, preventing a fixed threshold from holding the scanner open on local noise or an RF spur.
- Adds an Automatic Marine VHF Squelch option while retaining the manual dBFS threshold for unusual installations.
- Clarifies that Marine radio is one scan feed that cycles through every configured channel and pauses on active traffic.

## 2.7.16

- Supports two identical RTL-SDRs with independent AIS and marine VHF assignments by index, unique serial, automatic selection, or stable physical `port:<USB-port>` selector.
- Detects duplicate factory serials and refuses ambiguous assignments instead of allowing both services to contend for the same dongle.
- Adds an opt-in manual Marine radio recovery control that stops the scanner and resets only its resolved USB device.
- Adds optional automatic marine USB recovery with a configurable 0–5 reset limit; normal process restarts continue without resetting the AIS radio.
- Reports resolved device assignments, discovered RTL-SDR ports, reset counts, and recovery errors in the dashboard and receiver log.

## 2.7.15

- Adds an explicit AIS Network Data Source setting that defaults to the Rahamin Miami single-key private proxy.
- Makes the selected data source authoritative, preventing the Baiamonte app from polling AISHub while proxy mode is active even if an older username remains stored.
- Keeps Direct AISHub as a deliberate standalone fallback and reports whether its credential is actually in use without exposing it.
- Continues importing independent Miami and Baiamonte caches from `http://192.168.86.196:8999/api/status` while raw receiver NMEA remains separate on UDP port 10110.

## 2.7.14

- Adds an area-filtered compact `/api/status?view=tv&area=...` response so the Sicily TV no longer downloads and parses the complete Miami vessel dataset and unused dashboard logs every ten seconds.
- Stops `/tv` polling while its browser tab is hidden and rejects a late response after the user switches map areas.
- Reduces base-map and radar tile memory caches from 256+256 entries to 128+64 entries while retaining ample capacity for normal TV pan and zoom.
- Preserves the full unfiltered dashboard API response and all visible TV targets.

## 2.7.13

- Fixes the confirmed `/tv` layout jump where the complete vessel list expanded the map from the viewport height to more than 10,000 pixels after live targets loaded.
- Constrains the TV grid row, map, fleet sidebar, and scrolling vessel list to the browser viewport at every target count.
- Keeps the map at the same home centre and pixel dimensions when the live vessel list populates or refreshes.

## 2.7.12

- Locks the `/tv` map at its cached home centre and zoom by default so Samsung remote pointer glitches cannot drag it away from vessel targets.
- Adds an explicit Move/Lock control; pan and pinch are accepted only while Move mode is enabled.
- Makes Reset return to the cached home view, cancel all pointer state, and lock movement immediately.
- Guards against missing mouse-release and pointer-capture events that can otherwise leave TV browsers in a continuous drag.
- Disables caching for the TV HTML, JavaScript, and CSS and displays the running AIS version in the TV status footer.

## 2.7.11

- Fixes `/tv` map drift by using the fixed centre of the selected AIS watch area instead of following changing GPS snapshots.
- Removes the automatic 30-second and browser-resume recentering that could unexpectedly move the TV map.
- Makes the Reset button immediately cancel any queued drag repaint and restore the fixed home centre and fitted zoom.
- Keeps the chosen pan and zoom exactly unchanged during AIS data refreshes.

## 2.7.10

- Stabilizes the AIS TV map by retaining already-loaded base-map and radar tiles during the 10-second vessel refresh.
- Makes the TV Home control return to the live AIS GPS/reference location for Baiamonte, or the configured area centre for other map areas.
- Prevents touch-capable TV browsers from running duplicate pointer and legacy touch gestures, and throttles drag rendering for smoother Samsung TV operation.
- Serializes `/tv` status refreshes so an older response cannot repaint a newer map state.
- Returns the Overview and `/tv` maps Home after 30 seconds without map interaction and whenever the page resumes.
- Discards stale RainViewer results on both map surfaces so weather remains aligned with the current view.
- Keeps the map controls intact across data refreshes and refreshes cleanly after a TV browser resumes from sleep.

## 2.7.9

- Removes the ten-vessel cap from the `/tv` target sidebar so every live vessel in the selected area is included.
- Makes the complete compact list scrollable by wheel, touch, Samsung TV arrow keys, Page Up/Down, Home, and End while preserving position across feed refreshes.

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
# 2.7.30

- Accept fresh private-proxy snapshots whose timestamps use the source server's local clock without a UTC offset.
- Continue rejecting stale individual vessels by comparing them with the proxy snapshot time, while retaining absolute stale-snapshot protection for timezone-aware sources.
