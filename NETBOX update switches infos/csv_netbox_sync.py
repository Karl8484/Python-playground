#!/usr/bin/env python3
"""
CloudVision CSV exports -> NetBox interface sync (GUI, with preview).

Manages only: name, type, duplex, MAC, PoE, VRF, description, IP addresses, port-channel members.
Optionally proposes DELETING interfaces that exist in NetBox but not in the CSVs.
PoE mode is always PSE on copper interfaces (PoE type is taken from the CSV if present).

pip install pynetbox

1. Connect to NetBox and pick the switch.
2. Load the "routed interfaces", "switchport interfaces" and/or "port-channels" CSV
   (tab, comma or semicolon separated; the delimiter is auto-detected).
3. Compare -> preview of everything that is missing or different.
4. Select rows and Apply (Dry run is ticked by default). Deletions only happen if you tick
   "Propose deleting...", select the delete rows yourself and confirm.
"""
import csv
import io
import os
import re
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import pynetbox
import urllib3

FIELDS = ["type", "description", "lag", "duplex", "mac", "poe_mode", "poe_type", "vrf"]
DEFAULTS = {f: None for f in FIELDS} | {"description": ""}
PRIO = {"poe_mode": 1}  # apply order: poe_mode before poe_type (NetBox requires it)
XCVR_RULES = [(r"^2\.5G", "2.5gbase-t"), (r"^5G", "5gbase-t"), (r"^10GBASE-T", "10gbase-t"),
              (r"^10G", "10gbase-x-sfpp"), (r"^25G", "25gbase-x-sfp28"), (r"^40G", "40gbase-x-qsfpp"),
              (r"^50G", "50gbase-x-sfp56"), (r"^100G", "100gbase-x-qsfp28"),
              (r"^1000BASE-T", "1000base-t"), (r"^(1000BASE|1G)", "1000base-x-sfp"),
              (r"^100BASE", "100base-tx")]
EXPAND = [(r"^(?:Et|Eth)(?=\d)", "Ethernet"), (r"^Po(?=\d)", "Port-Channel"), (r"^Ma(?=\d)", "Management"),
          (r"^Vl(?=\d)", "Vlan"), (r"^Lo(?=\d)", "Loopback")]
COPPER = {"10base-t", "100base-tx", "1000base-t", "2.5gbase-t", "5gbase-t", "10gbase-t"}


# ---------------------------------------------------------------- parsing
def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        text = f.read()
    head = text.splitlines()[0]
    delim = max(["\t", ",", ";"], key=head.count)
    out = []
    for r in csv.DictReader(io.StringIO(text), delimiter=delim):
        out.append({k.strip().lower(): (v or "").strip() for k, v in r.items() if k and isinstance(v, (str, type(None)))})
    return out


def base_type(name, xcvr):
    if name.startswith(("Vlan", "Loopback", "Tunnel", "Vxlan")):
        return "virtual"
    if name.lower().startswith("port-channel"):
        return "lag"
    x = xcvr.upper().strip()
    return next((t for rx, t in XCVR_RULES if re.match(rx, x)), None)


def expand_if(n):
    """Et17 -> Ethernet17, Po3 -> Port-Channel3, ... (CloudVision abbreviates member names)."""
    for rx, full in EXPAND:
        if re.match(rx, n, re.I):
            return re.sub(rx, full, n, count=1, flags=re.I)
    return n


def clean_desc(s):
    s = (s or "").strip()
    return s[1:-1].strip() if len(s) >= 2 and s[0] == s[-1] == '"' else s


def first(r, *keys):
    """First non-empty value among several possible CSV column names."""
    return next((r[k] for k in keys if r.get(k)), "")


def parse_poe_type(r):
    """PoE type from the CSV (column names are guessed), or None."""
    t = first(r, "poe type", "poe standard", "poe class").lower()
    m = re.fullmatch(r"(?:class\s*)?(\d)", t)
    if m:
        c = int(m[1])
        return ("type1-ieee802.3af" if c <= 3 else "type2-ieee802.3at" if c == 4 else
                "type3-ieee802.3bt" if c <= 6 else "type4-ieee802.3bt")
    if re.search(r"802\.3bt|\bbt\b|type\s*[34]", t):
        return "type4-ieee802.3bt" if re.search(r"type\s*4", t) else "type3-ieee802.3bt"
    if re.search(r"802\.3at|\bat\b|type\s*2|poe\+", t):
        return "type2-ieee802.3at"
    if re.search(r"802\.3af|\baf\b|type\s*1", t):
        return "type1-ieee802.3af"
    return None


