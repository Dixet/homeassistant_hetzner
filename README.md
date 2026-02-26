<img width="100%" align=right alt="Logo_Hetzner svg" src="https://github.com/user-attachments/assets/4e410d98-0222-430e-beb6-923fdf30d7bb" />  
&nbsp;

# Hetzner Storage Box Integration for Home Assistant  
 
<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Description](#description)
- [Installation](#installation)
- [Configuration](#configuration)
- [Sensors and Attributes](#sensors-and-attributes)
- [Update Interval](#update-interval)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Description
**Hetzner Storage Box**  

Home Assistant custom component that exposes storage metrics for your [Hetzner Storage Box](https://www.hetzner.com/storage/storage-box/) via the official JSON API.  
Track **total, used, free, data and snapshot sizes** as native `bytes` sensors, plus a status sensor that shows the box state and enriches it with details such as type, creation date and server name.  Also a `location` sensor is available to show details of where your data is stored.
Perfect for monitoring your off-site backup space and add it to dashboards, automations and long-term statistics.

## Installation

### Installation through HACS
The easiest way to install the Hetzer Storage Box integration is through HACS. 

1. Find the Hetzner Storage Box Integration in the Home Assistant Community Store [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Dixet&repository=homeassistant_hetzner&category=integration)
2. Download the repository
3. Restart Home Assistant

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
[![Start Config Flow](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=hetzner)
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

### sensor.storage_box_&lt;boxname&gt;
- **State**: The current status of the storage box
- **Attributes**:
  - `ID`: unique location identifier
  - `Country`: Country where the data is stored
  - `City`: City where the data is stored
  - `Latitude`: Latitude of the location
  - `Longitude`: Longitude  of the location
  - `Description`: Full name of the location

### sensor.storage_box_&lt;boxname&gt;_location
- **State**: Location code for your storage box location
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
