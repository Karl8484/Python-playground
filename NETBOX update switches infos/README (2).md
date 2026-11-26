# CSV → NetBox interface sync

Small GUI tool that compares CloudVision CSV exports with a switch's interfaces in NetBox, shows a preview of the differences, and applies the ones you select.

## Setup

```
pip install pynetbox
python csv_netbox_sync.py
```

Optional environment variables pre-fill the login fields: `NETBOX_URL`, `NETBOX_TOKEN`.

## What it manages

| Item | Source CSV | Notes |
|---|---|---|
| Name | all | Interfaces missing in NetBox are created |
| Type | switchports, routed, port-channels | Derived from transceiver type; `lag` for port-channels, `virtual` for Vlan/Loopback/Tunnel/Vxlan |
| Description | all | Surrounding quotes are stripped |
| Duplex | switchports | full / half / auto |
| MAC | switchports | `Configured MAC Address` column |
| PoE | switchports | Mode is always `pse` on copper interfaces; PoE type is read from a `poe type` / `poe standard` / `poe class` column if present, and only set on copper |
| VRF | routed | `default` is ignored |
| IP addresses | routed | IPv4, IPv4 virtual (role `anycast`), IPv6; link-local `fe80::` is skipped |
| Port-channel members | port-channels | Sets the `lag` field on each member; `Et17` is expanded to `Ethernet17`; `Peer…` (MLAG peer) members are ignored |

## Usage

1. Tick or untick **Ignore SSL certificate errors** (ticked by default), then **Connect**.
2. Pick the switch.
3. Load the routed, switchport and/or port-channel CSV (delimiter is auto-detected). Only a row count is shown, not the file name.
4. **Compare** to get the preview.
5. Select rows and **Apply selected to NetBox**.

**Dry run** is ticked by default and only writes to the log. Untick it to write to NetBox.

## Deleting interfaces

Tick **Propose deleting interfaces not in CSV** before Compare to list interfaces that exist in NetBox but in none of the loaded CSVs.

- Deletions are never pre-selected. Use **Select deletions** or click the rows yourself.
- Load all three CSVs first. Otherwise interfaces that belong to a missing CSV are listed for deletion too.
- NetBox may also delete IP addresses and other objects attached to a deleted interface.
- Applying deletions asks for an extra confirmation.

Without this option, nothing is ever deleted or cleared.

## Notes

- Existing values are only overwritten when the CSV has a value; empty CSV fields are left alone.
- NetBox rejects any change to an interface that has a PoE type but no PoE mode. Compare proposes a `poe_type` clear row for those, which runs before other changes.
- IPs, VRFs and members are applied after the interfaces they depend on exist.