def build_desired(routed, switchports, portchannels=()):
    """Merge both CSVs into {ifname: desired-state}. Missing/unknown values are left out."""
    d = {}

    def put(x, k, v):
        if v not in (None, ""):
            x[k] = v

    for r in switchports:
        n = r.get("interface")
        if not n:
            continue
        x = d.setdefault(n, {"ips": []})
        put(x, "description", clean_desc(r.get("description")))
        put(x, "duplex", {"full": "full", "half": "half", "auto": "auto"}.get(r.get("duplex", "").lower()))
        put(x, "mac", r.get("configured mac address") or None)
        put(x, "type", base_type(n, r.get("transceiver type", "")))
        put(x, "poe_type", parse_poe_type(r))

    for r in routed:
        n = r.get("interface")
        if not n:
            continue
        x = d.setdefault(n, {"ips": []})
        put(x, "description", clean_desc(r.get("description")))
        put(x, "type", base_type(n, ""))
        vrf = r.get("vrf", "")
        if vrf and vrf.lower() != "default":
            x["vrf"] = vrf
        for col, role in (("ipv4 address", None), ("ipv4 virtual address", "anycast"), ("ipv6 address", None)):
            for ip in re.split(r"[,;\s]+", r.get(col, "")):
                if "/" in ip and not ip.lower().startswith("fe80"):
                    x["ips"].append((ip, role))

    for r in portchannels:
        n = r.get("interface")
        if not n:
            continue
        x = d.setdefault(n, {"ips": []})
        put(x, "description", clean_desc(r.get("description")))
        put(x, "type", base_type(n, ""))
        raw = re.sub(r"\(.*?\)", "", r.get("members", ""))
        x["members"] = {expand_if(m) for m in re.split(r"[,;\s]+", raw) if m}
    return d


def fmt(v):
    return "" if v is None else str(v)


