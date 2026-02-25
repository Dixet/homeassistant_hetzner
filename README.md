<img width="100%" align=right alt="Logo_Hetzner svg" src="https://github.com/user-attachments/assets/4e410d98-0222-430e-beb6-923fdf30d7bb" />  
&nbsp;

# Hetzner Storage Box Integration for Home Assistant  


## Description
**Hetzner Storage Box**  

Home Assistant custom component that exposes storage metrics for your [Hetzner Storage Box](https://www.hetzner.com/storage/storage-box/) via the official JSON API.  
Track **total, used, free, data and snapshot sizes** as native `bytes` sensors, plus a status sensor that shows the box state and enriches it with details such as location, type, creation date and server name.  
Perfect for monitoring your off-site backup space and add it to dashboards, automations and long-term statistics.

## Installation

### Installation through HACS
The easiest way to install the Hetzer Storage Box integration is through HACS. This version of is not yet available in HACS by default and needs to be added as a custom repository.

Add "Home Assistant Hetzner" repository - https://github.com/Dixet/homeassistant_hetzner - as a custom HACS integration repository.

Install "Hetzner Storage Box" from HACS (including restart), as you would with any other integration.

Add Hetzner Storage Box integration via the Ui and follow the instructions

### Manual Installation!


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
To obtain one, sign in into the [Hetzner Console](https://console.hetzner.com) and choose a Project, go to Security → API Tokens, and generate a new token. Make sure to copy the token because it won’t be shown to you again. 


## Sensors and Attributes

The integration provides the following sensors:

### sensor.storage_box_&lt;boxname&gt;
- **State**: The current status of the storage box
- **Attributes**:
  - `ID`: The id of your storage box
  - `name`: The name of your storage box
  - `username`: The username of the storage box owner
  - `server`: FQDN of the Storage Box
  - `system`: Host system of the Storage Box.
  - `storage_box_type`: Type of storage box (subscription type)
  - `created`: When the storage box was first created

### sensor.storage_box_&lt;boxname&gt;_total_size
- **State**: The total size in bytes of the Storage box
- **Unit**: bytes

### sensor.storage_box_&lt;boxname&gt;_total_used
- **State**: The total used size in bytes
- **Unit**: bytes

### sensor.storage_box_&lt;boxname&gt;_data_size
- **State**: The size in bytes used for data 
- **Unit**: bytes

### sensor.storage_box_&lt;boxname&gt;_snapshot_size
- **State**: The size in bytes used for snapshots 
- **Unit**: bytes

### sensor.storage_box_&lt;boxname&gt;_free_space
- **State**: The available free space in your Storage box 
- **Unit**: bytes

### sensor.storage_box_&lt;boxname&gt;_access_options
- **State**: Number of access methods that are enabled (integer)
- **Attributes**:
  - `webdav_enabled`: boolean
  - `zfs_enabled`: boolean
  - `samba_enabled`: boolean
  - `ssh_enabled`: boolean
  - `reachable_externally`: boolean

**Example:** with `samba_enabled`, `ssh_enabled`, `reachable_externally` set to `true`, the sensor state will be `3`.

## Update Interval

All sensors are automatically updated every 10 minutes to fetch the latest status of your Storage Box.
