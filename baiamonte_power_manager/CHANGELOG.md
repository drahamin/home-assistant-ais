# Changelog

## 0.3.1

- Replace comma-separated shedding and protection fields with searchable, touch-friendly Home Assistant switch selectors.
- Show friendly device names and current switch state while retaining entity IDs internally for safe validation.
- Automatically move a switch out of its previous editable category when it is selected elsewhere.

## 0.3.0

- Add a full Baiamonte-styled Web UI with overview, configuration, and trend pages.
- Add persistent, validated Web UI editing for load categories, thresholds, timing, and telemetry entities.
- Add a live battery gauge plus power-flow, SOC, and protected-runtime graphs.
- Add forecast margin, minimum planning load, recovery thresholds, history window, and learning-rate controls.
- Add branded Home Assistant store, sidebar, browser, and mobile icons.
- Add weather-discounted Solcast inputs, sunrise-aware overnight survival planning, seasonal/monthly load learning, and configurable solar-credit limits.
- Keep Nokia LTE as the default first-shed load while preserving hard protection for the estate main, cameras, and kitchen refrigerator.

## 0.2.0

- Move Nokia LTE out of hard protection and into its own editable shed-first category.
- Expand load categorization to four ordered stages configurable from the app settings.

## 0.1.0

- Add conservative learned runtime forecasting with hourly load history.
- Add three-stage, allow-listed load shedding and managed-only restoration.
- Hard-protect the estate main breaker and default camera, LTE, and refrigerator loads.
- Freeze all automatic actions when battery telemetry is invalid.
- Add an ingress status dashboard and Home Assistant status entities.
