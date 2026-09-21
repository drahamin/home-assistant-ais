# Baiamonte Power Guard

Baiamonte Power Guard is a local, fail-safe energy manager for the Tenuta
Baiamonte Home Assistant system. It forecasts usable battery runtime, learns the
estate's load by hour, and reduces noncritical loads before the inverter reaches
its low-battery cutoff.

It cannot guarantee uninterrupted power: battery faults, inverter trips,
wiring faults, an exhausted battery, or failed switching hardware can still
cause an outage. Its purpose is to extend runtime and preserve the most useful
services for as long as the available energy permits.

## Fixed safety boundary

The app never sends commands to the BMS or inverter. It cannot change charge
limits, discharge limits, battery protections, or the estate main breaker.
These loads are hard-protected in code and rejected if they are also entered in
a shedding category:

- Estate main breaker
- Camera circuit
- Kitchen refrigerator

The Nokia LTE backup is **not** protected. It is assigned to the editable
**Shed first** category by default. The Starlink connectivity sensor is monitored
as a health signal. Confirm which
physical circuit powers Starlink, the router, the Omada switch, Home Assistant,
and all required PoE cameras; add every corresponding controllable switch to
**Never shed** before enabling automatic mode.

## Load order

The supplied Baiamonte profile uses four editable categories in this order:

1. **Shed first / Economy:** Nokia LTE backup.
2. **Conserve:** dishwasher, washing machine, and ice maker.
3. **Protect:** wine refrigerator and cistern pump.
4. **Emergency:** lighting circuit.

The kitchen refrigerator, cameras, and estate main stay powered. A switch
is operated only if it is explicitly listed in one of the four categories. The app
records which loads it turned off and restores only those loads; it does not
undo a switch that a person turned off.

All four category fields are editable in the app Configuration page. Entity IDs
are comma-separated. A switch may appear in only one category, and any overlap
with a hard-protected load prevents the app from starting rather than guessing.

## How the learning works

Every evaluation stores an exponentially weighted load average plus a separate
average for each hour of the day. The runtime calculation deliberately uses the
highest credible value among live estate load, learned hourly load, and learned
battery discharge. A brief low-power reading therefore cannot create an
optimistic runtime estimate. Learning data and the managed-load journal survive
app restarts in `/data/power_guard_state.json`.

Decisions use both battery SOC and predicted runtime. Runtime can trigger
conservation before a simple SOC threshold would. Missing or invalid battery
telemetry freezes automatic switching and reports `telemetry_lost`.

## Commissioning

1. Install the app and leave **Control mode** set to `observe`.
2. Confirm the configured entity IDs exist and show plausible SOC, signed
   battery power, remaining energy, estate load, solar power, and connectivity.
3. Confirm the nominal-energy sensor reflects the installed pack count (for
   example, 10.24 kWh for two 5.12 kWh packs or 15.36 kWh for three).
4. Confirm negative battery power means charging. If the installed source uses
   the opposite sign, do not enable automatic restoration until it is adapted.
5. Review **Shed first**, **Stage 2**, **Stage 3**, and **Emergency** in the app
   Configuration page. Move switch entity IDs between categories as needed.
6. In Power Guard, compare its runtime and recommendations with the battery and
   main-meter history for at least one normal charge/discharge cycle.
7. Physically trace the Starlink/router/Home Assistant/PoE supply. Add those
   switches to **Never shed**. Never assume that a friendly name proves the
   downstream wiring.
8. Test each shedding-category switch individually in Home Assistant and confirm it
   does not remove Home Assistant, Internet, cameras, refrigeration, a safety
   system, medical equipment, or another essential load.
9. Change **Control mode** to `automatic`, save, and restart the app.

## Anti-flapping and recovery

A risk state must remain present for **Confirm seconds** before it is accepted.
Recovery is deliberately slower: the normal state must remain stable for
**Stable recovery time**, SOC must be above the restore threshold, and the
battery must be charging or solar must exceed the live load. This avoids
cycling compressors and relays when clouds or large loads cause short swings.

## Home Assistant entities

- `sensor.baiamonte_power_guard_status`
- `sensor.baiamonte_power_guard_estimated_runtime`
- `sensor.baiamonte_power_guard_learned_load`

The status entity attributes include the decision reason, control mode,
expected load, and the exact entities currently managed off.

## Recommended hardware layer

Software load management is only one layer. Put the ONT/Starlink terminal,
router, managed switch, Home Assistant host, camera recorder/HomeBase, and
critical PoE equipment on a correctly sized UPS or protected DC supply. Use
properly rated contactors for high-current loads; do not rely on consumer smart
plugs beyond their ratings. Electrical changes should be completed and tested
by a qualified electrician.
