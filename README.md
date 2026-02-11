# BYD Vehicle Integration for Home Assistant

## Description

The `byd_vehicle` integration connects Home Assistant to the BYD cloud service
using the pybyd library. It adds sensors for vehicle telemetry and energy data,
GPS tracking, and basic remote commands.

## Installation

This integration is not in the default HACS store. Install it as a custom
repository.

### HACS (Custom Repository)

1. Open HACS and go to Integrations.
2. Open the menu and select Custom repositories.
3. Add the repository URL and select Integration.
4. Install the integration.
5. Restart Home Assistant.
6. Add "BYD Vehicle" from Settings > Devices & Services.

### Manual

1. Open your Home Assistant configuration directory.
2. Create `custom_components` if it does not exist.
3. Copy `custom_components/byd_vehicle/` from this repository into your
	configuration directory.
4. Restart Home Assistant.
5. Add "BYD Vehicle" from Settings > Devices & Services.

## Configuration

Configuration is done through the Home Assistant UI.

Go to Settings > Devices & Services > Integrations, click Add Integration, and
search for "BYD Vehicle".

### Configuration Variables

| Name | Type | Required | Description |
|------|------|----------|-------------|
| Username | string | yes | BYD account username (email or phone). |
| Password | string | yes | BYD account password. |
| Region | string | yes | API region endpoint (Europe or Australia). |
| Country code | string | yes | ISO country code used for API requests. |
| Poll interval | int | no | Telemetry polling interval in seconds. |
| GPS poll interval | int | no | GPS polling interval in seconds. |

The integration uses the Home Assistant time zone and derives language from the
selected country code.

## Entities

- Sensors for vehicle, realtime, energy, and GPS fields
- Device tracker for vehicle location
- Climate (start/stop)
- Lock (lock/unlock)
- Switches for flash lights and horn

## Notes

This integration relies on the BYD cloud API and account permissions. Data
availability and command support can vary by vehicle and region.
