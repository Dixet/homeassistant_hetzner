# Hetzner Storage Box Integration for Home Assistant

## Description
**Hetzner Storage Box**  
Home Assistant custom component that exposes storage metrics for your [Hetzner Storage Box](https://www.hetzner.com/storage/storage-box/) via the official JSON API.  
Track **total, used, free, data and snapshot sizes** as native `bytes` sensors, plus a status sensor that shows the box state and enriches it with details such as location, type, creation date and server name.  
Perfect for monitoring your off-site backup space and add it to dashboards, automations and long-term statistics.

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

To use this integration, you need an API key from Hetzner. 
To obtain one, sign in into the Hetzner Console and choose a Project, go to Security → API Tokens, and generate a new token. Make sure to copy the token because it won’t be shown to you again. 


## Sensors and Attributes

The integration provides three main sensors:

### sensor.storage_box_<boxname>
- **State**: The current status of the storage box
- **Attributes**:
  - `ID`: The id of your storage box
  - `username`: The username of the storage box owner
  - `server`: FQDN of the Storage Box
  - `system`: Host system of the Storage Box.
  - `location`: Code for the physical location of the storage box 
  - `storage_box_type`: Type of storage box (subscription type)
  - `created`: When the storage box was first created

### sensor.storage_box_<boxname>_total_size
- **State**: The total size in bytes of the Storage box
- **Unit**: bytes

### sensor.storage_box_<boxname>_total_used
- **State**: The total used size in bytes
- **Unit**: bytes

### sensor.storage_box_<boxname>_data_size
- **State**: The size in bytes used for data 
- **Unit**: bytes

### sensor.storage_box_<boxname>_snapshot_size
- **State**: The size in bytes used for snapshots 
- **Unit**: bytes

### sensor.storage_box_<boxname>_free_space
- **State**: The available free space in your Storage box 
- **Unit**: bytes


## Update Interval

All sensors are automatically updated every hour to fetch the latest status of your Storage Box.
