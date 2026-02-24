# BYD Vehicle Integration for Home Assistant

## PLEASE READ FIRST!

> [!WARNING]
> This integration and the subsequent library is in an alpha stage, especially the library needs work to map out all the available API states.
> You have been warned.


## Description

The `byd_vehicle` integration connects Home Assistant to the BYD cloud service
using the [pyBYD](https://github.com/jkaberg/pyBYD) library. It provides
extensive vehicle telemetry, GPS tracking, climate control, door locks, seat
climate, and remote commands for BYD vehicles.

## Installation

This integration is not in the default HACS store. Install it as a custom repository.

### HACS (Custom Repository)

1. Open HACS and go to **Integrations**.
2. Open the three-dot menu and select **Custom repositories**.
3. Add the repository URL and select **Integration** as the category.
4. Search for "BYD Vehicle" and install the integration.
  - HACS will install the latest published release by default; for dev/testing you can open the repository in HACS and select the `main` branch as the download target.
5. Restart Home Assistant.
6. Add "BYD Vehicle" from **Settings > Devices & Services**.

### Manual

1. Open your Home Assistant configuration directory.
2. Create `custom_components/` if it does not exist.
3. Copy `custom_components/byd_vehicle/` from this repository into your
   configuration directory.
4. Restart Home Assistant.
5. Add "BYD Vehicle" from **Settings > Devices & Services**.

## Configuration

Configuration is done entirely through the Home Assistant UI (config flow).

> [!IMPORTANT]
> **IMPORTANT**: Please use an shared account (not the account you use with the app) for the integration; using the same account as the app will log you out in the app. It's also vital that you set up and command pin in the app prior to setting up the integration, an command pin must be set for commands to work.

> [!IMPORTANT]
> **INVALID AUTHENTICATION**: This is exactly that, or that you've choosen the wrong Region. We haven't worked out an proper Country->Region mapping so please try an diffrent region and try again. The Region corresponds to which servers the integration will use.

Go to **Settings > Devices & Services > Integrations**, click **Add
Integration**, and search for **BYD Vehicle**.

### Setup fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| Region | select | yes | Europe | API region endpoint (for example `Europe`, `Singapore/APAC`, `Australia`, `Brazil`, `Japan`, `Uzbekistan`, `Middle East/Africa`, `Mexico/Latin America`, `Indonesia`, `Turkey`, `Korea`, `India`, `Vietnam`, `Saudi Arabia`, `Oman`, `Kazakhstan`). |
| Username | string | yes | | BYD account username (email or phone). |
| Password | string | yes | | BYD account password. |
| Control PIN | string | no | | Optional 6-digit PIN required for remote commands (lock, climate, etc.). |
| Country | select | yes | Netherlands | Country used for API country code and language. |
| Polling interval | int | no | 300 | Vehicle data polling interval in seconds (allowed range: 30-900). |
| GPS polling interval | int | no | 300 | GPS location polling interval in seconds (allowed range: 30-900). |
| Smart GPS polling | bool | no | false | When enabled, uses different intervals depending on whether the vehicle is moving. |
| GPS active interval | int | no | 30 | GPS polling interval in seconds while the vehicle is moving (smart GPS, allowed range: 10-300). |
| GPS inactive interval | int | no | 600 | GPS polling interval in seconds while the vehicle is parked (smart GPS, allowed range: 60-3600). |
| Climate duration | int | no | 1 | Climate run duration in minutes for start-climate commands (allowed range: 1-60). |
| Debug dump API responses | bool | no | false | When enabled, writes redacted BYD API request/response traces to local JSON files for troubleshooting. |


## Notes

- This integration relies on the BYD cloud API and account permissions. Data
  availability and command support can vary by vehicle model and region.
- Unsupported command endpoints, cloud rate-limits, and control PIN lockouts are
  surfaced as explicit entity errors.
- When BYD reports a remote command endpoint as unsupported for a VIN, affected
  command entities become unavailable for that vehicle.
- The integration uses cloud polling (`cloud_polling` IoT class). Data freshness
  depends on the configured polling intervals.
- The `Last updated` sensor now reflects canonical telemetry freshness and only
  advances when core telemetry values change (realtime/charging/HVAC/energy
  material fields), not merely when transport timestamps churn.
- A dedicated `GPS last updated` diagnostic sensor exposes canonical GPS
  freshness side-by-side with telemetry freshness.
- Telemetry adaptive polling uses this same canonical telemetry freshness signal;
  GPS updates do not advance `Last updated`.
- Realtime and GPS fetches now use pyBYD cache-aware `stale_after` behavior,
  allowing scheduled coordinator polls to skip expensive trigger/poll API calls
  when MQTT/cache data is already fresh.
- A unique device fingerprint is generated per config entry to identify the
  integration to the BYD API.

### Debug dumps

When **Debug dump API responses** is enabled in integration options, BYD API
request/response traces are written to:

- `.storage/byd_vehicle_debug/`
- Home Assistant config path example: `/config/.storage/byd_vehicle_debug/`

Each trace is stored as a timestamped JSON file. This is intended only for
short-term troubleshooting because API payloads can contain sensitive metadata.

Behavior details:

- Disabled by default.
- Captures transport-level API request/response traces.
- Applies field redaction for common secrets before writing files.

### Debug logging (Home Assistant + pyBYD)

To enable verbose runtime logs from both this integration and the underlying
`pybyd` library, add this to your Home Assistant `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.byd_vehicle: debug
    pybyd: debug
```

Then restart Home Assistant (or reload YAML configuration for logger settings)
and reproduce the issue.

Where to view logs:

- **Settings → System → Logs** in Home Assistant UI
- `home-assistant.log` in your HA config directory

Tip: enable debug logging only while troubleshooting, as it can produce large
log volumes and may include sensitive vehicle metadata.
