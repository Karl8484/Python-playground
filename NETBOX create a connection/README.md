# Create cables in bulk

just a CSV template for NetBox's built-in cable import. The example connects the rear ports of two fibre patch panels, one port at a time.

## Use

1. Edit `create connection.csv` with your own devices and ports. They must already exist in NetBox.
2. In NetBox, go to Connections → Cables → Import.
3. Paste the content or upload the file, set the delimiter to semicolon, and submit.

NetBox checks every line before saving, so you'll see errors up front (for example if a port is already connected).