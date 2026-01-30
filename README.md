# Hetzner Storage Box Integration for Home Assistant

## Description



## Installation

### Manual Installation

1. Download the `hetzner` custom component from this repository
2. Navigate to your Home Assistant configuration directory
3. Create a `custom_components` folder if it doesn't already exist
4. Place the `hetzner` folder inside the `custom_components` directory
5. Restart Home Assistant

Your directory structure should look like this:
```
home-assistant/
├── configuration.yaml
├── custom_components/
│   └── hetzner/
│       ├── __init__.py
│       ├── sensor.py
│       ├── config_flow.py
│       ├── manifest.json
│       └── icon.png
```

## Configuration

### Setup via Configuration Flow

1. In Home Assistant, navigate to **Settings** > **Devices & Services**
2. Click **Add Integration** and search for "Hetzner" 
3. Enter your API key when prompted
4. Select the Storage Box you would like to configure
5. Complete the configuration flow

### Obtaining an API Key

To use this integration, you need an API key from Hetzner:


## Example Output



![Spotprice forecast](assets/spotprice-forecast.png)

## Sensors and Attributes

The integration provides three main sensors:


## Update Interval

All sensors are automatically updated every hour to fetch the latest energy price forecasts.
