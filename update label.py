import tkinter as tk
from tkinter import filedialog, messagebox
import requests
import pandas as pd
import urllib3

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ===== CONFIGURATION =====
NETBOX_URL = "yoururl"
NETBOX_TOKEN = "yourtoken"

HEADERS = {
    "Authorization": f"Token {NETBOX_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# ===== FUNCTIONS =====

def get_device_id(name):
    url = f"{NETBOX_URL}/dcim/devices/?name={name}"
    r = requests.get(url, headers=HEADERS, verify=False)
    r.raise_for_status()
    results = r.json().get("results")
    return results[0]["id"] if results else None

def get_ports(device_id, port_type):
    url = f"{NETBOX_URL}/dcim/{port_type}-ports/?device_id={device_id}"
    r = requests.get(url, headers=HEADERS, verify=False)
    r.raise_for_status()
    return sorted(r.json().get("results"), key=lambda x: x['id'])

def update_label(port_type, port_id, label):
    url = f"{NETBOX_URL}/dcim/{port_type}-ports/{port_id}/"
    r = requests.patch(url, headers=HEADERS, json={"label": label}, verify=False)
    r.raise_for_status()
    return r.json()

def load_csv_labels(path):
    df = pd.read_csv(path, header=None)
    return df[0].tolist()

def preview_changes():
    device_name = entry_device.get()
    port_type = var_port_type.get()
    first_id = int(entry_first_id.get())
    last_id = int(entry_last_id.get())

    if not csv_labels:
        messagebox.showerror("Error", "CSV not loaded")
        return

    preview_text.delete(1.0, tk.END)
    total_ports = last_id - first_id + 1
    if total_ports > len(csv_labels):
        messagebox.showwarning("Warning", "CSV has fewer labels than selected ports.")

    for i in range(total_ports):
        label = csv_labels[i] if i < len(csv_labels) else "(none)"
        preview_text.insert(tk.END, f"Port ID {first_id + i} → Label: {label}\n")

def send_updates():
    device_name = entry_device.get()
    port_type = var_port_type.get()
    first_id = int(entry_first_id.get())
    last_id = int(entry_last_id.get())

    if not csv_labels:
        messagebox.showerror("Error", "CSV not loaded")
        return

    try:
        for i, port_id in enumerate(range(first_id, last_id + 1)):
            label = csv_labels[i] if i < len(csv_labels) else ""
            update_label(port_type, port_id, label)
        messagebox.showinfo("Success", "Labels updated successfully.")
    except Exception as e:
        messagebox.showerror("Error", str(e))

def open_csv():
    file_path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")])
    if file_path:
        global csv_labels
        csv_labels = load_csv_labels(file_path)
        label_file_path.config(text=file_path.split("/")[-1])

# ===== GUI =====
root = tk.Tk()
root.title("NetBox Port Label Updater")

csv_labels = []

# Row 0
tk.Label(root, text="Device Name:").grid(row=0, column=0, sticky="e")
entry_device = tk.Entry(root, width=30)
entry_device.grid(row=0, column=1, columnspan=2)

# Row 1
tk.Label(root, text="Port Type:").grid(row=1, column=0, sticky="e")
var_port_type = tk.StringVar(value="front")
tk.Radiobutton(root, text="Front", variable=var_port_type, value="front").grid(row=1, column=1, sticky="w")
tk.Radiobutton(root, text="Rear", variable=var_port_type, value="rear").grid(row=1, column=2, sticky="w")

# Row 2
tk.Label(root, text="First Port ID:").grid(row=2, column=0, sticky="e")
entry_first_id = tk.Entry(root, width=10)
entry_first_id.grid(row=2, column=1, sticky="w")

tk.Label(root, text="Last Port ID:").grid(row=2, column=2, sticky="e")
entry_last_id = tk.Entry(root, width=10)
entry_last_id.grid(row=2, column=3, sticky="w")

# Row 3
btn_load_csv = tk.Button(root, text="Load CSV", command=open_csv)
btn_load_csv.grid(row=3, column=0)
label_file_path = tk.Label(root, text="No file selected")
label_file_path.grid(row=3, column=1, columnspan=3, sticky="w")

# Row 4
btn_preview = tk.Button(root, text="Preview", command=preview_changes)
btn_preview.grid(row=4, column=0, pady=10)
btn_send = tk.Button(root, text="Send", command=send_updates)
btn_send.grid(row=4, column=1, pady=10)

# Row 5
preview_text = tk.Text(root, height=10, width=60)
preview_text.grid(row=5, column=0, columnspan=4, padx=10, pady=10)

root.mainloop()
