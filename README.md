<img width="100%" align=right alt="Logo_Hetzner svg" src="https://github.com/user-attachments/assets/4e410d98-0222-430e-beb6-923fdf30d7bb" />  
&nbsp;

# Hetzner Storage Box Integration for Home Assistant  
 
<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
**Table of Contents**

- [Description](#description)
- [Installation](#installation)
  - [Installation through HACS](#installation-through-hacs)
  - [Manual Installation](#manual-installation)
- [Configuration](#configuration)
  - [Prerequisites: Obtaining an API Key](#prerequisites-obtaining-an-api-key)
  - [Setup via Configuration Flow](#setup-via-configuration-flow)
  - [Changing the Unit](#changing-the-unit)
  - [Replacing the API Key](#replacing-the-api-key)
- [Sensors and Attributes](#sensors-and-attributes)
- [Binary Sensors](#binary-sensors)
- [Subaccounts](#subaccounts)
- [Automation Examples](#automation-examples)
  - [Notify when the Storage Box is running full](#notify-when-the-storage-box-is-running-full)
  - [Notify when the Storage Box stays locked](#notify-when-the-storage-box-stays-locked)
  - [Notify when external access is switched on](#notify-when-external-access-is-switched-on)
- [Update Interval](#update-interval)
- [Troubleshooting](#troubleshooting)
  - [Storage Box Not Found](#storage-box-not-found)
  - [API Errors and Maintenance](#api-errors-and-maintenance)
  - [Diagnostics](#diagnostics)
- [Removing the Integration](#removing-the-integration)
- [Development](#development)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Description
**Hetzner Storage Box**  

Home Assistant custom component that exposes storage metrics for your [Hetzner Storage Box](https://www.hetzner.com/storage/storage-box/) via the official [Hetzner API](https://docs.hetzner.cloud/reference/hetzner) (`https://api.hetzner.com/v1`).  
Track **total, used, free, data and snapshot sizes** as native data size sensors (shown in GiB by default, any other unit can be picked per sensor), plus a status sensor that shows the box state and enriches it with details such as type, creation date and server name. Also a `location` sensor is available to show details of where your data is stored, and binary sensors show which access methods (SSH, Samba, WebDAV, external reachability, ZFS snapshot folder) are enabled for the main account. Each [subaccount](#subaccounts) of the box gets its own device with its access settings and home directory.
Perfect for monitoring your off-site backup space and add it to dashboards, automations and long-term statistics.

## Installation

### Installation through HACS
The easiest way to install the Hetzner Storage Box integration is through HACS. 

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
│       ├── brand/
│       ├── __init__.py
│       ├── api.py
│       ├── binary_sensor.py
│       ├── config_flow.py
│       ├── const.py
│       ├── coordinator.py
│       ├── entity.py
│       ├── icons.json
│       ├── manifest.json
│       ├── sensor.py
│       ├── strings.json
│       └── translations/
```

## Configuration

### Prerequisites: Obtaining an API Key

You need Home Assistant 2026.10 or newer, and a Hetzner account with at least one Storage Box and an API key of the project that contains it.
To obtain one, sign in into the [Hetzner Console](https://console.hetzner.com) and choose a Project, go to Security → API Tokens, and generate a new token. Read access is enough. Make sure to copy the token because it won’t be shown to you again.

### Setup via Configuration Flow
[![Start Config Flow](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=hetzner)
1. In Home Assistant, navigate to **Settings** > **Devices & Services**
2. Click **Add Integration** and search for "Hetzner"
3. Enter your API key when prompted
4. Select the Storage Box you would like to configure (boxes that are already set up are listed last and marked "already configured", in English in every language; selecting one shows an error)
5. Complete the configuration flow

To monitor more than one Storage Box, add the integration once per box.

### Changing the Unit

The size sensors report bytes and Home Assistant converts them for display. New sensors are shown in GiB, because Hetzner sizes are binary: a BX11 ("1 TB") has 1 TiB = 1,024 GiB (about 1,099.51 GB).

To show a sensor in another unit (for example `GB`, `TB` or `TiB`), open the sensor, click the gear icon (**Settings**) and pick a **Unit of measurement**. The setting applies to that sensor only; Home Assistant converts history and long-term statistics to the new unit.

Earlier versions had a single unit setting (`B`, `MB`, `GB` or `TB`) in the integration options. When updating, each size sensor gets the unit chosen there as its unit of measurement, unless a different unit was already set on the sensor, so dashboards and long-term statistics continue unchanged; change it per sensor as described above.

Two exceptions affect long-term statistics once after the update:
- Size sensors of version 0.3.x or earlier, which had no unit setting and reported the unit `bytes`, now report `B`. Home Assistant treats this as a changed unit and stops compiling statistics for them until you open **Tools** > **Statistics**, click **Fix issue** on these sensors and confirm the new unit `B` (the values are the same).
- The former `access_options` sensor is removed by the update and replaced by the [binary sensors](#binary-sensors). Its long-term statistics are kept; **Tools** > **Statistics** lists them as having no state, where **Fix issue** deletes them.

### Replacing the API Key

If the Hetzner API rejects the API key, for example because the token was revoked or lacks permissions, Home Assistant shows a **Reauthenticate** notice for the integration. Follow it and enter a new API key of the same project. Temporary errors such as Hetzner maintenance or rate limits do not ask for a new key; see [API Errors and Maintenance](#api-errors-and-maintenance).

To replace the API key at any other time, for example to rotate a token before revoking the old one, open the three-dot menu of the Storage Box under **Settings** > **Devices & Services** > **Hetzner Storage Box** and select **Reconfigure**. The new key must have access to the same Storage Box; entities, history and settings stay as they are. To monitor another box, add the integration again.


## Sensors and Attributes

The integration provides the following sensors. Their names follow the Home Assistant language, the entity IDs stay English in every language (also for the [binary sensors](#binary-sensors)):

#### sensor.storage_box_&lt;boxname&gt;
- **State**: The current status of the storage box: `active`, `initializing` or `locked` (shown translated in the UI). A status the integration does not know yet is shown as unknown.
- **Attributes**:
  - `id`: The id of your storage box
  - `name`: The name of your storage box
  - `username`: The username of the storage box owner
  - `server`: FQDN of the Storage Box
  - `system`: Host system of the Storage Box.
  - `storage_box_type`: Type of storage box (subscription type)
  - `created`: When the storage box was first created

#### sensor.storage_box_&lt;boxname&gt;_location
- **State**: Name of the location where your storage box is hosted (for example `fsn1`)
- **Attributes**:
  - `id`: unique location identifier
  - `country`: Country where the data is stored
  - `city`: City where the data is stored
  - `latitude`: Latitude of the location
  - `longitude`: Longitude of the location
  - `description`: Full name of the location

#### sensor.storage_box_&lt;boxname&gt;_total_size
- **State**: The total size of your storage box plan
- **Unit**: GiB by default, changeable per sensor (see [Changing the Unit](#changing-the-unit))

#### sensor.storage_box_&lt;boxname&gt;_total_used
- **State**: The total used size
- **Unit**: GiB by default, changeable per sensor (see [Changing the Unit](#changing-the-unit))

#### sensor.storage_box_&lt;boxname&gt;_data_size
- **State**: The size used for data
- **Unit**: GiB by default, changeable per sensor (see [Changing the Unit](#changing-the-unit))

#### sensor.storage_box_&lt;boxname&gt;_snapshot_size
- **State**: The size used for snapshots
- **Unit**: GiB by default, changeable per sensor (see [Changing the Unit](#changing-the-unit))

#### sensor.storage_box_&lt;boxname&gt;_free_space
- **State**: The available free space in your Storage Box
- **Unit**: GiB by default, changeable per sensor (see [Changing the Unit](#changing-the-unit))

Free space is the total size minus the total used size, so the two add up to the total size. A size the API does not report shows as unknown rather than 0, so it does not end up in the long-term statistics; free space is unknown when the total size or the total used size is missing.

The attributes of the status, location and home directory sensors are details that rarely change. Home Assistant shows their current values but does not store them in the history.

## Binary Sensors

One binary sensor per access setting of the Storage Box shows whether it is enabled (`on`) or disabled (`off`). These are the settings of the Storage Box's main account, so their names end with "(main account)" (the entity IDs leave this out). The access settings of subaccounts are covered by their own devices, see [Subaccounts](#subaccounts). The binary sensors are diagnostic entities, so they appear under **Diagnostic** on the device page. Except for *Reachable externally*, they are disabled by default; enable them there if you need them (entities created by earlier versions stay enabled). A setting the API does not report shows as unknown.

| Entity ID | Shows whether |
| --- | --- |
| `binary_sensor.storage_box_<boxname>_ssh` | SSH/SFTP access is enabled |
| `binary_sensor.storage_box_<boxname>_samba` | Samba/CIFS access is enabled |
| `binary_sensor.storage_box_<boxname>_webdav` | WebDAV access is enabled |
| `binary_sensor.storage_box_<boxname>_reachable_externally` | The Storage Box is reachable from outside the Hetzner network |
| `binary_sensor.storage_box_<boxname>_zfs_snapshot_folder` | The ZFS snapshot folder is visible |

## Subaccounts

Every subaccount of the Storage Box gets its own device. It is a child device of the Storage Box device, because a subaccount is a part of the box: it has its own login, access settings and home directory on the same box. The device is named after the subaccount (the username if the subaccount has no name), for example "sub1". Home Assistant puts the name of the Storage Box device in front of it in entity names and entity IDs, for example "Storage Box backup sub1 SSH", which keeps them apart from those of the box and of subaccounts of other boxes. If you give a subaccount device an area of its own, Home Assistant uses the area name there instead. A subaccount device without an area of its own is in the area of the Storage Box device, and disabling the Storage Box device disables its subaccount devices as well.

Names changed in the Hetzner Console (of the box or a subaccount) and a changed box type are taken over with the next update; a name you gave a device yourself stays. Entity IDs do not change.

Up to version 1.1, subaccount devices were separate devices "connected via" the Storage Box device and named "Storage Box &lt;boxname&gt; &lt;subaccount name&gt;". The update turns them into child devices: their device, entity IDs, history, areas, labels and names you gave them are kept. The device name becomes the subaccount name, so the `friendly_name` state attribute of their entities becomes for example "sub1 SSH" instead of "Storage Box backup sub1 SSH" unless you named the entity yourself. The devices no longer show a manufacturer, model or link to the Hetzner Console; these are on the Storage Box device.

All subaccount entities are diagnostic entities. The SSH, Samba and WebDAV binary sensors are disabled by default, so that boxes with many subaccounts do not get flooded with entities; enable them on the device page under **Diagnostic** if you need them (entities created by earlier versions stay enabled). For a subaccount `sub1` of the box `backup`:

| Entity ID | Shows |
| --- | --- |
| `binary_sensor.storage_box_backup_sub1_ssh` | SSH/SFTP access is enabled |
| `binary_sensor.storage_box_backup_sub1_samba` | Samba/CIFS access is enabled |
| `binary_sensor.storage_box_backup_sub1_webdav` | WebDAV access is enabled |
| `binary_sensor.storage_box_backup_sub1_reachable_externally` | The subaccount is reachable from outside the Hetzner network |
| `binary_sensor.storage_box_backup_sub1_read_only` | The subaccount has read-only access (`on`) |
| `sensor.storage_box_backup_sub1_home_directory` | The directory the subaccount is limited to (unknown if longer than 255 characters); attributes `username`, `server` (FQDN to connect to) and `description` |

Subaccounts are picked up automatically: one created in the Hetzner Console appears with the next update (within 10 minutes), and the device of a deleted subaccount is removed together with its entities. While the Hetzner API cannot be read, nothing is removed. A leftover subaccount device that the API no longer lists can also be deleted by hand on its device page; the Storage Box device itself cannot be deleted while the integration entry exists.

## Automation Examples

The examples use a box named `backup`; replace `backup` with the name in your entity IDs, and `notify.mobile_app_my_phone` with your own notify action (the companion app registers one per phone as `notify.mobile_app_<device name>`). Paste them in the automation editor under **Edit in YAML**.

### Notify when the Storage Box is running full

Sends a notification once free space has stayed below 200 GiB for an hour. The threshold is in the sensor's unit, GiB unless you changed it (see [Changing the Unit](#changing-the-unit)). The `for` keeps one short dip from sending a message; the sensor is updated every 10 minutes.

```yaml
alias: "Storage Box: running full"
triggers:
  - trigger: numeric_state
    entity_id: sensor.storage_box_backup_free_space
    below: 200
    for: "01:00:00"
actions:
  - action: notify.mobile_app_my_phone
    data:
      title: Hetzner Storage Box is running full
      message: >-
        Only {{ states('sensor.storage_box_backup_free_space') | float(0) | round(1) }}
        {{ state_attr('sensor.storage_box_backup_free_space', 'unit_of_measurement') }}
        left. Check the backup retention or upgrade the Storage Box.
```

The trigger fires when free space goes below the threshold, not on every update while it stays there. After a Home Assistant restart, or after the sensor was unavailable (for example during Hetzner maintenance), Home Assistant treats the next value below the threshold as a new crossing, so the message can come once more. Instead of a notify action you can run a script of your own (`action: script.<name>`) or create a notification in Home Assistant (`action: persistent_notification.create` with the same `title` and `message`).

### Notify when the Storage Box stays locked

Sends a notification when the status sensor has shown `locked` for 30 minutes. The `for` skips locks that end again within that time.

```yaml
alias: "Storage Box: locked"
triggers:
  - trigger: state
    entity_id: sensor.storage_box_backup
    to: locked
    for: "00:30:00"
actions:
  - action: notify.mobile_app_my_phone
    data:
      title: Hetzner Storage Box locked
      message: The Storage Box backup has been locked for 30 minutes. Check the Hetzner Console.
```

### Notify when external access is switched on

Warns when the Storage Box becomes reachable from outside the Hetzner network, for example after a change in the Hetzner Console. `binary_sensor.storage_box_backup_reachable_externally` is enabled by default; the same works for a subaccount, for example with `binary_sensor.storage_box_backup_sub1_reachable_externally`.

```yaml
alias: "Storage Box: reachable externally"
triggers:
  - trigger: state
    entity_id: binary_sensor.storage_box_backup_reachable_externally
    to: "on"
actions:
  - action: notify.mobile_app_my_phone
    data:
      title: Hetzner Storage Box reachable externally
      message: The Storage Box backup can now be reached from outside the Hetzner network.
```

The trigger has no `from: "off"` on purpose: while the Hetzner API cannot be read, the sensor is unavailable, and a switch made in that time arrives as a change from unavailable to `on`, which `from: "off"` would miss. The price is a repeated warning when the sensor comes back from unavailable while external access is still on.

## Update Interval

All sensors are automatically updated every 10 minutes to fetch the latest status of your Storage Box. Each update makes two read-only API requests, `GET /v1/storage_boxes/{id}` and `GET /v1/storage_boxes/{id}/subaccounts` (see the sections *Storage Boxes* and *Storage Box Subaccounts* of the [Hetzner API reference](https://docs.hetzner.cloud/reference/hetzner)), well within the default Hetzner API limit of 3,600 requests per hour and project.

## Troubleshooting

### Storage Box Not Found

If the Hetzner API answers that the Storage Box was not found, the box was deleted, or it is not accessible with the configured API token (for example a token of another project). The Hetzner API does not tell these cases apart, so the integration keeps retrying, marks the sensors as unavailable and shows a repair issue under **Settings** > **System** > **Repairs**. The issue disappears as soon as the box can be read again, or when the integration entry is unloaded, deleted or asks for a new API key.

- If the box was deleted, delete the integration entry.
- If the box belongs to another project than the API token, select **Reconfigure** in the three-dot menu of the entry and enter an API token of that project (see [Replacing the API Key](#replacing-the-api-key)).

### API Errors and Maintenance

Network errors, server errors, rate limits and Hetzner maintenance make the sensors unavailable until the next successful update; Home Assistant keeps retrying on its own. Only a rejected API token (HTTP 401, or 403 for missing permissions) asks for a new API key.

### Diagnostics

When reporting an issue, attach the diagnostics of the integration entry: open the three-dot menu of the Storage Box under **Settings** > **Devices & Services** > **Hetzner Storage Box** and select **Download diagnostics**. The file contains the entry's configuration and the Storage Box and subaccount data of the last update, as returned by the Hetzner API. The API token, usernames, server names (both contain your Hetzner customer number) and location coordinates are replaced by `**REDACTED**`; check the file before posting it publicly, for example for names, descriptions or labels you would rather not share.

## Removing the Integration

1. In Home Assistant, navigate to **Settings** > **Devices & Services** > **Hetzner Storage Box**
2. Open the three-dot menu of each configured Storage Box and select **Delete**
3. To uninstall the files as well, remove the integration in HACS (or delete `custom_components/hetzner` for a manual installation) and restart Home Assistant
4. Optionally, revoke the API token in the [Hetzner Console](https://console.hetzner.com) under your project → Security → API Tokens

## Development

It is recommend to create a virtualenv with Python 3.14.x
After that you can run 

```bash
$ pip install -r requirements.dev.txt
```

If you make any changes, please run the linters, the type checker and the tests before pushing your changes.

```bash
$ ruff check . && ruff format --check . && mypy custom_components tests && pytest
```
