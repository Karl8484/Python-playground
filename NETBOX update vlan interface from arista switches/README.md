# Interface importer (Arista CVP to NetBox)

A small GUI that takes an interface export from Arista CloudVision and pushes it into NetBox: it creates or updates the interfaces, assigns their IPs and sets the VRF. It also has a tab that finds VLAN interfaces still in NetBox but no longer in your CSV, so you can delete them.

## Setup

```bash
pip install requests
python netbox_interface_importer-9.py
```

Enter your NetBox URL and token in the window (or set `NETBOX_URL` and `NETBOX_TOKEN` beforehand), then click Test Connection.

## The CSV

Semicolon-separated, with these columns (see `cvp.csv` for an example):

```
device;name;type;description;ipv4_address;vrf
```

Devices and VRFs need to exist in NetBox already.

## Use

Load the CSV, tick **Dry run** first to check the log, then untick it and click Import.

For cleanup, open the Stale VLAN Check tab, click Fetch & Compare, tick what you want gone and click Delete Selected.