# ---------------------------------------------------------------- GUI
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CSV -> NetBox interface sync")
        self.geometry("1150x760")
        self.nb, self.nb_devs, self.nb_if, self.changes = None, [], {}, {}
        self.routed, self.sw, self.pc, self.dev = [], [], [], None
        self._build()

    def _build(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        self.url = tk.StringVar(value=os.environ.get("NETBOX_URL", "https://netbox.example.com"))
        self.tok = tk.StringVar(value=os.environ.get("NETBOX_TOKEN", ""))
        ttk.Label(top, text="NetBox URL / token").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.url, width=40).grid(row=0, column=1, padx=4)
        ttk.Entry(top, textvariable=self.tok, width=40, show="*").grid(row=0, column=2, padx=4)
        ttk.Button(top, text="Connect", command=self.connect).grid(row=0, column=3)
        self.status = ttk.Label(top, text="-")
        self.status.grid(row=0, column=4, padx=8)
        self.insecure = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Ignore SSL certificate errors", variable=self.insecure).grid(
            row=1, column=1, sticky="w", padx=4)

        mid = ttk.Frame(self, padding=8)
        mid.pack(fill="x")
        ttk.Label(mid, text="Switch (type to filter)").grid(row=0, column=0, sticky="w")
        self.box = ttk.Combobox(mid, width=40)
        self.box.grid(row=0, column=1, padx=4)
        self.box.bind("<KeyRelease>", self.filter_devs)
        self.lbl_r = ttk.Label(mid, text="routed: -")
        self.lbl_s = ttk.Label(mid, text="switchports: -")
        ttk.Button(mid, text="Load routed CSV...", command=lambda: self.load("routed")).grid(row=0, column=2, padx=4)
        self.lbl_r.grid(row=0, column=3)
        ttk.Button(mid, text="Load interfaces CSV...", command=lambda: self.load("sw")).grid(row=0, column=4, padx=4)
        self.lbl_s.grid(row=0, column=5)
        self.lbl_p = ttk.Label(mid, text="port-channels: -")
        ttk.Button(mid, text="Load port-channel CSV...", command=lambda: self.load("pc")).grid(
            row=1, column=2, padx=4, pady=(4, 0))
        self.lbl_p.grid(row=1, column=3, pady=(4, 0))
        ttk.Button(mid, text="Compare", command=self.compare).grid(row=0, column=6, padx=12)

        cols = ("action", "interface", "field", "netbox", "csv")
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, padx=8)
        self.tree = ttk.Treeview(frm, columns=cols, show="headings", selectmode="extended")
        for c, w in zip(cols, (90, 170, 120, 350, 350)):
            self.tree.heading(c, text="CSV (new value)" if c == "csv" else c.capitalize())
            self.tree.column(c, width=w)
        sb = ttk.Scrollbar(frm, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bot = ttk.Frame(self, padding=8)
        bot.pack(fill="x")
        ttk.Button(bot, text="Select all", command=lambda: self.select("keep")).pack(side="left")
        ttk.Button(bot, text="Select deletions", command=lambda: self.select("delete")).pack(side="left", padx=4)
        self.dry, self.mk = tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        self.rm = tk.BooleanVar(value=False)
        ttk.Checkbutton(bot, text="Dry run (log only)", variable=self.dry).pack(side="left", padx=15)
        ttk.Checkbutton(bot, text="Create missing VRFs", variable=self.mk).pack(side="left")
        ttk.Checkbutton(bot, text="Propose deleting interfaces not in CSV", variable=self.rm).pack(side="left", padx=15)
        ttk.Button(bot, text="Apply selected to NetBox", command=self.apply).pack(side="right")
        self.logbox = tk.Text(self, height=9, state="disabled")
        self.logbox.pack(fill="x", padx=8, pady=(0, 8))

    def log(self, m):
        def w():
            self.logbox.config(state="normal")
            self.logbox.insert("end", m + "\n")
            self.logbox.see("end")
            self.logbox.config(state="disabled")
        self.after(0, w)

    def bg(self, fn, done=None):
        def run():
            try:
                r = fn()
                if done:
                    self.after(0, lambda: done(r))
            except Exception as e:  # noqa
                self.log(f"ERROR: {e}")
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
        threading.Thread(target=run, daemon=True).start()

    # ---- connect / select / load
    def connect(self):
        insecure = self.insecure.get()

        def work():
            nb = pynetbox.api(self.url.get().strip(), token=self.tok.get().strip())
            if insecure:
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                nb.http_session.verify = False
            return nb, sorted((d for d in nb.dcim.devices.all() if d.name), key=lambda d: d.name)

        def done(r):
            self.nb, self.nb_devs = r
            self.box["values"] = [d.name for d in self.nb_devs]
            self.status.config(text=f"OK, {len(self.nb_devs)} devices")
        self.bg(work, done)

    def filter_devs(self, _):
        t = self.box.get().lower()
        self.box["values"] = [d.name for d in self.nb_devs if t in d.name.lower()]

    def load(self, which):
        p = filedialog.askopenfilename(filetypes=[("CSV / TSV", "*.csv *.tsv *.txt"), ("All", "*.*")])
        if not p:
            return
        try:
            rows = read_csv(p)
        except Exception as e:  # noqa
            return messagebox.showerror("CSV error", str(e))
        setattr(self, which, rows)
        {"routed": self.lbl_r, "sw": self.lbl_s, "pc": self.lbl_p}[which].config(text=f"{len(rows)} rows")

    # ---- NetBox record -> comparable dict
    @staticmethod
    def nb_view(n):
        def ch(x):
            if x is None or isinstance(x, str):
                return x
            return x.get("value") if isinstance(x, dict) else getattr(x, "value", None)
        pm = getattr(n, "primary_mac_address", None)
        mac = getattr(pm, "mac_address", None) if pm is not None else getattr(n, "mac_address", None)
        vrf = getattr(n, "vrf", None)
        return {"description": n.description or "", "type": ch(n.type),
                "duplex": ch(n.duplex), "mac": str(mac) if mac else None,
                "poe_mode": ch(getattr(n, "poe_mode", None)), "poe_type": ch(getattr(n, "poe_type", None)),
                "vrf": vrf.name if vrf else None,
                "lag": n.lag.name if getattr(n, "lag", None) else None}

    @staticmethod
    def differs(f, cur, want):
        if want is None:
            return False
        if f == "mac":
            return (cur or "").lower() != want.lower()
        return cur != want

    # ---- compare
    def compare(self):
        i = self.box.get()
        dev = next((d for d in self.nb_devs if d.name == i), None)
        if not (self.nb and dev):
            return messagebox.showwarning("Select", "Connect to NetBox and pick a switch.")
        if not (self.routed or self.sw or self.pc):
            return messagebox.showwarning("CSV", "Load at least one CSV.")
        want_all = build_desired(self.routed, self.sw, self.pc)

        def work():
            nbi = {x.name: x for x in self.nb.dcim.interfaces.filter(device_id=dev.id)}
            ips = {}
            for ip in self.nb.ipam.ip_addresses.filter(device_id=dev.id):
                ao = ip.assigned_object
                ips.setdefault(ao.name if ao else None, set()).add(ip.address)
            return nbi, ips

        def done(r):
            nbi, nb_ips = r
            self.nb_if, self.dev = nbi, dev
            self.tree.delete(*self.tree.get_children())
            self.changes = {}
            for pc_name, pw in list(want_all.items()):  # port-channel members -> their "lag" field
                for m in sorted(pw.get("members", ())):
                    if m in want_all or m in nbi:
                        want_all.setdefault(m, {"ips": []})["lag"] = pc_name
                    else:
                        self.log(f"  member {m} of {pc_name}: not in NetBox or CSV, skipped")
            for name in sorted(want_all):
                want, n = want_all[name], self.nb_if.get(name)
                if n is None:
                    t = want.get("type") or "other"
                    self.add(("create", name, "interface", "(missing)", t),
                             {"op": "create", "name": name, "payload": {"name": name, "type": t}})
                    cur = dict(DEFAULTS, type=t)
                else:
                    cur = self.nb_view(n)
                if (want.get("type") or cur["type"]) in COPPER:
                    want["poe_mode"] = "pse"
                else:
                    want.pop("poe_type", None)  # PoE only managed on copper
                if cur["poe_type"] and not cur["poe_mode"] and not want.get("poe_mode"):
                    # NetBox rejects ANY save of an interface that has a PoE type but no PoE mode
                    self.add(("update", name, "poe_type", cur["poe_type"], "(clear: no PoE mode)"),
                             {"op": "set", "name": name, "field": "poe_type", "value": None})
                for f in FIELDS:
                    if self.differs(f, cur[f], want.get(f)):
                        self.add(("update", name, f, fmt(cur[f]), fmt(want[f])),
                                 {"op": "set", "name": name, "field": f, "value": want[f]})
                for ip, role in want["ips"]:
                    if ip not in nb_ips.get(name, set()):
                        self.add(("add IP", name, "ip" + (f" ({role})" if role else ""), "-", ip),
                                 {"op": "ip", "name": name, "ip": ip, "role": role, "vrf": want.get("vrf")})
            n_del = 0
            if self.rm.get():
                for name in sorted(n for n in nbi if n not in want_all):
                    n_del += 1
                    self.add(("delete", name, "interface", self.nb_view(nbi[name])["type"] or "", "(not in CSV)"),
                             {"op": "delete", "name": name})
                n_csv = sum(map(bool, (self.routed, self.sw, self.pc)))
                if n_del and n_csv < 3:
                    self.log(f"  WARNING: only {n_csv} of 3 CSVs loaded - interfaces that live in the other "
                             f"CSV(s) are listed for deletion too")
            self.select("keep")  # deletions are never pre-selected
            self.log(f"{len(want_all)} interfaces in CSV vs {len(self.nb_if)} in NetBox: "
                     f"{len(self.changes) - n_del} changes proposed"
                     + (f", {n_del} deletions proposed (NOT selected)" if n_del else ""))
        self.bg(work, done)

    def select(self, what):
        self.tree.selection_set([i for i, c in self.changes.items() if (c["op"] == "delete") == (what == "delete")])

    def add(self, row, change):
        self.changes[self.tree.insert("", "end", values=row)] = change

    # ---- apply
    def vrf_pk(self, name):
        v = self.nb.ipam.vrfs.get(name=name)
        if not v and self.mk.get():
            v = self.nb.ipam.vrfs.create(name=name)
            self.log(f"  created VRF {name}")
        return v.id if v else None

    def set_field(self, ifc, f, val, ifs=None):
        if f == "lag":
            t = (ifs or {}).get(val)
            return ifc.update({"lag": t.id}) if t else self.log(f"  SKIP lag {val}: not in NetBox")
        if f == "vrf":
            pk = self.vrf_pk(val)
            return ifc.update({"vrf": pk}) if pk else self.log(f"  SKIP vrf {val}: not in NetBox")
        if f == "mac":
            try:
                ver = tuple(int(x) for x in str(self.nb.version).split(".")[:2])
            except Exception:  # noqa
                ver = (4, 2)
            if ver >= (4, 2):
                m = self.nb.dcim.mac_addresses.create(mac_address=val, assigned_object_type="dcim.interface",
                                                      assigned_object_id=ifc.id)
                return ifc.update({"primary_mac_address": m.id})
            return ifc.update({"mac_address": val})
        return ifc.update({f: val})

    def apply(self):
        sel = [self.changes[i] for i in self.tree.selection()]
        if not sel:
            return
        dry = self.dry.get()
        n_del = sum(c["op"] == "delete" for c in sel)
        msg = f"Write {len(sel)} change(s) to NetBox?"
        if n_del:
            msg += (f"\n\n{n_del} interface(s) will be DELETED permanently (NetBox may also delete "
                    f"IP addresses and other objects attached to them).")
        if not dry and not messagebox.askyesno("Confirm", msg, icon="warning" if n_del else "question"):
            return
        order = lambda c: (-1 if c["op"] == "create" else
                           9 if c["op"] == "delete" else
                           5 if c["op"] == "ip" else
                           0 if c.get("field") == "poe_type" and c["value"] is None else
                           PRIO.get(c.get("field"), 3))

        def work():
            ifs, tag = dict(self.nb_if), "[DRY] " if dry else ""
            for ch in sorted(sel, key=order):
                nm = ch["name"]
                try:
                    if ch["op"] == "create":
                        self.log(f"{tag}create interface {nm} ({ch['payload']['type']})")
                        if not dry:
                            ifs[nm] = self.nb.dcim.interfaces.create(device=self.dev.id, **ch["payload"])
                    elif ch["op"] == "delete":
                        self.log(f"{tag}DELETE interface {nm}")
                        if not dry and nm in ifs:
                            ifs.pop(nm).delete()
                    elif ch["op"] == "ip":
                        self.log(f"{tag}{nm}: add IP {ch['ip']}" + (f" role={ch['role']}" if ch["role"] else ""))
                        if dry or nm not in ifs:
                            continue
                        found = list(self.nb.ipam.ip_addresses.filter(address=ch["ip"]))
                        free = [x for x in found if not x.assigned_object_id]
                        kw = {"role": ch["role"]} if ch["role"] else {}
                        if free:
                            free[0].update({"assigned_object_type": "dcim.interface",
                                            "assigned_object_id": ifs[nm].id, **kw})
                        elif found and not ch["role"]:
                            self.log(f"  SKIP: {ch['ip']} already assigned to another interface")
                        else:
                            if ch["vrf"]:
                                kw["vrf"] = self.vrf_pk(ch["vrf"])
                            self.nb.ipam.ip_addresses.create(
                                address=ch["ip"], status="active", assigned_object_type="dcim.interface",
                                assigned_object_id=ifs[nm].id, **kw)
                    else:
                        self.log(f"{tag}{nm}: {ch['field']} = " + ("(clear)" if ch["value"] is None else fmt(ch["value"])))
                        if not dry and nm in ifs:
                            self.set_field(ifs[nm], ch["field"], ch["value"], ifs)
                except Exception as e:  # noqa
                    self.log(f"  FAILED {nm}: {e}")
            self.log("Dry run finished; untick 'Dry run' to write." if dry else "Done.")
        self.bg(work)


if __name__ == "__main__":
    App().mainloop()
