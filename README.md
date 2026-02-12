# BYD Vehicle Integration for Home Assistant

## PLEASE READ FIRST!

This integration and the subsequent library is an alpha stage, especially the library needs work to map out all the metr

You have been warned.

## Description

The `byd_vehicle` integration connects Home Assistant to the BYD cloud service
using the [pyBYD](https://github.com/jkaberg/pyBYD) library. It provides
extensive vehicle telemetry, GPS tracking, climate control, door locks, seat
climate, and remote commands for BYD vehicles.

Requires `pybyd>=0.0.12`.

## Installation

This integration is not in the default HACS store. Install it as a custom
repository.

### HACS (Custom Repository)

1. Open HACS and go to **Integrations**.
2. Open the three-dot menu and select **Custom repositories**.
3. Add the repository URL and select **Integration** as the category.
4. Search for "BYD Vehicle" and install the integration.
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

Go to **Settings > Devices & Services > Integrations**, click **Add
Integration**, and search for **BYD Vehicle**.

### Setup fields

| Field                 | Type   | Required | Default     | Description                                                                        |
| --------------------- | ------ | -------- | ----------- | ---------------------------------------------------------------------------------- |
| Region                | select | yes      | Europe      | API region endpoint (`Europe` or `Australia`).                                     |
| Username              | string | yes      |             | BYD account username (email or phone).                                             |
| Password              | string | yes      |             | BYD account password.                                                              |
| Control PIN           | string | no       |             | Optional 6-digit PIN required for remote commands (lock, climate, etc.).           |
| Country               | select | yes      | Netherlands | Country used for API country code and language.                                    |
| Polling interval      | int    | no       | 300         | Vehicle data polling interval in seconds.                                          |
| GPS polling interval  | int    | no       | 300         | GPS location polling interval in seconds.                                          |
| Smart GPS polling     | bool   | no       | false       | When enabled, uses different intervals depending on whether the vehicle is moving. |
| GPS active interval   | int    | no       | 30          | GPS polling interval in seconds while the vehicle is moving (smart GPS).           |
| GPS inactive interval | int    | no       | 600         | GPS polling interval in seconds while the vehicle is parked (smart GPS).           |

#### Supported countries

Australia, Austria, Belgium, Brazil, Colombia, Costa Rica, Denmark,
El Salvador, France, Germany, Hong Kong, Hungary, India, Indonesia, Japan,
Malaysia, Mexico, Netherlands, New Zealand, Norway, Pakistan, Philippines,
Poland, South Africa, South Korea, Sweden, Thailand, Turkey,
United Kingdom, Uzbekistan.

#### Supported Home Assistant UI languages

The integration includes Home Assistant translation files for:

- English (`en`)
- German (`de`)
- Portuguese (`pt`)
- Spanish (`es`)
- Danish (`da`)
- French (`fr`)
- Chinese Simplified (`zh-Hans`)
- Hungarian (`hu`)
- Indonesian (`id`)
- Italian (`it`)
- Japanese (`ja`)
- Malay (`ms`)
- Dutch (`nl`)
- Norwegian (`no`)
- Polish (`pl`)
- Korean (`ko`)
- Swedish (`sv`)
- Thai (`th`)
- Turkish (`tr`)
- Uzbek (`uz`)

### Options (reconfigure)

After initial setup, the polling intervals and smart GPS settings can be changed
from the integration's **Configure** menu under **Settings > Devices & Services**.

| Option                | Type | Default | Description                                            |
| --------------------- | ---- | ------- | ------------------------------------------------------ |
| Polling interval      | int  | 300     | Vehicle data polling interval in seconds.              |
| GPS polling interval  | int  | 300     | GPS location polling interval in seconds.              |
| Smart GPS polling     | bool | false   | Enable adaptive GPS polling based on vehicle movement. |
| GPS active interval   | int  | 30      | GPS interval while moving (seconds).                   |
| GPS inactive interval | int  | 600     | GPS interval while parked (seconds).                   |

## Entities

Each vehicle registered to your BYD account is added as a device. The following
entity platforms are created per vehicle.

### Sensors

| Sensor                              | Description                                    |
| ----------------------------------- | ---------------------------------------------- |
| Battery level                       | Current battery state of charge (%).           |
| Range                               | Estimated driving range (km).                  |
| Odometer                            | Total distance driven (km).                    |
| Speed                               | Current vehicle speed (km/h).                  |
| Cabin temperature                   | Interior temperature (°C).                     |
| Exterior temperature                | Outside temperature (°C).                      |
| Front/rear left/right tire pressure | Individual tire pressures (bar).               |
| Front/rear left/right tire status   | Individual tire status codes.                  |
| TPMS state                          | Tire pressure monitoring system state.         |
| Rapid tire leak                     | Rapid tire leak detection status.              |
| Charging SOC                        | Battery level reported by the charger (%).     |
| Time to full charge                 | Estimated time remaining until full (minutes). |
| Hours/minutes to full               | Charge time breakdown.                         |
| Charge remaining hours/minutes      | Remaining charge time (alternative).           |
| Charging state / Charge state       | Current charging status.                       |
| Charge wait status                  | Scheduled charge wait state.                   |
| Scheduled charging / hour / minute  | Scheduled charge timer details.                |
| Charger state / connection state    | Charger hardware status.                       |
| Charging last update                | Timestamp of last charging data update.        |
| Total energy consumption            | Cumulative energy consumed (kWh).              |
| Average energy consumption          | Average energy consumption (kWh/100km).        |
| Recent energy consumption           | Recent trip energy usage (kWh/100km).          |
| Recent 50km energy                  | Energy used over last 50 km (kWh).             |
| Electricity consumption             | Electricity consumption reading.               |
| Total power                         | Total power reading.                           |
| Power battery level                 | Power battery SOC (%).                         |
| EV endurance                        | EV-only range estimate (km).                   |
| Range V2 / Odometer V2              | Alternative range and odometer readings.       |
| Gear position                       | Current gear (P/R/N/D).                        |
| Fuel range / level / consumption    | Fuel-related readings (PHEV/hybrid).           |
| Engine status                       | Engine running state.                          |
| Electronic parking brake            | EPB status.                                    |
| Electric power steering             | EPS status.                                    |
| Electronic stability                | ESC status.                                    |
| ABS warning                         | ABS warning indicator.                         |
| Service vehicle soon                | Service reminder status.                       |
| Airbag warning                      | Airbag warning indicator.                      |
| Coolant temperature / warning       | Coolant temp and warning state.                |
| Power warning / Power system        | Power system warnings.                         |
| OTA upgrade status                  | Over-the-air update state.                     |
| PM2.5                               | In-cabin air quality (µg/m³).                  |
| Refrigerator / Refrigerator door    | Vehicle refrigerator status.                   |
| Fuel consumption                    | Fuel consumption reading.                      |

### Binary sensors

| Binary sensor                   | Description                                |
| ------------------------------- | ------------------------------------------ |
| Online                          | Vehicle cloud connectivity status.         |
| Charging                        | Whether the vehicle is currently charging. |
| Charger connected               | Whether a charger is plugged in.           |
| Doors                           | Aggregate door open/closed state.          |
| Front/rear left/right door      | Individual door states.                    |
| Trunk                           | Trunk open/closed.                         |
| Frunk                           | Front trunk open/closed.                   |
| Sliding door                    | Sliding door state.                        |
| Windows                         | Aggregate window open/closed state.        |
| Front/rear left/right window    | Individual window states.                  |
| Skylight                        | Skylight open/closed.                      |
| Locked                          | Aggregate lock state.                      |
| Front/rear left/right door lock | Individual door lock states.               |
| Sliding door lock               | Sliding door lock state.                   |
| Battery heating                 | Battery heating active.                    |
| Charge heating                  | Charge preheating active.                  |
| Sentry mode                     | Sentry/guard mode active.                  |
| Vehicle on                      | Vehicle power state.                       |

### Device tracker

GPS location of the vehicle, updated at the configured GPS polling interval.

### Climate

Full climate control entity with on/off, target temperature (15–31 °C), and
preset modes (Max Heat, Max Cool). Requires a control PIN.

### Lock

Lock and unlock the vehicle doors. Reports the combined state of all four door
locks. Requires a control PIN.

### Switches

| Switch                 | Description                                                  |
| ---------------------- | ------------------------------------------------------------ |
| Car on                 | Starts climate at 21 °C when on; turns climate off when off. |
| Battery heat           | Toggle battery heating on/off.                               |
| Steering wheel heating | Toggle steering wheel heater on/off.                         |

### Buttons

| Button        | Description                    |
| ------------- | ------------------------------ |
| Flash lights  | Flash the vehicle lights.      |
| Find car      | Trigger the find-my-car alert. |
| Close windows | Close all windows remotely.    |

### Select (seat climate)

| Select                      | Description               |
| --------------------------- | ------------------------- |
| Driver seat heating         | Off / Low / Medium / High |
| Driver seat ventilation     | Off / Low / Medium / High |
| Passenger seat heating      | Off / Low / Medium / High |
| Passenger seat ventilation  | Off / Low / Medium / High |
| Rear left seat heating      | Off / Low / Medium / High |
| Rear left seat ventilation  | Off / Low / Medium / High |
| Rear right seat heating     | Off / Low / Medium / High |
| Rear right seat ventilation | Off / Low / Medium / High |

## Remote commands

Remote command results (climate, lock, switches, buttons, seat climate) are
exposed as `last_remote_result` in each entity's attributes, including success
status, control state, request serial number, and API error details when
available (`error_code`, `error_endpoint`).

A **control PIN** (6-digit) is required for remote commands. Set it during
initial setup or update it by re-adding the integration.

## Notes

- This integration relies on the BYD cloud API and account permissions. Data
  availability and command support can vary by vehicle model and region.
- Unsupported command endpoints, cloud rate-limits, and control PIN lockouts are
  surfaced as explicit entity errors.
- When BYD reports a remote command endpoint as unsupported for a VIN, affected
  command entities become unavailable for that vehicle.
- The integration uses cloud polling (`cloud_polling` IoT class). Data freshness
  depends on the configured polling intervals.
- A unique device fingerprint is generated per config entry to identify the
  integration to the BYD API.
