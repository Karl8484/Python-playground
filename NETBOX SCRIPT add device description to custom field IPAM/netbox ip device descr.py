from extras.scripts import Script
from ipam.models import IPAddress


class SyncIPDeviceDescription(Script):
    class Meta:
        name = "Sync device description to IP addresses"
        commit_default = True

    def run(self, data, commit):
        qs = IPAddress.objects.filter(assigned_object_id__isnull=False)
        for ip in qs:
            obj = ip.assigned_object
            parent = getattr(obj, "device", None) or getattr(obj, "virtual_machine", None)
            desc = parent.description if parent else ""
            if ip.custom_field_data.get("device_description") != desc:
                ip.custom_field_data["device_description"] = desc
                ip.save()
                self.log_success(f"{ip}: set to '{desc}'")
