# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

hass-byd is a Home Assistant custom integration for BYD battery systems. It is a Python project following Home Assistant's custom component conventions.

## Tech Stack

- **Language:** Python
- **Framework:** Home Assistant (custom integration)
- **Linting:** Ruff
- **Type checking:** MyPy
- **Testing:** Pytest

## Expected Project Structure

Home Assistant custom integrations follow a standard layout:

```
custom_components/byd/
  __init__.py       # Integration setup
  manifest.json     # Integration metadata (domain, dependencies, version)
  config_flow.py    # UI-based configuration
  const.py          # Constants
  sensor.py         # Sensor platform entities
  coordinator.py    # DataUpdateCoordinator for polling
```

## Home Assistant Integration Patterns

- Integrations are defined by `manifest.json` with a unique `domain` identifier
- Configuration uses config flows (`config_flow.py`) for UI setup
- Data fetching should use `DataUpdateCoordinator` to centralize polling and share data across entities
- Entity platforms (sensor, binary_sensor, etc.) register entities via `async_setup_entry`
- All I/O must be async or wrapped in `hass.async_add_executor_job()`
