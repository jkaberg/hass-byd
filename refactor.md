# hass-byd Refactor: Adapt to reworked pyBYD client

## 1. Coordinator rewrite (`coordinator.py`)
- [x] 1a. Remove `StateSection` import and `_hydrate_store_model()` helper
- [x] 1b. Rewrite `BydApi._ensure_client()` — remove `response_trace_recorder`, add `on_vehicle_info` MQTT callback
- [x] 1c. Rewrite `BydDataUpdateCoordinator._async_update_data()` — capture returned models directly
- [x] 1d. Retain conditional fetching with local state tracking (`_last_realtime`, `_last_charging`, `_last_hvac`)
- [x] 1e. Rewrite `BydGpsUpdateCoordinator._async_update_data()` — capture returned `GpsInfo` directly
- [x] 1f. Wire MQTT push via `on_vehicle_info` callback → `async_set_updated_data()`
- [x] 1g. Reimplement debug dumps via `model.model_dump(mode="json")`
- [x] 1h. Remove `stale_after` parameter usage

## 2. Climate entity (`climate.py`)
- [x] 2a. Import `ClimateStartParams` and rewrite all `start_climate` calls to use it
- [x] 2b. Pass temperature in °C directly (remove local `_celsius_to_scale()` conversion)
- [x] 2c. Convert `time_span` from user-facing minutes to pyBYD codes (1-5)
- [x] 2d. Update `async_set_preset_mode` to use `ClimateStartParams`

## 3. Switch entities (`switch.py`)
- [x] 3a. Import `BatteryHeatParams`, `SeatClimateParams`, `ClimateStartParams`
- [x] 3b. `BydBatteryHeatSwitch` → use `BatteryHeatParams`
- [x] 3c. `BydCarOnSwitch` → use `ClimateStartParams(temperature=21.0, time_span=1)`
- [x] 3d. `BydSteeringWheelHeatSwitch` → use `SeatClimateParams`

## 4. Select entities (`select.py`)
- [x] 4a. Import `SeatClimateParams` and replace `set_seat_climate(**kwargs)` with `params=SeatClimateParams(**kwargs)`

## 5. Sensor entities (`sensor.py`)
- [x] 5a. Rename `charge_remaining_hours` → `remaining_hours`, `charge_remaining_minutes` → `remaining_minutes`

## 6. Constants (`const.py`)
- [x] 6a. Add Finland `("FI", "fi")` to `COUNTRY_OPTIONS`
- [x] 6b. Add `CLIMATE_DURATION_TO_CODE` mapping `{10: 1, 15: 2, 20: 3, 25: 4, 30: 5}`

## 7. Fix translations across ALL entity platforms
- [x] 7a. Sensor platform — remove hardcoded `name=`, use `translation_key` for all 59 sensors
- [x] 7b. Binary sensor platform — remove hardcoded `name=`, use `translation_key` for all 28 sensors
- [x] 7c. Select platform — remove hardcoded `name=`, use `translation_key` for all 8 selects
- [x] 7d. Button platform — remove hardcoded `name=`, use `translation_key`; add `force_poll` to strings
- [x] 7e. Switch platform — replace `_attr_name = "Disable polling"` with `_attr_translation_key = "disable_polling"`

## 8. Update `strings.json`
- [x] 8a. Add `force_poll` and `disable_polling` entity keys
- [x] 8b. Rename sensor keys `charge_remaining_hours` → `remaining_hours`, `charge_remaining_minutes` → `remaining_minutes`
- [x] 8c. Add Finland to config step country list

## 9. Update all 21 existing translation files
- [x] 9a. Add `force_poll`, `disable_polling` keys to all translation files
- [x] 9b. Rename `charge_remaining_hours`/`charge_remaining_minutes` in all translation files
- [x] 9c. Add Finland to country lists in all translation files

## 10. Add Finnish translation (`translations/fi.json`)
- [x] 10a. Create complete `fi.json` with all entity names, config flow, select states, climate presets

## 11. Init module (`__init__.py`)
- [x] 11a. Wire MQTT callbacks — pass coordinator references to `BydApi` for MQTT dispatch
