# EQ Energy Integration for Home Assistant

## Description

The EQ Energy integration provides Home Assistant with access to forecasted energy prices from Europe. It fetches data from the [Energy Quantified API](https://app.eqenergy.com/) to populate sensors with current energy prices and price statistics for the upcoming forecast period. The integration supports 52 different regions across Europe including countries like Netherlands, Germany, France, Spain, UK, and many more. 

## Installation

### Manual Installation

1. Download the `eqenergy` custom component from this repository
2. Navigate to your Home Assistant configuration directory
3. Create a `custom_components` folder if it doesn't already exist
4. Place the `eqenergy` folder inside the `custom_components` directory
5. Restart Home Assistant

Your directory structure should look like this:
```
home-assistant/
├── configuration.yaml
├── custom_components/
│   └── eqenergy/
│       ├── __init__.py
│       ├── sensor.py
│       ├── config_flow.py
│       ├── manifest.json
│       └── icon.png
```

## Configuration

### Setup via Configuration Flow

1. In Home Assistant, navigate to **Settings** > **Devices & Services**
2. Click **Add Integration** and search for "EQ Energy"
3. Select your desired region from the dropdown list (supports 52 European regions)
4. Enter your API key when prompted
5. Complete the configuration flow

### Obtaining an API Key

To use this integration, you need an API key from Montel EQ Energy Quantified:

1. Visit [Montel EQ](https://app.eqenergy.com/) and sign in to your account
2. If you don't have an account, you create one on their home page
3. Navigate to **Settings** in the web application
4. Find your API key in the settings and copy it
5. Paste the API key when prompted during the Home Assistant configuration flow

## Example Output

The component return a forecast of energy prices for the upcoming days, that can be used in e.g. an Apex chart:

![Spotprice forecast](assets/spotprice-forecast.png)

## Sensors and Attributes

The integration provides three main sensors:

### sensor.energy_forecast
- **State**: The current hourly energy price in EUR/kWh
- **Unit**: EUR/kWh
- **Icon**: 💶 (Currency Euro)
- **Attributes**:
  - `datetime`: The timestamp of the current forecast hour
  - `area`: The geographic area code
  - `region`: The full name of the selected region
  - `raw`: Complete forecast data for the next 7 days containing:
    - `datetime`: Timestamp for each hour
    - `price`: Hourly price in EUR/kWh

### sensor.energy_forecast_highest_price
- **State**: The highest energy price in the forecast period in EUR/kWh
- **Unit**: EUR/kWh
- **Attributes**:
  - `datetime`: The timestamp when the highest price occurs
  - `area`: The geographic area code
  - `region`: The full name of the selected region

### sensor.energy_forecast_lowest_price
- **State**: The lowest energy price in the forecast period in EUR/kWh
- **Unit**: EUR/kWh
- **Attributes**:
  - `datetime`: The timestamp when the lowest price occurs
  - `area`: The geographic area code
  - `region`: The full name of the selected region

## Update Interval

All sensors are automatically updated every hour to fetch the latest energy price forecasts.
