# Sync device description to IP addresses

A NetBox custom script that copies the description of a device (or VM) into a custom field on every IP address assigned to it. Handy when you want to see what an IP belongs to straight from the IP list.

## Setup

1. Create a text custom field called `device_description` on the **IP address** object type.
2. Upload `netbox ip device descr.py` under Customization → Scripts.

## Use

Run **Sync device description to IP addresses** from the Scripts page. It only updates IPs where the value has changed, and logs each one. Untick "Commit changes" if you just want to see what it would do.

Re-run it whenever device descriptions change.
