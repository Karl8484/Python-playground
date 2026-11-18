# Port label updater

A small window where you give a range of port IDs and a CSV of labels, and it sets the labels on those front or rear ports in NetBox.

## Setup

```bash
pip install requests pandas
```

Open `update label.py` and fill in your NetBox URL (including `/api`) and API token at the top.

## Use

```bash
python "update label.py"
```

Pick front or rear, enter the first and last port ID, load your CSV and hit Preview to check it. If it looks right, click Send.

The CSV is just one label per line with no header (see `exemple label.csv`). Labels are applied in order, starting from the first port ID.

## Worth knowing

- Ports are matched by ID, not by name, so double-check the range belongs to the right device. The device name field isn't actually used.
- If you have fewer labels than ports, the extra ports get an empty label.
- There's no undo.
