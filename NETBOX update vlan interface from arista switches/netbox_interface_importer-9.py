#!/usr/bin/env python3
"""
NetBox Interface Importer
GUI tool to import interfaces from a CSV file into NetBox.
CSV columns: device, name, type, description, ipv4_address, vrf
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import csv
import threading
import requests
import json
import os


# ─── NetBox API Helper ────────────────────────────────────────────────────────

class NetBoxAPI:
    def __init__(self, url: str, token: str, verify_ssl: bool = True):
        # Normalise base: strip trailing slash and /api suffix so we can add /api ourselves
        base = url.rstrip("/")
        if not base.endswith("/api"):
            base = base + "/api"
        self.base = base
        self.session = requests.Session()
        self.session.verify = verify_ssl
        if not verify_ssl:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.session.headers.update({
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _get(self, path, params=None):
        r = self.session.get(f"{self.base}{path}", params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path, data):
        r = self.session.post(f"{self.base}{path}", json=data, timeout=10)
        r.raise_for_status()
        return r.json()

    def get_device_id(self, name):
        data = self._get("/dcim/devices/", {"name": name})
        results = data.get("results", [])
        if not results:
            raise ValueError(f"Device not found: {name}")
        return results[0]["id"]

    def get_vrf_id(self, name):
        if not name:
            return None
        data = self._get("/ipam/vrfs/", {"name": name})
        results = data.get("results", [])
        if not results:
            raise ValueError(f"VRF not found: {name}")
        return results[0]["id"]

    def get_or_create_interface(self, device_id, iface_name, iface_type, description, vrf_id=None):
        # Check if interface already exists
        data = self._get("/dcim/interfaces/", {"device_id": device_id, "name": iface_name})
        results = data.get("results", [])
        if results:
            iface_id = results[0]["id"]
            # Update description, type and vrf
            r = self.session.patch(
                f"{self.base}/dcim/interfaces/{iface_id}/",
                json={"description": description, "type": iface_type, "vrf": vrf_id},
                timeout=10,
            )
            if not r.ok:
                raise ValueError(f"PATCH interface failed [{r.status_code}]: {r.text}")
            return iface_id, "updated"
        # Create
        payload = {
            "device": device_id,
            "name": iface_name,
            "type": iface_type,
            "description": description,
            "vrf": vrf_id,
        }
        result = self._post("/dcim/interfaces/", payload)
        return result["id"], "created"

    def get_vlan_interfaces(self, device_id):
        """Return all Vlan* interfaces for a device from NetBox."""
        results = []
        params = {"device_id": device_id, "limit": 500}
        while True:
            data = self._get("/dcim/interfaces/", params)
            results.extend(data.get("results", []))
            if not data.get("next"):
                break
            params["offset"] = params.get("offset", 0) + 500
        # Filter only Vlan interfaces
        return [r for r in results if r["name"].lower().startswith("vlan")]

    def delete_interface(self, interface_id):
        r = self.session.delete(f"{self.base}/dcim/interfaces/{interface_id}/", timeout=10)
        if not r.ok:
            raise ValueError(f"DELETE interface failed [{r.status_code}]: {r.text}")

    def assign_ip(self, interface_id, ip_address, vrf_id):
        if not ip_address:
            return None, "skipped"

        # Check if IP already exists (search with and without prefix length)
        data = self._get("/ipam/ip-addresses/", {"address": ip_address})
        results = data.get("results", [])
        if results:
            ip_id = results[0]["id"]
            # Use bare integer for vrf — confirmed working in NetBox 4.x REST API
            patch_payload = {
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": interface_id,
                "vrf": vrf_id,   # bare int or None
            }
            r = self.session.patch(
                f"{self.base}/ipam/ip-addresses/{ip_id}/",
                json=patch_payload,
                timeout=10,
            )
            if not r.ok:
                raise ValueError(f"PATCH ip-address failed [{r.status_code}]: {r.text}")
            resp = r.json()
            saved_vrf = resp.get("vrf")
            return ip_id, f"ip_updated (vrf_saved={saved_vrf})"
        else:
            # Create new IP
            payload = {
                "address": ip_address,
                "status": "active",
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": interface_id,
                "vrf": vrf_id,   # bare int or None
            }
            r = self.session.post(f"{self.base}/ipam/ip-addresses/", json=payload, timeout=10)
            if not r.ok:
                raise ValueError(f"POST ip-address failed [{r.status_code}]: {r.text}")
            return r.json()["id"], "ip_created"


# ─── GUI ──────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NetBox Interface Importer")
        self.resizable(True, True)
        self.minsize(760, 640)
        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # ── Connection frame ──────────────────────────────────────────────────
        conn_frame = ttk.LabelFrame(self, text="NetBox Connection")
        conn_frame.pack(fill="x", **pad)

        ttk.Label(conn_frame, text="URL:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.url_var = tk.StringVar(value=os.environ.get("NETBOX_URL", "http://netbox.example.com"))
        ttk.Entry(conn_frame, textvariable=self.url_var, width=45).grid(row=0, column=1, sticky="ew", padx=6, pady=4)

        ttk.Label(conn_frame, text="API Token:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.token_var = tk.StringVar(value=os.environ.get("NETBOX_TOKEN", ""))
        ttk.Entry(conn_frame, textvariable=self.token_var, show="*", width=45).grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        conn_frame.columnconfigure(1, weight=1)

        self.verify_ssl_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(conn_frame, text="Verify SSL certificate", variable=self.verify_ssl_var).grid(
            row=2, column=0, sticky="w", padx=6, pady=4)

        btn_test = ttk.Button(conn_frame, text="Test Connection", command=self._test_connection)
        btn_test.grid(row=2, column=1, sticky="e", padx=6, pady=4)

        # ── CSV frame ─────────────────────────────────────────────────────────
        csv_frame = ttk.LabelFrame(self, text="CSV File")
        csv_frame.pack(fill="x", **pad)

        self.csv_path_var = tk.StringVar()
        ttk.Entry(csv_frame, textvariable=self.csv_path_var, width=50).grid(row=0, column=0, sticky="ew", padx=6, pady=4)
        ttk.Button(csv_frame, text="Browse…", command=self._browse).grid(row=0, column=1, padx=6, pady=4)
        ttk.Button(csv_frame, text="Preview", command=self._preview).grid(row=0, column=2, padx=6, pady=4)
        csv_frame.columnconfigure(0, weight=1)

        ttk.Label(csv_frame, text="Expected columns: device, name, type, description, ipv4_address, vrf",
                  foreground="gray").grid(row=1, column=0, columnspan=3, sticky="w", padx=6)

        # ── Notebook (tabs) ───────────────────────────────────────────────────
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, **pad)

        # Tab 1 – Import
        import_tab = ttk.Frame(nb)
        nb.add(import_tab, text="  Import  ")

        prev_frame = ttk.LabelFrame(import_tab, text="Preview")
        prev_frame.pack(fill="both", expand=True, padx=6, pady=6)

        cols = ("device", "name", "type", "description", "ipv4_address", "vrf")
        self.tree = ttk.Treeview(prev_frame, columns=cols, show="headings", height=8)
        col_widths = {"device": 100, "name": 120, "type": 100, "description": 160, "ipv4_address": 110, "vrf": 80}
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=col_widths[c], minwidth=60)
        sb = ttk.Scrollbar(prev_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        opt_frame = ttk.Frame(import_tab)
        opt_frame.pack(fill="x", padx=6, pady=6)
        self.dry_run_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="Dry run (no changes made)", variable=self.dry_run_var).pack(side="left")
        self.import_btn = ttk.Button(opt_frame, text="▶  Import", command=self._start_import)
        self.import_btn.pack(side="right")

        # Tab 2 – Stale VLAN check
        stale_tab = ttk.Frame(nb)
        nb.add(stale_tab, text="  Stale VLAN Check  ")
        self._build_stale_tab(stale_tab)

        # ── Log (shared) ──────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)

        self.log = scrolledtext.ScrolledText(log_frame, height=8, state="disabled",
                                              font=("Courier", 9), wrap="word")
        self.log.pack(fill="both", expand=True, padx=4, pady=4)

        self.log.tag_configure("ok",   foreground="#2e7d32")
        self.log.tag_configure("warn", foreground="#e65100")
        self.log.tag_configure("err",  foreground="#c62828")
        self.log.tag_configure("info", foreground="#1565c0")

        # ── Status bar ────────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w").pack(
            fill="x", side="bottom", ipady=2)

    def _build_stale_tab(self, parent):
        """Build the stale VLAN check tab."""
        pad = {"padx": 6, "pady": 4}

        info = ttk.Label(parent,
            text="Fetches all Vlan* interfaces from NetBox for each device in the CSV, "
                 "then highlights those NOT present in the CSV so you can delete them.",
            foreground="gray", justify="left")
        info.pack(anchor="w", **pad)

        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill="x", **pad)
        self.check_btn = ttk.Button(btn_frame, text="🔍  Fetch & Compare", command=self._start_stale_check)
        self.check_btn.pack(side="left")
        ttk.Button(btn_frame, text="Select All", command=self._stale_select_all).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Deselect All", command=self._stale_deselect_all).pack(side="left")
        self.delete_btn = ttk.Button(btn_frame, text="🗑  Delete Selected", command=self._delete_stale,
                                     state="disabled")
        self.delete_btn.pack(side="right")

        # Treeview with checkboxes (simulated via a column)
        cols = ("select", "device", "interface", "description", "vrf")
        self.stale_tree = ttk.Treeview(parent, columns=cols, show="headings", height=12)
        self.stale_tree.heading("select",      text="✓")
        self.stale_tree.heading("device",      text="Device")
        self.stale_tree.heading("interface",   text="Interface")
        self.stale_tree.heading("description", text="Description")
        self.stale_tree.heading("vrf",         text="VRF")
        self.stale_tree.column("select",      width=30,  minwidth=30,  stretch=False)
        self.stale_tree.column("device",      width=160, minwidth=80)
        self.stale_tree.column("interface",   width=120, minwidth=80)
        self.stale_tree.column("description", width=200, minwidth=80)
        self.stale_tree.column("vrf",         width=100, minwidth=60)
        self.stale_tree.tag_configure("checked",   background="#c8e6c9")
        self.stale_tree.tag_configure("unchecked", background="#ffffff")
        self.stale_tree.bind("<ButtonRelease-1>", self._stale_toggle)

        sb2 = ttk.Scrollbar(parent, orient="vertical", command=self.stale_tree.yview)
        self.stale_tree.configure(yscrollcommand=sb2.set)
        self.stale_tree.pack(side="left", fill="both", expand=True, padx=(6,0), pady=4)
        sb2.pack(side="right", fill="y", pady=4)

        # Internal store: iid -> netbox interface id
        self._stale_ids = {}   # iid -> netbox interface id
        self._stale_checked = set()  # iids currently checked

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log(self, msg, tag="info"):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_status(self, msg):
        self.status_var.set(msg)
        self.update_idletasks()

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Select CSV file",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if path:
            self.csv_path_var.set(path)
            self._preview()

    def _load_csv(self):
        path = self.csv_path_var.get()
        if not path:
            messagebox.showwarning("No file", "Please select a CSV file first.")
            return []
        rows = []
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                rows.append({k.strip().lower(): v.strip() for k, v in row.items()})
        return rows

    def _preview(self):
        try:
            rows = self._load_csv()
        except Exception as e:
            messagebox.showerror("CSV Error", str(e))
            return
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert("", "end", values=(
                row.get("device", ""),
                row.get("name", ""),
                row.get("type", ""),
                row.get("description", ""),
                row.get("ipv4_address", ""),
                row.get("vrf", ""),
            ))
        self._log(f"Loaded {len(rows)} row(s) from CSV.", "info")

    def _test_connection(self):
        url = self.url_var.get().strip()
        token = self.token_var.get().strip()
        if not url or not token:
            messagebox.showwarning("Missing info", "Please enter both URL and token.")
            return
        try:
            api = NetBoxAPI(url, token, verify_ssl=self.verify_ssl_var.get())
            data = api._get("/status/")
            version = data.get("netbox-version", "?")
            messagebox.showinfo("Connected", f"Connected to NetBox {version}")
            self._log(f"Connection OK – NetBox {version}", "ok")
        except Exception as e:
            messagebox.showerror("Connection Failed", str(e))
            self._log(f"Connection failed: {e}", "err")

    # ── Stale VLAN check ──────────────────────────────────────────────────────

    def _stale_toggle(self, event):
        """Toggle checkbox on row click."""
        region = self.stale_tree.identify_region(event.x, event.y)
        if region not in ("cell", "tree"):
            return
        iid = self.stale_tree.identify_row(event.y)
        if not iid:
            return
        if iid in self._stale_checked:
            self._stale_checked.discard(iid)
            self.stale_tree.item(iid, values=("☐",) + self.stale_tree.item(iid, "values")[1:], tags=("unchecked",))
        else:
            self._stale_checked.add(iid)
            self.stale_tree.item(iid, values=("☑",) + self.stale_tree.item(iid, "values")[1:], tags=("checked",))
        self.delete_btn.configure(state="normal" if self._stale_checked else "disabled")

    def _stale_select_all(self):
        for iid in self.stale_tree.get_children():
            self._stale_checked.add(iid)
            self.stale_tree.item(iid, values=("☑",) + self.stale_tree.item(iid, "values")[1:], tags=("checked",))
        self.delete_btn.configure(state="normal" if self._stale_checked else "disabled")

    def _stale_deselect_all(self):
        for iid in self.stale_tree.get_children():
            self._stale_checked.discard(iid)
            self.stale_tree.item(iid, values=("☐",) + self.stale_tree.item(iid, "values")[1:], tags=("unchecked",))
        self.delete_btn.configure(state="disabled")

    def _start_stale_check(self):
        url = self.url_var.get().strip()
        token = self.token_var.get().strip()
        if not url or not token:
            messagebox.showwarning("Missing info", "Please enter NetBox URL and token.")
            return
        if not self.csv_path_var.get().strip():
            messagebox.showwarning("No file", "Please select a CSV file first.")
            return
        self.check_btn.configure(state="disabled")
        self.delete_btn.configure(state="disabled")
        self.stale_tree.delete(*self.stale_tree.get_children())
        self._stale_ids.clear()
        self._stale_checked.clear()
        threading.Thread(target=self._do_stale_check, args=(url, token), daemon=True).start()

    def _do_stale_check(self, url, token):
        self._log("─" * 60, "info")
        self._log("Fetching interfaces from NetBox for comparison…", "info")
        try:
            rows = self._load_csv()
        except Exception as e:
            self._log(f"Failed to read CSV: {e}", "err")
            self.check_btn.configure(state="normal")
            return

        # Build set of (device_name, iface_name) from CSV — only Vlan interfaces
        csv_vlans = {
            (r.get("device", "").strip(), r.get("name", "").strip())
            for r in rows
            if r.get("name", "").strip().lower().startswith("vlan")
        }
        # Unique devices in CSV that have at least one Vlan
        csv_devices = {r.get("device", "").strip() for r in rows if r.get("device", "").strip()}

        api = NetBoxAPI(url, token, verify_ssl=self.verify_ssl_var.get())
        stale_count = 0

        for device_name in sorted(csv_devices):
            self._set_status(f"Checking {device_name}…")
            try:
                device_id = api.get_device_id(device_name)
                nb_vlans = api.get_vlan_interfaces(device_id)
                self._log(f"{device_name}: {len(nb_vlans)} Vlan interface(s) in NetBox", "info")
                for iface in nb_vlans:
                    key = (device_name, iface["name"])
                    if key not in csv_vlans:
                        desc = iface.get("description", "")
                        vrf_obj = iface.get("vrf")
                        vrf_name = vrf_obj["name"] if vrf_obj else ""
                        iid = self.stale_tree.insert("", "end",
                            values=("☐", device_name, iface["name"], desc, vrf_name),
                            tags=("unchecked",))
                        self._stale_ids[iid] = iface["id"]
                        stale_count += 1
            except Exception as e:
                self._log(f"Error checking {device_name}: {e}", "err")

        self._log(f"Done – {stale_count} stale Vlan interface(s) found.", "warn" if stale_count else "ok")
        self._set_status(f"Check complete: {stale_count} stale interface(s) found.")
        self.check_btn.configure(state="normal")

    def _delete_stale(self):
        selected = list(self._stale_checked)
        if not selected:
            return
        names = [self.stale_tree.item(iid, "values")[2] for iid in selected]
        msg = f"Delete {len(selected)} interface(s) from NetBox?\n\n" + "\n".join(names[:20])
        if len(names) > 20:
            msg += f"\n… and {len(names)-20} more"
        if not messagebox.askyesno("Confirm Deletion", msg, icon="warning"):
            return
        url = self.url_var.get().strip()
        token = self.token_var.get().strip()
        self.delete_btn.configure(state="disabled")
        threading.Thread(target=self._do_delete_stale, args=(url, token, selected), daemon=True).start()

    def _do_delete_stale(self, url, token, iids):
        api = NetBoxAPI(url, token, verify_ssl=self.verify_ssl_var.get())
        ok = err = 0
        for iid in iids:
            nb_id = self._stale_ids.get(iid)
            name = self.stale_tree.item(iid, "values")[2]
            device = self.stale_tree.item(iid, "values")[1]
            try:
                api.delete_interface(nb_id)
                self.stale_tree.delete(iid)
                self._stale_ids.pop(iid, None)
                self._stale_checked.discard(iid)
                self._log(f"Deleted {device} > {name}", "ok")
                ok += 1
            except Exception as e:
                self._log(f"Failed to delete {device} > {name}: {e}", "err")
                err += 1
        self._log(f"Deletion complete: {ok} deleted, {err} errors.", "info")
        self._set_status(f"Deletion complete: {ok} deleted, {err} errors.")

    # ── Import ────────────────────────────────────────────────────────────────

    def _start_import(self):
        path = self.csv_path_var.get().strip()
        url = self.url_var.get().strip()
        token = self.token_var.get().strip()
        if not path:
            messagebox.showwarning("No file", "Please select a CSV file.")
            return
        if not url or not token:
            messagebox.showwarning("Missing info", "Please enter NetBox URL and token.")
            return
        self.import_btn.configure(state="disabled")
        threading.Thread(target=self._do_import, args=(url, token), daemon=True).start()

    def _do_import(self, url, token):
        dry = self.dry_run_var.get()
        self._log("─" * 60, "info")
        self._log(f"{'DRY RUN – ' if dry else ''}Starting import…", "info")
        try:
            rows = self._load_csv()
        except Exception as e:
            self._log(f"Failed to read CSV: {e}", "err")
            self.import_btn.configure(state="normal")
            return

        api = NetBoxAPI(url, token, verify_ssl=self.verify_ssl_var.get())
        ok = warn = err = 0

        for i, row in enumerate(rows, 1):
            device  = row.get("device", "").strip()
            name    = row.get("name", "").strip()
            itype   = row.get("type", "other").strip() or "other"
            desc    = row.get("description", "").strip()
            ipv4    = row.get("ipv4_address", "").strip()
            vrf     = row.get("vrf", "").strip()

            self._set_status(f"Processing row {i}/{len(rows)}: {device} / {name}")

            if not device or not name:
                self._log(f"[Row {i}] ✗ Missing device or interface name – skipped.", "warn")
                warn += 1
                continue

            if dry:
                self._log(f"[Row {i}] [DRY] Would import: {device} > {name} ({itype}) ip={ipv4} vrf={vrf}", "ok")
                ok += 1
                continue

            try:
                device_id = api.get_device_id(device)
                vrf_id    = api.get_vrf_id(vrf) if vrf else None
                self._log(f"[Row {i}] VRF '{vrf}' → id={vrf_id}", "info")
                iface_id, iface_status = api.get_or_create_interface(device_id, name, itype, desc, vrf_id=vrf_id)
                ip_id, ip_status = api.assign_ip(iface_id, ipv4, vrf_id)

                msg = (f"[Row {i}] ✓ {device} > {name} "
                       f"[iface:{iface_status}]"
                       + (f" ip={ipv4} vrf_id={vrf_id} [{ip_status}]" if ipv4 else ""))
                self._log(msg, "ok")
                ok += 1

            except Exception as e:
                self._log(f"[Row {i}] ✗ {device} > {name}: {e}", "err")
                err += 1

        self._log("─" * 60, "info")
        self._log(f"Done – {ok} OK, {warn} skipped, {err} errors.", "info")
        self._set_status(f"Import complete: {ok} OK, {warn} skipped, {err} errors.")
        self.import_btn.configure(state="normal")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
