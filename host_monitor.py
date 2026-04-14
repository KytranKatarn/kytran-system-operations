#!/usr/bin/env python3
"""
A.R.C.H.I.E. Host System Monitor
Runs on the HOST machine to collect system data inaccessible from Docker containers.
Writes data to a JSON file that the container can read.

Usage:
    python3 host_monitor.py              # Run once
    python3 host_monitor.py --daemon     # Run continuously (every 30 seconds)
    python3 host_monitor.py --interval 10  # Custom interval in seconds

Output:
    /mnt/archie_brain/host_monitor_data.json
"""

import json
import os
import subprocess
import time
import argparse
import re
import uuid
import glob
from datetime import datetime
from pathlib import Path

OUTPUT_FILE = "/mnt/archie_brain/host_monitor_data.json"
BANDWIDTH_FILE = os.path.join(os.path.dirname(OUTPUT_FILE), "bandwidth_history.json")

# ============================================================================
# COMMAND QUEUE CONSTANTS
# ============================================================================

COMMANDS_DIR = "/mnt/archie_brain/host_commands"
PENDING_DIR = os.path.join(COMMANDS_DIR, "pending")
COMPLETED_DIR = os.path.join(COMMANDS_DIR, "completed")
COMMAND_LOG_FILE = os.path.join(COMMANDS_DIR, "command_log.jsonl")
COMMAND_MAX_AGE_SECONDS = 300  # 5 minutes
COMPLETED_TTL_SECONDS = 3600  # 1 hour


def run_command(cmd, timeout=10):
    """Run a shell command and return output"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=isinstance(cmd, str),
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except Exception as e:
        return None


def get_lvm_info():
    """Get LVM volume group and logical volume information.

    Tries sudo -n (non-interactive) first for full LVM data.
    Falls back to deriving LVM info from lsblk if pvs/vgs/lvs fail
    (e.g. running as non-root user without sudoers entry).
    """
    info = _get_lvm_info_direct()
    if info:
        return info

    # Fallback: derive LVM info from lsblk data
    return _derive_lvm_from_lsblk()


def _get_lvm_info_direct():
    """Try to get LVM info directly via pvs/vgs/lvs (with sudo -n fallback)"""
    info = {"vgs": [], "lvs": [], "pvs": []}

    # Try both direct and sudo -n for each command
    def try_lvm_cmd(cmd_args):
        result = run_command(cmd_args)
        if result:
            return result
        # Try with sudo -n (non-interactive, fails silently if no sudoers entry)
        return run_command(["sudo", "-n"] + cmd_args)

    # Get Physical Volumes
    output = try_lvm_cmd(
        ["pvs", "--noheadings", "--units", "g", "-o", "pv_name,vg_name,pv_size,pv_free"]
    )
    if output:
        for line in output.split("\n"):
            parts = line.split()
            if len(parts) >= 4:
                info["pvs"].append(
                    {
                        "pv_name": parts[0],
                        "vg_name": parts[1],
                        "pv_size": parts[2],
                        "pv_free": parts[3],
                    }
                )

    # Get Volume Groups
    output = try_lvm_cmd(
        ["vgs", "--noheadings", "--units", "g", "-o", "vg_name,vg_size,vg_free"]
    )
    if output:
        for line in output.split("\n"):
            parts = line.split()
            if len(parts) >= 3:
                vg_size = float(parts[1].rstrip("gG")) if parts[1] else 0
                vg_free = float(parts[2].rstrip("gG")) if parts[2] else 0
                info["vgs"].append(
                    {
                        "vg_name": parts[0],
                        "vg_size_gb": round(vg_size, 2),
                        "vg_free_gb": round(vg_free, 2),
                    }
                )

    # Get Logical Volumes
    output = try_lvm_cmd(
        ["lvs", "--noheadings", "--units", "g", "-o", "lv_name,vg_name,lv_size"]
    )
    if output:
        for line in output.split("\n"):
            parts = line.split()
            if len(parts) >= 3:
                lv_size = float(parts[2].rstrip("gG")) if parts[2] else 0
                info["lvs"].append(
                    {
                        "lv_name": parts[0],
                        "vg_name": parts[1],
                        "lv_size_gb": round(lv_size, 2),
                    }
                )

    return info if (info["vgs"] or info["lvs"] or info["pvs"]) else None


def _derive_lvm_from_lsblk():
    """Derive LVM info from lsblk data when pvs/vgs/lvs are unavailable.

    lsblk tells us:
    - A partition with fstype 'LVM2_member' is a PV
    - Children with type 'lvm' are LVs (e.g. name 'ubuntu--vg-ubuntu--lv')
    - Device mapper name format: 'vgname-lvname' (with -- for literal hyphens)
    - VG total size ≈ PV partition size
    - VG free = VG total - sum(LV sizes)
    """
    output = run_command(
        ["lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,PKNAME"]
    )
    if not output:
        return None

    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return None

    info = {"vgs": [], "lvs": [], "pvs": []}
    vg_map = {}  # vg_name -> {'size_bytes': ..., 'lv_total_bytes': ...}

    def scan_devices(devices):
        for device in devices:
            # Look for LVM2_member partitions (these are PVs)
            if device.get("fstype") == "LVM2_member":
                pv_size_bytes = int(device.get("size", 0))
                pv_size_gb = round(pv_size_bytes / (1024**3), 2)
                pv_device = f"/dev/{device['name']}"

                # Parse children to find LVs and determine VG name
                for child in device.get("children", []):
                    if child.get("type") == "lvm":
                        vg_name, lv_name = _parse_dm_name(child["name"])
                        if vg_name:
                            lv_size_bytes = int(child.get("size", 0))
                            lv_size_gb = round(lv_size_bytes / (1024**3), 2)

                            info["lvs"].append(
                                {
                                    "lv_name": lv_name,
                                    "vg_name": vg_name,
                                    "lv_size_gb": lv_size_gb,
                                }
                            )

                            if vg_name not in vg_map:
                                vg_map[vg_name] = {
                                    "size_bytes": pv_size_bytes,
                                    "lv_total_bytes": 0,
                                }
                            vg_map[vg_name]["lv_total_bytes"] += lv_size_bytes

                            # Add PV entry (once per VG)
                            if not any(p["pv_name"] == pv_device for p in info["pvs"]):
                                pv_free_bytes = (
                                    pv_size_bytes - vg_map[vg_name]["lv_total_bytes"]
                                )
                                info["pvs"].append(
                                    {
                                        "pv_name": pv_device,
                                        "vg_name": vg_name,
                                        "pv_size": f"{pv_size_gb}g",
                                        "pv_free": f"{round(max(0, pv_free_bytes) / (1024**3), 2)}g",
                                    }
                                )

            # Recurse into children
            for child in device.get("children", []):
                scan_devices([child])

    scan_devices(data.get("blockdevices", []))

    # Build VG entries from accumulated data
    for vg_name, vg_data in vg_map.items():
        vg_size_gb = round(vg_data["size_bytes"] / (1024**3), 2)
        vg_free_gb = round(
            max(0, vg_data["size_bytes"] - vg_data["lv_total_bytes"]) / (1024**3), 2
        )
        info["vgs"].append(
            {"vg_name": vg_name, "vg_size_gb": vg_size_gb, "vg_free_gb": vg_free_gb}
        )

    # Update PV free values now that we have final LV totals
    for pv in info["pvs"]:
        vg_name = pv["vg_name"]
        if vg_name in vg_map:
            pv_free_bytes = (
                vg_map[vg_name]["size_bytes"] - vg_map[vg_name]["lv_total_bytes"]
            )
            pv["pv_free"] = f"{round(max(0, pv_free_bytes) / (1024**3), 2)}g"

    return info if (info["vgs"] or info["lvs"] or info["pvs"]) else None


def _parse_dm_name(dm_name):
    """Parse device mapper name to extract VG and LV names.

    Format: 'vgname-lvname' where literal hyphens in names are doubled.
    Example: 'ubuntu--vg-ubuntu--lv' -> ('ubuntu-vg', 'ubuntu-lv')

    Strategy: replace '--' with a placeholder, split on single '-',
    then restore hyphens.
    """
    placeholder = "\x00"
    escaped = dm_name.replace("--", placeholder)
    parts = escaped.split("-", 1)
    if len(parts) == 2:
        vg_name = parts[0].replace(placeholder, "-")
        lv_name = parts[1].replace(placeholder, "-")
        return vg_name, lv_name
    return None, None


def get_raid_status():
    """Check for RAID arrays"""
    if not os.path.exists("/proc/mdstat"):
        return None

    try:
        with open("/proc/mdstat", "r") as f:
            content = f.read()

        if "md" not in content:
            return None

        arrays = []
        lines = content.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("md"):
                parts = line.split()
                if len(parts) >= 4:
                    name = parts[0].rstrip(":")
                    status = "active" if "active" in line else "inactive"
                    level = ""
                    devices = []

                    # Find RAID level
                    for p in parts:
                        if p.startswith("raid"):
                            level = p
                            break

                    # Find devices
                    for p in parts[3:]:
                        if "[" in p:
                            devices.append(p)

                    # Check next line for status
                    health = "healthy"
                    if i + 1 < len(lines):
                        next_line = lines[i + 1]
                        if "_" in next_line:
                            health = "degraded"

                    arrays.append(
                        {
                            "name": name,
                            "status": status,
                            "level": level,
                            "devices": devices,
                            "health": health,
                        }
                    )
            i += 1

        return (
            {"arrays": arrays, "healthy": all(a["health"] == "healthy" for a in arrays)}
            if arrays
            else None
        )
    except:
        return None


def get_systemd_services():
    """Get systemd services (important ones)"""
    services = []

    # List of important services to monitor
    important_services = [
        "docker",
        "nginx",
        "postgresql",
        "ssh",
        "sshd",
        "cron",
        "systemd-resolved",
        "networkd-dispatcher",
        "ufw",
        "fail2ban",
        "ollama",
    ]

    output = run_command(
        ["systemctl", "list-units", "--type=service", "--all", "--no-pager", "--plain"]
    )
    if output:
        for line in output.split("\n")[1:]:  # Skip header
            if ".service" not in line:
                continue

            parts = line.split()
            if len(parts) >= 4:
                name = parts[0].replace(".service", "")
                status = parts[2]  # active/inactive
                sub_status = parts[3]

                # Only include important services or active ones
                if name in important_services or status == "active":
                    # Check if enabled
                    enabled_output = run_command(
                        ["systemctl", "is-enabled", f"{name}.service"]
                    )
                    enabled = enabled_output == "enabled" if enabled_output else False

                    services.append(
                        {
                            "name": name,
                            "status": status,
                            "sub_status": sub_status,
                            "enabled": enabled,
                        }
                    )

    return services


def get_network_info():
    """Get host network information including interfaces, listening ports, and connections"""
    info = {
        "hostname": "",
        "interfaces": [],
        "primary_ip": None,
        "listening_ports": [],
        "connections": [],
        "dns_servers": [],
        "gateway": None,
    }

    # Hostname
    info["hostname"] = run_command(["hostname"]) or "unknown"

    # Get interfaces with IPs
    output = run_command(["ip", "-4", "addr", "show"])
    if output:
        current_iface = None
        for line in output.split("\n"):
            if not line.startswith(" "):
                parts = line.split(":")
                if len(parts) >= 2:
                    current_iface = parts[1].strip().split("@")[0]
            elif "inet " in line and current_iface:
                parts = line.strip().split()
                if len(parts) >= 2:
                    ip_with_mask = parts[1]
                    ip = ip_with_mask.split("/")[0]
                    if not ip.startswith("127."):
                        info["interfaces"].append(
                            {"name": current_iface, "ip": ip, "cidr": ip_with_mask}
                        )
                        if not info["primary_ip"]:
                            info["primary_ip"] = ip

    # Default gateway
    gw_output = run_command(["ip", "route", "show", "default"])
    if gw_output:
        parts = gw_output.split()
        if "via" in parts:
            idx = parts.index("via")
            if idx + 1 < len(parts):
                info["gateway"] = parts[idx + 1]

    # DNS servers from /etc/resolv.conf
    try:
        with open("/etc/resolv.conf", "r") as f:
            for line in f:
                if line.startswith("nameserver"):
                    dns = line.split()[1]
                    if dns not in info["dns_servers"]:
                        info["dns_servers"].append(dns)
    except OSError:
        pass

    # Listening ports via ss
    ss_output = run_command(["ss", "-tlnp"])
    if ss_output:
        for line in ss_output.split("\n")[1:]:  # Skip header
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                local_addr = parts[3]
                # Parse address:port (handles both IPv4 and [::]:port)
                if "]:" in local_addr:
                    ip, port = local_addr.rsplit(":", 1)
                    ip = ip.strip("[]")
                elif local_addr.startswith("*:"):
                    ip, port = "0.0.0.0", local_addr[2:]
                else:
                    ip, port = local_addr.rsplit(":", 1)
                port = int(port)

                # Extract process name from "users:(("name",pid=X,...))"
                process = None
                pid = None
                if len(parts) >= 6 and "users:" in parts[5]:
                    proc_str = parts[5]
                    if '(("' in proc_str:
                        process = proc_str.split('(("')[1].split('"')[0]
                    if "pid=" in proc_str:
                        pid = int(proc_str.split("pid=")[1].split(",")[0])

                info["listening_ports"].append(
                    {
                        "port": port,
                        "ip": ip,
                        "protocol": "tcp",
                        "process": process,
                        "pid": pid,
                    }
                )
            except (ValueError, IndexError):
                continue

        info["listening_ports"].sort(key=lambda x: x["port"])

    # Active connections via ss (established, limit to 50)
    # ss -tnp state established output: Recv-Q Send-Q Local Peer [Process]
    # Note: no State column in this mode
    conn_output = run_command(["ss", "-tnp", "state", "established"])
    if conn_output:
        for line in conn_output.split("\n")[1:]:  # Skip header
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                local_addr = parts[2]
                remote_addr = parts[3]

                process = None
                pid = None
                if len(parts) >= 5 and "users:" in parts[4]:
                    proc_str = parts[4]
                    if '(("' in proc_str:
                        process = proc_str.split('(("')[1].split('"')[0]
                    if "pid=" in proc_str:
                        pid = int(proc_str.split("pid=")[1].split(",")[0])

                info["connections"].append(
                    {
                        "local_addr": local_addr,
                        "remote_addr": remote_addr,
                        "status": "ESTABLISHED",
                        "process": process,
                        "pid": pid,
                    }
                )
            except (ValueError, IndexError):
                continue

        info["connections"] = info["connections"][:50]

    # Per-interface bandwidth from /proc/net/dev
    bandwidth_interfaces = []
    try:
        with open("/proc/net/dev", "r") as f:
            for line in f:
                line = line.strip()
                if (
                    ":" not in line
                    or line.startswith("Inter")
                    or line.startswith(" face")
                ):
                    continue
                parts = line.split(":")
                iface_name = parts[0].strip()
                values = parts[1].split()
                if len(values) >= 10:
                    bandwidth_interfaces.append(
                        {
                            "name": iface_name,
                            "rx_bytes": int(values[0]),
                            "rx_packets": int(values[1]),
                            "tx_bytes": int(values[8]),
                            "tx_packets": int(values[9]),
                        }
                    )
    except OSError:
        pass
    info["bandwidth_interfaces"] = bandwidth_interfaces

    return info


def get_disk_layout():
    """Get host disk and partition layout"""
    disks = []

    # Use lsblk to get disk info
    output = run_command(
        ["lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,PKNAME,MODEL"]
    )
    if output:
        try:
            data = json.loads(output)

            for device in data.get("blockdevices", []):
                if device.get("type") == "disk":
                    disk_info = {
                        "name": device.get("name"),
                        "device": f"/dev/{device.get('name')}",
                        "size_bytes": int(device.get("size", 0)),
                        "size_gb": round(int(device.get("size", 0)) / (1024**3), 2),
                        "model": (
                            device.get("model", "Unknown").strip()
                            if device.get("model")
                            else "Unknown"
                        ),
                        "fstype": device.get(
                            "fstype"
                        ),  # Capture fstype for disks without partitions
                        "mountpoint": device.get(
                            "mountpoint"
                        ),  # Capture mountpoint for disks without partitions
                        "partitions": [],
                    }

                    # Get partitions
                    for child in device.get("children", []):
                        # Determine device path based on type
                        if child.get("type") == "lvm":
                            child_device = f"/dev/mapper/{child.get('name')}"
                        else:
                            child_device = f"/dev/{child.get('name')}"

                        part_info = {
                            "name": child.get("name"),
                            "device": child_device,
                            "size_bytes": int(child.get("size", 0)),
                            "size_gb": round(int(child.get("size", 0)) / (1024**3), 2),
                            "type": child.get("type"),
                            "fstype": child.get("fstype") or "Unknown",
                            "mountpoint": child.get("mountpoint"),
                            "children": [],
                        }

                        # Parse VG/LV names for direct LVM children (raw disk as PV)
                        if child.get("type") == "lvm":
                            vg_name, lv_name = _parse_dm_name(child.get("name", ""))
                            if vg_name:
                                part_info["vg_name"] = vg_name
                                part_info["lv_name"] = lv_name
                                part_info["is_lvm"] = True

                        # Check for LVM or other children
                        for grandchild in child.get("children", []):
                            child_entry = {
                                "name": grandchild.get("name"),
                                "device": (
                                    f"/dev/mapper/{grandchild.get('name')}"
                                    if grandchild.get("type") == "lvm"
                                    else f"/dev/{grandchild.get('name')}"
                                ),
                                "size_gb": round(
                                    int(grandchild.get("size", 0)) / (1024**3), 2
                                ),
                                "type": grandchild.get("type"),
                                "fstype": grandchild.get("fstype"),
                                "mountpoint": grandchild.get("mountpoint"),
                            }
                            # Parse VG/LV names from device mapper name for LVM children
                            if grandchild.get("type") == "lvm":
                                vg_name, lv_name = _parse_dm_name(
                                    grandchild.get("name", "")
                                )
                                if vg_name:
                                    child_entry["vg_name"] = vg_name
                                    child_entry["lv_name"] = lv_name
                            part_info["children"].append(child_entry)

                        disk_info["partitions"].append(part_info)

                    disks.append(disk_info)
        except Exception:
            pass

    # Check for unallocated space using fdisk
    for disk in disks:
        disk["unallocated_gb"] = 0
        # If disk is formatted directly without partitions (has fstype and mountpoint),
        # there's no unallocated space - the whole disk is the filesystem
        if disk.get("fstype") and disk.get("mountpoint"):
            disk["unallocated_gb"] = 0
            continue
        fdisk_output = run_command(["fdisk", "-l", disk["device"]])
        if fdisk_output:
            # Calculate total partition sizes
            total_partitioned = sum(p["size_bytes"] for p in disk["partitions"])
            if disk["size_bytes"] > total_partitioned:
                disk["unallocated_gb"] = round(
                    (disk["size_bytes"] - total_partitioned) / (1024**3), 2
                )

    # Add disk usage stats for mounted partitions and disks
    for disk in disks:
        # Check if disk itself is mounted (no partitions, formatted directly)
        if disk.get("mountpoint"):
            _add_usage_stats(disk)
        for part in disk["partitions"]:
            _add_usage_stats(part)
            for child in part.get("children", []):
                _add_usage_stats(child)

    # Enrich with blkid data (UUID, LABEL)
    blkid = get_blkid_info()
    for disk in disks:
        # Check disk itself (for disks without partitions)
        ddev = disk.get("device", "")
        if ddev in blkid:
            disk["uuid"] = blkid[ddev].get("uuid")
            disk["label"] = blkid[ddev].get("label")
        for part in disk["partitions"]:
            dev = part.get("device", "")
            if dev in blkid:
                part["uuid"] = blkid[dev].get("uuid")
                part["label"] = blkid[dev].get("label")
            for child in part.get("children", []):
                cdev = child.get("device", "")
                if cdev in blkid:
                    child["uuid"] = blkid[cdev].get("uuid")
                    child["label"] = blkid[cdev].get("label")

    # Enrich with SMART health
    smart = get_smart_health()
    for disk in disks:
        disk["smart_health"] = smart.get(disk["name"], "unavailable")

    # Enrich with I/O stats
    io_stats = get_disk_io_rates()
    for disk in disks:
        if disk["name"] in io_stats:
            disk["io"] = io_stats[disk["name"]]

    # Enrich with stable identifiers from /dev/disk/by-id/
    stable_ids = get_disk_stable_identifiers()
    for disk in disks:
        if disk["name"] in stable_ids:
            info = stable_ids[disk["name"]]
            disk["serial"] = info.get("serial")
            disk["by_id_path"] = info.get("by_id_path")
            disk["stable_id"] = info.get("stable_id")

            # Enrich partitions with stable identifiers
            for part in disk.get("partitions", []):
                if part["name"] in info.get("partitions", {}):
                    part_info = info["partitions"][part["name"]]
                    part["by_id_path"] = part_info.get("by_id_path")
                    part["stable_id"] = part_info.get("stable_id")

    return disks


def _add_usage_stats(partition):
    """Add usage stats (used/free/percent) to a mounted partition"""
    mp = partition.get("mountpoint")
    if not mp:
        return
    try:
        st = os.statvfs(mp)
        total = st.f_frsize * st.f_blocks
        free = st.f_frsize * st.f_bavail
        used = total - free
        partition["usage"] = {
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "percent": round((used / total) * 100, 1) if total > 0 else 0,
        }
    except OSError:
        pass


def get_disk_stable_identifiers():
    """Get stable identifiers from /dev/disk/by-id/ symlinks.

    Returns dict: {device_name: {serial, model, by_id_path, stable_id, partitions: {...}}}

    These identifiers are stable across reboots and device letter changes because
    they're based on hardware serial numbers.
    """
    identifiers = {}
    partition_entries = []  # Store partition entries for second pass
    by_id_dir = "/dev/disk/by-id"

    if not os.path.exists(by_id_dir):
        return identifiers

    try:
        entries = os.listdir(by_id_dir)
    except OSError:
        return identifiers

    # First pass: collect all disk entries
    for entry in entries:
        # Skip dm-, lvm-, wwn-, md- entries - we want ata- or nvme- or scsi-
        if entry.startswith(("dm-", "lvm-", "wwn-", "md-")):
            continue

        full_path = os.path.join(by_id_dir, entry)

        try:
            # Resolve symlink to get current device name
            target = os.readlink(full_path)
            device_name = os.path.basename(target)
        except OSError:
            continue

        # Check if this is a partition entry (ends with -partN)
        is_partition = "-part" in entry

        if is_partition:
            # Store for second pass
            partition_entries.append((entry, full_path, device_name))
            continue

        # Extract model and serial from entry name
        model = None
        serial = None

        if entry.startswith(("ata-", "nvme-", "scsi-", "usb-")):
            name_part = entry.split("-", 1)[1] if "-" in entry else entry
            parts = name_part.rsplit("_", 1)
            if len(parts) == 2:
                model = parts[0].replace("_", " ")
                serial = parts[1]
            else:
                serial = name_part

        stable_id = (
            f"{model}_{serial}"
            if model and serial
            else entry.split("-", 1)[1] if "-" in entry else entry
        )

        # Only add if not already present (prefer ata- over scsi- entries)
        if device_name not in identifiers:
            identifiers[device_name] = {
                "serial": serial,
                "model": model,
                "by_id_path": full_path,
                "stable_id": stable_id,
                "partitions": {},
            }

    # Second pass: add partition entries to their parent disks
    for entry, full_path, device_name in partition_entries:
        match = re.match(r"(.+)-part(\d+)$", entry)
        if not match:
            continue

        base_entry = match.group(1)
        part_num = int(match.group(2))

        # Extract model and serial from base entry
        model = None
        serial = None
        if base_entry.startswith(("ata-", "nvme-", "scsi-", "usb-")):
            name_part = base_entry.split("-", 1)[1] if "-" in base_entry else base_entry
            parts = name_part.rsplit("_", 1)
            if len(parts) == 2:
                model = parts[0].replace("_", " ")
                serial = parts[1]
            else:
                serial = name_part

        # Find parent device (e.g., sda for sda1)
        parent_name = re.sub(r"\d+$", "", device_name)

        if parent_name in identifiers:
            identifiers[parent_name]["partitions"][device_name] = {
                "by_id_path": full_path,
                "stable_id": (
                    f"{model}_{serial}-part{part_num}" if model and serial else entry
                ),
            }

    return identifiers


def get_blkid_info():
    """Get partition UUID and LABEL from blkid"""
    info = {}
    output = run_command(["blkid"])
    if output:
        for line in output.split("\n"):
            if ":" not in line:
                continue
            device = line.split(":")[0].strip()
            entry = {}
            if 'UUID="' in line:
                entry["uuid"] = line.split('UUID="')[1].split('"')[0]
            if 'LABEL="' in line:
                entry["label"] = line.split('LABEL="')[1].split('"')[0]
            if 'TYPE="' in line:
                entry["type"] = line.split('TYPE="')[1].split('"')[0]
            if 'PARTUUID="' in line:
                entry["partuuid"] = line.split('PARTUUID="')[1].split('"')[0]
            if entry:
                info[device] = entry
    return info


def get_disk_io_rates():
    """Get disk I/O stats from /proc/diskstats"""
    stats = {}
    try:
        with open("/proc/diskstats", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 14:
                    continue
                name = parts[2]
                stats[name] = {
                    "reads_completed": int(parts[3]),
                    "sectors_read": int(parts[5]),
                    "writes_completed": int(parts[7]),
                    "sectors_written": int(parts[9]),
                    "io_in_progress": int(parts[11]),
                }
    except OSError:
        pass
    return stats


def get_smart_health():
    """Get basic SMART health for each disk"""
    health = {}
    output = run_command(["lsblk", "-dn", "-o", "NAME,TYPE"])
    if not output:
        return health
    for line in output.split("\n"):
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "disk":
            name = parts[0]
            # Use run_command_full because smartctl may return non-zero exit codes
            # even for healthy drives (e.g., exit code 32 for marginal attributes)
            result = run_command_full(
                ["sudo", "-n", "smartctl", "-H", f"/dev/{name}"], timeout=10
            )
            smart_out = result.get("stdout", "") + result.get("stderr", "")
            if "PASSED" in smart_out:
                health[name] = "healthy"
            elif "FAILED" in smart_out:
                health[name] = "failing"
            elif smart_out and result.get("returncode", -1) >= 0:
                health[name] = "unknown"
            else:
                health[name] = "unavailable"
    return health


def get_smart_details():
    """Get detailed SMART data for all disks.

    Returns a dict keyed by disk name with detailed SMART attributes:
    - model, serial, firmware
    - temperature_celsius
    - power_on_hours
    - reallocated_sector_count
    - current_pending_sector
    - offline_uncorrectable
    - health_status
    - capacity_alerts (if usage data available)
    """
    details = {}
    output = run_command(["lsblk", "-dn", "-o", "NAME,TYPE"])
    if not output:
        return details

    for line in output.split("\n"):
        parts = line.split()
        if len(parts) < 2 or parts[1] != "disk":
            continue
        name = parts[0]
        disk_details = {
            "name": name,
            "model": None,
            "serial": None,
            "firmware": None,
            "temperature_celsius": None,
            "power_on_hours": None,
            "reallocated_sector_count": None,
            "current_pending_sector": None,
            "offline_uncorrectable": None,
            "health_status": "unavailable",
            "smart_attributes": [],
            "warnings": [],
        }

        # Get SMART info including attributes
        result = run_command_full(
            ["sudo", "-n", "smartctl", "-a", f"/dev/{name}"], timeout=15
        )
        smart_out = result.get("stdout", "")

        if not smart_out:
            details[name] = disk_details
            continue

        # Parse health status
        if "PASSED" in smart_out:
            disk_details["health_status"] = "healthy"
        elif "FAILED" in smart_out:
            disk_details["health_status"] = "failing"
        else:
            disk_details["health_status"] = "unknown"

        # Parse device info section
        for ln in smart_out.split("\n"):
            ln = ln.strip()
            if ln.startswith("Model Family:"):
                disk_details["model_family"] = ln.split(":", 1)[1].strip()
            elif ln.startswith("Device Model:") or ln.startswith("Model Number:"):
                disk_details["model"] = ln.split(":", 1)[1].strip()
            elif ln.startswith("Serial Number:"):
                disk_details["serial"] = ln.split(":", 1)[1].strip()
            elif ln.startswith("Firmware Version:"):
                disk_details["firmware"] = ln.split(":", 1)[1].strip()

        # Parse SMART attributes table
        # Format: ID# ATTRIBUTE_NAME FLAGS VALUE WORST THRESH TYPE UPDATED WHEN_FAILED RAW_VALUE
        in_attributes = False
        for ln in smart_out.split("\n"):
            if "ID#" in ln and "ATTRIBUTE_NAME" in ln:
                in_attributes = True
                continue
            if in_attributes:
                if ln.strip() == "" or not ln.strip()[0].isdigit():
                    in_attributes = False
                    continue
                attr_parts = ln.split()
                if len(attr_parts) >= 10:
                    try:
                        attr_id = int(attr_parts[0])
                        attr_name = attr_parts[1]
                        raw_value_str = attr_parts[9]

                        # Parse raw value (may have format like "32 (Min/Max 28/45)")
                        raw_value = raw_value_str.split()[0] if raw_value_str else "0"
                        try:
                            raw_int = int(raw_value)
                        except ValueError:
                            raw_int = 0

                        # Store specific attributes
                        if attr_id == 194 or attr_name == "Temperature_Celsius":
                            disk_details["temperature_celsius"] = raw_int
                        elif attr_id == 9 or attr_name == "Power_On_Hours":
                            disk_details["power_on_hours"] = raw_int
                        elif attr_id == 5 or attr_name == "Reallocated_Sector_Ct":
                            disk_details["reallocated_sector_count"] = raw_int
                            if raw_int > 0:
                                disk_details["warnings"].append(
                                    f"Reallocated sectors: {raw_int}"
                                )
                        elif attr_id == 197 or attr_name == "Current_Pending_Sector":
                            disk_details["current_pending_sector"] = raw_int
                            if raw_int > 0:
                                disk_details["warnings"].append(
                                    f"Pending sectors: {raw_int}"
                                )
                        elif attr_id == 198 or attr_name == "Offline_Uncorrectable":
                            disk_details["offline_uncorrectable"] = raw_int
                            if raw_int > 0:
                                disk_details["warnings"].append(
                                    f"Offline uncorrectable: {raw_int}"
                                )

                        disk_details["smart_attributes"].append(
                            {"id": attr_id, "name": attr_name, "raw_value": raw_int}
                        )
                    except (ValueError, IndexError):
                        continue

        # For NVMe drives, parse different format
        if disk_details["temperature_celsius"] is None:
            for ln in smart_out.split("\n"):
                if "Temperature:" in ln:
                    try:
                        temp_str = ln.split(":")[1].strip().split()[0]
                        disk_details["temperature_celsius"] = int(temp_str)
                    except (ValueError, IndexError):
                        pass
                elif "Power On Hours:" in ln:
                    try:
                        hours_str = ln.split(":")[1].strip().replace(",", "")
                        disk_details["power_on_hours"] = int(hours_str)
                    except (ValueError, IndexError):
                        pass

        details[name] = disk_details

    return details


def check_capacity_alerts(disks):
    """Check disk capacity and generate alerts.

    Returns a list of alerts with severity levels:
    - 95%+ = critical (red)
    - 90%+ = warning (orange)
    - 80%+ = notice (yellow)
    """
    alerts = []
    for disk in disks:
        # Check disk-level mount
        if disk.get("usage"):
            pct = disk["usage"].get("percent", 0)
            mount = disk.get("mountpoint", disk.get("device", ""))
            if pct >= 95:
                alerts.append(
                    {
                        "mount": mount,
                        "percent": pct,
                        "severity": "critical",
                        "device": disk.get("device"),
                    }
                )
            elif pct >= 90:
                alerts.append(
                    {
                        "mount": mount,
                        "percent": pct,
                        "severity": "warning",
                        "device": disk.get("device"),
                    }
                )
            elif pct >= 80:
                alerts.append(
                    {
                        "mount": mount,
                        "percent": pct,
                        "severity": "notice",
                        "device": disk.get("device"),
                    }
                )

        # Check partitions
        for part in disk.get("partitions", []):
            if part.get("usage"):
                pct = part["usage"].get("percent", 0)
                mount = part.get("mountpoint", part.get("device", ""))
                if pct >= 95:
                    alerts.append(
                        {
                            "mount": mount,
                            "percent": pct,
                            "severity": "critical",
                            "device": part.get("device"),
                        }
                    )
                elif pct >= 90:
                    alerts.append(
                        {
                            "mount": mount,
                            "percent": pct,
                            "severity": "warning",
                            "device": part.get("device"),
                        }
                    )
                elif pct >= 80:
                    alerts.append(
                        {
                            "mount": mount,
                            "percent": pct,
                            "severity": "notice",
                            "device": part.get("device"),
                        }
                    )

            # Check LVM children
            for child in part.get("children", []):
                if child.get("usage"):
                    pct = child["usage"].get("percent", 0)
                    mount = child.get("mountpoint", child.get("device", ""))
                    if pct >= 95:
                        alerts.append(
                            {
                                "mount": mount,
                                "percent": pct,
                                "severity": "critical",
                                "device": child.get("device"),
                            }
                        )
                    elif pct >= 90:
                        alerts.append(
                            {
                                "mount": mount,
                                "percent": pct,
                                "severity": "warning",
                                "device": child.get("device"),
                            }
                        )
                    elif pct >= 80:
                        alerts.append(
                            {
                                "mount": mount,
                                "percent": pct,
                                "severity": "notice",
                                "device": child.get("device"),
                            }
                        )

    # Sort by severity (critical first, then warning, then notice)
    severity_order = {"critical": 0, "warning": 1, "notice": 2}
    alerts.sort(key=lambda x: (severity_order.get(x["severity"], 3), -x["percent"]))

    return alerts


def get_gpu_info():
    """Get GPU information from the host"""
    gpus = []

    # Detect GPUs via lspci
    output = run_command('lspci 2>/dev/null | grep -i -E "VGA|3D|Display"')
    if not output:
        return None

    for line in output.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Parse lspci output: "05:00.0 VGA compatible controller: NVIDIA Corporation GM204GL [Quadro M4000] (rev a1)"
        pci_addr = line.split()[0] if line.split() else ""
        vendor = "unknown"
        model = line

        # Extract vendor and model
        if "NVIDIA" in line.upper():
            vendor = "nvidia"
            # Extract bracketed model name if present: [Quadro M4000]
            if "[" in line and "]" in line:
                model = line[line.index("[") + 1 : line.index("]")]
            else:
                # Fall back to text after the colon
                model = line.split(":", 1)[-1].strip() if ":" in line else line
        elif "AMD" in line.upper() or "ATI" in line.upper():
            vendor = "amd"
            if "[" in line and "]" in line:
                model = line[line.index("[") + 1 : line.index("]")]
            else:
                model = line.split(":", 1)[-1].strip() if ":" in line else line
        elif "INTEL" in line.upper():
            vendor = "intel"
            if "[" in line and "]" in line:
                model = line[line.index("[") + 1 : line.index("]")]
            else:
                model = line.split(":", 1)[-1].strip() if ":" in line else line

        gpu = {
            "pci_address": pci_addr,
            "vendor": vendor,
            "model": model,
            "temperature": None,
            "vram_total_mb": None,
            "vram_used_mb": None,
            "utilization_percent": None,
            "driver": None,
        }

        # Try nvidia-smi for NVIDIA GPUs
        if vendor == "nvidia":
            smi_output = run_command(
                "nvidia-smi --query-gpu=memory.total,memory.used,utilization.gpu,temperature.gpu,driver_version "
                "--format=csv,noheader,nounits 2>/dev/null"
            )
            if smi_output:
                parts = [p.strip() for p in smi_output.split(",")]
                if len(parts) >= 5:
                    gpu["vram_total_mb"] = int(parts[0]) if parts[0].isdigit() else None
                    gpu["vram_used_mb"] = int(parts[1]) if parts[1].isdigit() else None
                    gpu["utilization_percent"] = (
                        int(parts[2]) if parts[2].isdigit() else None
                    )
                    gpu["temperature"] = int(parts[3]) if parts[3].isdigit() else None
                    gpu["driver"] = parts[4] if parts[4] != "[N/A]" else None

        # Fall back to hwmon for temperature (nouveau driver, etc.)
        if gpu["temperature"] is None:
            gpu["temperature"] = _read_hwmon_gpu_temp()

        # Detect driver in use
        if gpu["driver"] is None:
            drv_output = run_command(f"lspci -k -s {pci_addr} 2>/dev/null")
            if drv_output:
                for drv_line in drv_output.split("\n"):
                    if "Kernel driver in use:" in drv_line:
                        gpu["driver"] = drv_line.split(":")[-1].strip()
                        break

        gpus.append(gpu)

    return gpus if gpus else None


def _read_hwmon_gpu_temp():
    """Read GPU temperature from hwmon (nouveau, amdgpu, etc.)"""
    hwmon_base = "/sys/class/hwmon"
    gpu_drivers = {"nouveau", "amdgpu", "radeon", "nvidia"}
    try:
        for entry in os.listdir(hwmon_base):
            name_path = os.path.join(hwmon_base, entry, "name")
            try:
                with open(name_path, "r") as f:
                    name = f.read().strip()
                if name in gpu_drivers:
                    temp_path = os.path.join(hwmon_base, entry, "temp1_input")
                    with open(temp_path, "r") as f:
                        return int(f.read().strip()) // 1000
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return None


def get_memory_hardware():
    """Get physical memory (DIMM) hardware info from dmidecode"""
    result = {
        "dimms": [],
        "total_slots": 0,
        "populated_slots": 0,
        "total_capacity_gb": 0,
        "max_capacity_gb": None,
        "memory_type": None,
        "max_speed_mhz": None,
        "ecc_supported": False,
        "channels": None,
    }

    try:
        # Get memory array info (max capacity, slots)
        array_output = subprocess.check_output(
            ["sudo", "dmidecode", "-t", "16"], stderr=subprocess.DEVNULL, timeout=10
        ).decode("utf-8", errors="replace")

        for line in array_output.split("\n"):
            line = line.strip()
            if line.startswith("Maximum Capacity:"):
                cap_str = line.split(":", 1)[1].strip()
                if "GB" in cap_str:
                    result["max_capacity_gb"] = int(cap_str.replace("GB", "").strip())
                elif "TB" in cap_str:
                    result["max_capacity_gb"] = int(
                        float(cap_str.replace("TB", "").strip()) * 1024
                    )
            elif line.startswith("Number Of Devices:"):
                result["total_slots"] = int(line.split(":", 1)[1].strip())
            elif "Error Correction Type:" in line:
                ecc_type = line.split(":", 1)[1].strip()
                result["ecc_supported"] = ecc_type not in ["None", "Unknown"]

        # Get individual DIMM info
        dimm_output = subprocess.check_output(
            ["sudo", "dmidecode", "-t", "17"], stderr=subprocess.DEVNULL, timeout=10
        ).decode("utf-8", errors="replace")

        # Parse DIMM entries
        current_dimm = None
        for line in dimm_output.split("\n"):
            line_stripped = line.strip()

            if "Memory Device" in line and not line.startswith("\t"):
                if current_dimm:
                    result["dimms"].append(current_dimm)
                current_dimm = {
                    "slot": None,
                    "size_gb": None,
                    "type": None,
                    "speed_mhz": None,
                    "configured_speed_mhz": None,
                    "manufacturer": None,
                    "part_number": None,
                    "serial": None,
                    "form_factor": None,
                    "populated": False,
                }
            elif current_dimm is not None:
                if line_stripped.startswith("Locator:"):
                    current_dimm["slot"] = line_stripped.split(":", 1)[1].strip()
                elif line_stripped.startswith("Size:"):
                    size_str = line_stripped.split(":", 1)[1].strip()
                    if "No Module" in size_str or "Unknown" in size_str:
                        current_dimm["size_gb"] = None
                        current_dimm["populated"] = False
                    else:
                        current_dimm["populated"] = True
                        if "GB" in size_str:
                            current_dimm["size_gb"] = int(
                                size_str.replace("GB", "").strip()
                            )
                        elif "MB" in size_str:
                            current_dimm["size_gb"] = (
                                int(size_str.replace("MB", "").strip()) / 1024
                            )
                elif line_stripped.startswith("Type:") and not line_stripped.startswith(
                    "Type Detail"
                ):
                    mem_type = line_stripped.split(":", 1)[1].strip()
                    if mem_type not in ["Unknown", "Other"]:
                        current_dimm["type"] = mem_type
                        if result["memory_type"] is None:
                            result["memory_type"] = mem_type
                elif line_stripped.startswith("Speed:"):
                    speed_str = line_stripped.split(":", 1)[1].strip()
                    if "MT/s" in speed_str or "MHz" in speed_str:
                        speed_val = (
                            speed_str.replace("MT/s", "").replace("MHz", "").strip()
                        )
                        try:
                            current_dimm["speed_mhz"] = int(speed_val)
                            if (
                                result["max_speed_mhz"] is None
                                or current_dimm["speed_mhz"] > result["max_speed_mhz"]
                            ):
                                result["max_speed_mhz"] = current_dimm["speed_mhz"]
                        except ValueError:
                            pass
                elif line_stripped.startswith("Configured Memory Speed:"):
                    speed_str = line_stripped.split(":", 1)[1].strip()
                    if "MT/s" in speed_str or "MHz" in speed_str:
                        speed_val = (
                            speed_str.replace("MT/s", "").replace("MHz", "").strip()
                        )
                        try:
                            current_dimm["configured_speed_mhz"] = int(speed_val)
                        except ValueError:
                            pass
                elif line_stripped.startswith("Manufacturer:"):
                    mfg = line_stripped.split(":", 1)[1].strip()
                    if mfg not in ["Unknown", "Not Specified", ""]:
                        current_dimm["manufacturer"] = mfg
                elif line_stripped.startswith("Part Number:"):
                    pn = line_stripped.split(":", 1)[1].strip()
                    if pn not in ["Unknown", "Not Specified", ""]:
                        current_dimm["part_number"] = pn
                elif line_stripped.startswith("Serial Number:"):
                    sn = line_stripped.split(":", 1)[1].strip()
                    if sn not in ["Unknown", "Not Specified", ""]:
                        current_dimm["serial"] = sn
                elif line_stripped.startswith("Form Factor:"):
                    ff = line_stripped.split(":", 1)[1].strip()
                    if ff not in ["Unknown", "Not Specified"]:
                        current_dimm["form_factor"] = ff

        # Don't forget the last DIMM
        if current_dimm:
            result["dimms"].append(current_dimm)

        # Calculate totals
        result["populated_slots"] = sum(1 for d in result["dimms"] if d["populated"])
        result["total_capacity_gb"] = sum(d["size_gb"] or 0 for d in result["dimms"])

        # Estimate channels based on populated slots
        if result["populated_slots"] >= 4:
            result["channels"] = (
                "Quad Channel"
                if result["populated_slots"] % 4 == 0
                else "Multi-Channel"
            )
        elif result["populated_slots"] >= 2:
            result["channels"] = (
                "Dual Channel"
                if result["populated_slots"] % 2 == 0
                else "Single Channel"
            )
        elif result["populated_slots"] == 1:
            result["channels"] = "Single Channel"

    except subprocess.TimeoutExpired:
        pass
    except subprocess.CalledProcessError:
        pass
    except FileNotFoundError:
        # dmidecode not installed
        pass
    except Exception as e:
        print(f"Memory hardware error: {e}")

    return result


def get_cpu_hardware():
    """Get extended CPU hardware info from dmidecode for Hardware tab"""
    result = {
        "socket_type": None,
        "max_speed_mhz": None,
        "current_speed_mhz": None,
        "voltage": None,
        "cache_l1": None,
        "cache_l2": None,
        "cache_l3": None,
        "max_processors": 0,
        "populated_sockets": 0,
        "processors": [],
    }

    try:
        # Get processor info from dmidecode
        proc_output = subprocess.check_output(
            ["sudo", "-n", "dmidecode", "-t", "processor"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode("utf-8", errors="replace")

        current_proc = None
        for line in proc_output.split("\n"):
            line_stripped = line.strip()

            if "Processor Information" in line and not line.startswith("\t"):
                if current_proc:
                    result["processors"].append(current_proc)
                current_proc = {
                    "socket": None,
                    "model": None,
                    "family": None,
                    "max_speed_mhz": None,
                    "current_speed_mhz": None,
                    "voltage": None,
                    "cores": None,
                    "threads": None,
                    "status": None,
                }
                result["max_processors"] += 1
            elif current_proc is not None:
                if line_stripped.startswith("Socket Designation:"):
                    current_proc["socket"] = line_stripped.split(":", 1)[1].strip()
                    if result["socket_type"] is None:
                        result["socket_type"] = current_proc["socket"]
                elif line_stripped.startswith("Version:"):
                    ver = line_stripped.split(":", 1)[1].strip()
                    if ver and ver not in ["Unknown", "Not Specified"]:
                        current_proc["model"] = ver
                elif line_stripped.startswith("Family:"):
                    fam = line_stripped.split(":", 1)[1].strip()
                    if fam and fam not in ["Unknown", "Other"]:
                        current_proc["family"] = fam
                elif line_stripped.startswith("Max Speed:"):
                    speed_str = line_stripped.split(":", 1)[1].strip()
                    if "MHz" in speed_str:
                        try:
                            current_proc["max_speed_mhz"] = int(
                                speed_str.replace("MHz", "").strip()
                            )
                            if result["max_speed_mhz"] is None:
                                result["max_speed_mhz"] = current_proc["max_speed_mhz"]
                        except ValueError:
                            pass
                elif line_stripped.startswith("Current Speed:"):
                    speed_str = line_stripped.split(":", 1)[1].strip()
                    if "MHz" in speed_str:
                        try:
                            current_proc["current_speed_mhz"] = int(
                                speed_str.replace("MHz", "").strip()
                            )
                            if result["current_speed_mhz"] is None:
                                result["current_speed_mhz"] = current_proc[
                                    "current_speed_mhz"
                                ]
                        except ValueError:
                            pass
                elif line_stripped.startswith("Voltage:"):
                    voltage = line_stripped.split(":", 1)[1].strip()
                    if voltage and voltage not in ["Unknown"]:
                        current_proc["voltage"] = voltage
                        if result["voltage"] is None:
                            result["voltage"] = voltage
                elif line_stripped.startswith("Core Count:"):
                    try:
                        current_proc["cores"] = int(
                            line_stripped.split(":", 1)[1].strip()
                        )
                    except ValueError:
                        pass
                elif line_stripped.startswith("Thread Count:"):
                    try:
                        current_proc["threads"] = int(
                            line_stripped.split(":", 1)[1].strip()
                        )
                    except ValueError:
                        pass
                elif line_stripped.startswith("Status:"):
                    status = line_stripped.split(":", 1)[1].strip()
                    current_proc["status"] = status
                    if "Populated" in status:
                        result["populated_sockets"] += 1
                elif line_stripped.startswith("Upgrade:"):
                    # This contains the actual socket type (e.g., "Socket LGA2011")
                    upgrade = line_stripped.split(":", 1)[1].strip()
                    if upgrade and upgrade not in ["Unknown", "None", "Other"]:
                        current_proc["socket_type"] = upgrade
                        # Use this as the socket type (overrides Socket Designation)
                        result["socket_type"] = upgrade

        # Don't forget the last processor
        if current_proc:
            result["processors"].append(current_proc)

        # Get cache info from dmidecode
        cache_output = subprocess.check_output(
            ["sudo", "-n", "dmidecode", "-t", "cache"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode("utf-8", errors="replace")

        for line in cache_output.split("\n"):
            line_stripped = line.strip()
            if line_stripped.startswith("Socket Designation:"):
                cache_name = line_stripped.split(":", 1)[1].strip().upper()
            elif line_stripped.startswith("Installed Size:"):
                size_str = line_stripped.split(":", 1)[1].strip()
                if "KB" in size_str or "MB" in size_str:
                    size_val = size_str
                    if "L1" in cache_name:
                        result["cache_l1"] = size_val
                    elif "L2" in cache_name:
                        result["cache_l2"] = size_val
                    elif "L3" in cache_name:
                        result["cache_l3"] = size_val

    except subprocess.TimeoutExpired:
        pass
    except subprocess.CalledProcessError:
        pass
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"CPU hardware error: {e}")

    return result


def get_motherboard_info():
    """Get motherboard and system info from dmidecode"""
    result = {
        "manufacturer": None,
        "product_name": None,
        "system_product": None,
        "system_manufacturer": None,
        "serial_number": None,
        "version": None,
        "chipset": None,
        "bios_vendor": None,
        "bios_version": None,
        "bios_date": None,
    }

    try:
        # Get baseboard info
        baseboard_output = subprocess.check_output(
            ["sudo", "-n", "dmidecode", "-t", "baseboard"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode("utf-8", errors="replace")

        for line in baseboard_output.split("\n"):
            line_stripped = line.strip()
            if line_stripped.startswith("Manufacturer:"):
                mfg = line_stripped.split(":", 1)[1].strip()
                if mfg and mfg not in [
                    "Unknown",
                    "Not Specified",
                    "To Be Filled By O.E.M.",
                ]:
                    result["manufacturer"] = mfg
            elif line_stripped.startswith("Product Name:"):
                pn = line_stripped.split(":", 1)[1].strip()
                if pn and pn not in [
                    "Unknown",
                    "Not Specified",
                    "To Be Filled By O.E.M.",
                ]:
                    result["product_name"] = pn
            elif line_stripped.startswith("Version:"):
                ver = line_stripped.split(":", 1)[1].strip()
                if ver and ver not in [
                    "Unknown",
                    "Not Specified",
                    "To Be Filled By O.E.M.",
                ]:
                    result["version"] = ver
            elif line_stripped.startswith("Serial Number:"):
                sn = line_stripped.split(":", 1)[1].strip()
                if sn and sn not in [
                    "Unknown",
                    "Not Specified",
                    "To Be Filled By O.E.M.",
                ]:
                    result["serial_number"] = sn

        # Get system info (for full product name like "HP Z420 Workstation")
        system_output = subprocess.check_output(
            ["sudo", "-n", "dmidecode", "-t", "system"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode("utf-8", errors="replace")

        for line in system_output.split("\n"):
            line_stripped = line.strip()
            if line_stripped.startswith("Product Name:"):
                pn = line_stripped.split(":", 1)[1].strip()
                if pn and pn not in [
                    "Unknown",
                    "Not Specified",
                    "To Be Filled By O.E.M.",
                ]:
                    result["system_product"] = pn
            elif line_stripped.startswith("Manufacturer:"):
                mfg = line_stripped.split(":", 1)[1].strip()
                if mfg and mfg not in [
                    "Unknown",
                    "Not Specified",
                    "To Be Filled By O.E.M.",
                ]:
                    result["system_manufacturer"] = mfg

        # Get BIOS info
        bios_output = subprocess.check_output(
            ["sudo", "-n", "dmidecode", "-t", "bios"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode("utf-8", errors="replace")

        for line in bios_output.split("\n"):
            line_stripped = line.strip()
            if line_stripped.startswith("Vendor:"):
                vendor = line_stripped.split(":", 1)[1].strip()
                if vendor and vendor not in ["Unknown", "Not Specified"]:
                    result["bios_vendor"] = vendor
            elif line_stripped.startswith("Version:"):
                ver = line_stripped.split(":", 1)[1].strip()
                if ver and ver not in ["Unknown", "Not Specified"]:
                    result["bios_version"] = ver
            elif line_stripped.startswith("Release Date:"):
                date = line_stripped.split(":", 1)[1].strip()
                if date and date not in ["Unknown", "Not Specified"]:
                    result["bios_date"] = date

        # Get chipset from lspci (Host bridge)
        lspci_output = run_command('lspci 2>/dev/null | grep -i "Host bridge"')
        if lspci_output:
            # Parse: "00:00.0 Host bridge: Intel Corporation Xeon E5/Core i7..."
            if ":" in lspci_output:
                chipset_part = lspci_output.split("Host bridge:", 1)[-1].strip()
                # Extract chipset name (e.g., "Intel X79" from full description)
                if "Intel" in chipset_part:
                    result["chipset"] = (
                        "Intel " + chipset_part.split("(")[0].strip().split()[-1]
                        if "(" in chipset_part
                        else chipset_part[:50]
                    )
                elif "AMD" in chipset_part:
                    result["chipset"] = chipset_part[:50]
                else:
                    result["chipset"] = chipset_part[:50]

    except subprocess.TimeoutExpired:
        pass
    except subprocess.CalledProcessError:
        pass
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Motherboard info error: {e}")

    return result


def get_pci_slots():
    """Get PCI/PCIe expansion slot info from dmidecode with device mapping from lspci"""
    slots = []

    try:
        # First, build a map of PCI addresses to device names from lspci
        pci_devices = {}
        lspci_output = run_command("lspci 2>/dev/null")
        if lspci_output:
            for line in lspci_output.split("\n"):
                if not line.strip():
                    continue
                parts = line.split(" ", 1)
                if len(parts) >= 2:
                    addr = parts[0].strip()
                    # Extract device name (text after the colon)
                    if ":" in parts[1]:
                        device_desc = parts[1].split(":", 1)[-1].strip()
                        # Shorten long device names
                        if "[" in device_desc and "]" in device_desc:
                            # Extract bracketed model name: [Quadro M4000]
                            device_desc = device_desc[
                                device_desc.index("[") + 1 : device_desc.index("]")
                            ]
                        elif len(device_desc) > 40:
                            device_desc = device_desc[:40] + "..."
                        pci_devices[addr] = device_desc

        # Get slot info from dmidecode
        slot_output = subprocess.check_output(
            ["sudo", "-n", "dmidecode", "-t", "slot"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode("utf-8", errors="replace")

        current_slot = None
        for line in slot_output.split("\n"):
            line_stripped = line.strip()

            if "System Slot Information" in line and not line.startswith("\t"):
                if current_slot:
                    slots.append(current_slot)
                current_slot = {
                    "designation": None,
                    "type": None,
                    "current_usage": None,
                    "length": None,
                    "bus_address": None,
                    "device": None,
                }
            elif current_slot is not None:
                if line_stripped.startswith("Designation:"):
                    current_slot["designation"] = line_stripped.split(":", 1)[1].strip()
                elif line_stripped.startswith("Type:"):
                    slot_type = line_stripped.split(":", 1)[1].strip()
                    # Simplify slot type description
                    if "PCI Express" in slot_type:
                        # Extract "x16 PCI Express 3" style
                        current_slot["type"] = slot_type
                    else:
                        current_slot["type"] = slot_type
                elif line_stripped.startswith("Current Usage:"):
                    current_slot["current_usage"] = line_stripped.split(":", 1)[
                        1
                    ].strip()
                elif line_stripped.startswith("Length:"):
                    current_slot["length"] = line_stripped.split(":", 1)[1].strip()
                elif line_stripped.startswith("Bus Address:"):
                    bus_addr = line_stripped.split(":", 1)[1].strip()
                    current_slot["bus_address"] = bus_addr
                    # Try to match with lspci device (strip leading 0000: if present)
                    short_addr = (
                        bus_addr.replace("0000:", "")
                        if bus_addr.startswith("0000:")
                        else bus_addr
                    )
                    if short_addr in pci_devices:
                        current_slot["device"] = pci_devices[short_addr]

        # Don't forget the last slot
        if current_slot:
            slots.append(current_slot)

    except subprocess.TimeoutExpired:
        pass
    except subprocess.CalledProcessError:
        pass
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"PCI slots error: {e}")

    return slots


def get_sata_ports():
    """Get SATA port information from /sys/class/ata_port"""
    result = {"total_ports": 0, "used_ports": 0, "available_ports": 0, "ports": []}

    try:
        ata_path = "/sys/class/ata_port"
        if not os.path.exists(ata_path):
            return result

        # Get list of ata ports
        ports = sorted([p for p in os.listdir(ata_path) if p.startswith("ata")])
        result["total_ports"] = len(ports)

        for port_name in ports:
            port_path = os.path.join(ata_path, port_name)
            port_info = {
                "name": port_name,
                "port_number": int(port_name.replace("ata", "")),
                "device": None,
                "device_model": None,
                "in_use": False,
            }

            # Try to find connected device
            try:
                # Look for block device in the port's host
                real_path = os.path.realpath(port_path)
                parent_dir = os.path.dirname(os.path.dirname(real_path))

                # Look for scsi_host subdirs
                for entry in os.listdir(parent_dir):
                    if entry.startswith("host"):
                        host_path = os.path.join(parent_dir, entry)
                        # Look for target subdirs
                        for subentry in os.listdir(host_path):
                            if subentry.startswith("target"):
                                target_path = os.path.join(host_path, subentry)
                                for dev_entry in os.listdir(target_path):
                                    if ":" in dev_entry:
                                        block_path = os.path.join(
                                            target_path, dev_entry, "block"
                                        )
                                        if os.path.exists(block_path):
                                            devices = os.listdir(block_path)
                                            if devices:
                                                port_info["device"] = devices[0]
                                                port_info["in_use"] = True
                                                # Try to get model
                                                model_path = os.path.join(
                                                    target_path, dev_entry, "model"
                                                )
                                                if os.path.exists(model_path):
                                                    with open(model_path, "r") as f:
                                                        port_info["device_model"] = (
                                                            f.read().strip()
                                                        )
            except (OSError, IOError):
                pass

            result["ports"].append(port_info)

        result["used_ports"] = sum(1 for p in result["ports"] if p["in_use"])
        result["available_ports"] = result["total_ports"] - result["used_ports"]

    except Exception as e:
        print(f"SATA ports error: {e}")

    return result


def get_cpu_info():
    """Get CPU information from the host"""
    info = {
        "model": None,
        "physical_cores": None,
        "logical_cores": None,
        "temperature": None,
        "temperatures": [],
    }

    # CPU model from /proc/cpuinfo
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("model name"):
                    info["model"] = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass

    # Core counts
    try:
        info["logical_cores"] = os.cpu_count()
    except Exception:
        pass

    # Physical cores (count unique core IDs)
    try:
        with open("/proc/cpuinfo", "r") as f:
            core_ids = set()
            for line in f:
                if line.startswith("core id"):
                    core_ids.add(line.split(":", 1)[1].strip())
            if core_ids:
                info["physical_cores"] = len(core_ids)
    except OSError:
        pass

    # CPU temperatures from coretemp hwmon
    hwmon_base = "/sys/class/hwmon"
    try:
        for entry in os.listdir(hwmon_base):
            name_path = os.path.join(hwmon_base, entry, "name")
            try:
                with open(name_path, "r") as f:
                    name = f.read().strip()
                if name in ("coretemp", "k10temp", "zenpower"):
                    hwmon_dir = os.path.join(hwmon_base, entry)
                    temps = []
                    for fname in sorted(os.listdir(hwmon_dir)):
                        if fname.startswith("temp") and fname.endswith("_input"):
                            try:
                                with open(os.path.join(hwmon_dir, fname), "r") as f:
                                    temp_c = int(f.read().strip()) // 1000
                                    temps.append(temp_c)
                            except (OSError, ValueError):
                                continue
                    if temps:
                        info["temperatures"] = temps
                        info["temperature"] = max(temps)
                    break
            except (OSError, ValueError):
                continue
    except OSError:
        pass

    return info


def get_system_info():
    """Get host system information"""
    info = {
        "hostname": None,
        "distribution": None,
        "version_id": None,
        "kernel": None,
        "uptime_seconds": None,
        "uptime_formatted": None,
        "boot_time": None,
        "load_average": None,
        "process_count": None,
        "users_logged_in": None,
        "architecture": None,
    }

    # Hostname
    info["hostname"] = run_command(["hostname"]) or os.uname().nodename

    # Distribution from /etc/os-release
    try:
        with open("/etc/os-release", "r") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    info["distribution"] = line.split("=", 1)[1].strip().strip('"')
                elif line.startswith("VERSION_ID="):
                    info["version_id"] = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass

    # Kernel
    info["kernel"] = os.uname().release

    # Architecture
    info["architecture"] = os.uname().machine

    # Uptime from /proc/uptime
    try:
        with open("/proc/uptime", "r") as f:
            uptime_secs = float(f.read().split()[0])
            info["uptime_seconds"] = int(uptime_secs)
            days = int(uptime_secs) // 86400
            hours = (int(uptime_secs) % 86400) // 3600
            minutes = (int(uptime_secs) % 3600) // 60
            parts = []
            if days > 0:
                parts.append(f"{days}d")
            parts.append(f"{hours}h")
            parts.append(f"{minutes}m")
            info["uptime_formatted"] = " ".join(parts)
    except (OSError, ValueError):
        pass

    # Boot time
    boot_output = run_command(["who", "-b"])
    if boot_output:
        # Format: "system boot  2026-01-25 13:16"
        parts = boot_output.strip().split()
        if len(parts) >= 4:
            info["boot_time"] = f"{parts[-2]} {parts[-1]}"

    # Load average
    try:
        load = os.getloadavg()
        info["load_average"] = [round(l, 2) for l in load]
    except OSError:
        pass

    # Process count
    try:
        count = sum(1 for entry in os.listdir("/proc") if entry.isdigit())
        info["process_count"] = count
    except OSError:
        pass

    # Users logged in
    who_output = run_command(["who"])
    if who_output:
        info["users_logged_in"] = len([l for l in who_output.split("\n") if l.strip()])
    else:
        info["users_logged_in"] = 0

    return info


def get_docker_container_pids():
    """Get mapping of PIDs to Docker containers using docker top"""
    pid_to_container = {}

    # First get list of running containers
    containers_output = run_command(
        ["docker", "ps", "--format", "{{.Names}}"], timeout=10
    )
    if not containers_output:
        return pid_to_container

    containers = [c.strip() for c in containers_output.strip().split("\n") if c.strip()]

    for container in containers:
        # Get PIDs from docker top
        top_output = run_command(["docker", "top", container, "-o", "pid"], timeout=5)
        if not top_output:
            continue

        lines = top_output.strip().split("\n")
        for line in lines[1:]:  # Skip header
            try:
                pid = int(line.strip())
                # Determine display name for container
                if container.startswith("archie_"):
                    display_name = "A.R.C.H.I.E."
                else:
                    display_name = container
                pid_to_container[pid] = {
                    "container": container,
                    "display_name": display_name,
                }
            except ValueError:
                continue

    return pid_to_container


def categorize_process(name, command, user):
    """Categorize a process into system/user/docker types"""
    # System processes typically run as root or system users
    system_users = [
        "root",
        "systemd",
        "daemon",
        "nobody",
        "messagebus",
        "syslog",
        "avahi",
        "colord",
        "cups",
        "gdm",
        "polkitd",
        "rtkit",
        "usbmux",
    ]

    system_processes = [
        "systemd",
        "init",
        "kthreadd",
        "ksoftirqd",
        "kworker",
        "migration",
        "watchdog",
        "cpuhp",
        "netns",
        "rcu_",
        "irq/",
        "dbus-daemon",
        "polkitd",
        "udisksd",
        "upowerd",
        "accounts-daemon",
        "gdm",
        "gnome-shell",
        "Xorg",
        "pulseaudio",
        "pipewire",
    ]

    # Check for kernel threads (shown in brackets)
    if command.startswith("[") and command.endswith("]"):
        return "kernel"

    # Check for system processes
    for sp in system_processes:
        if name.startswith(sp) or command.startswith(sp):
            return "system"

    # Check for system users
    if user in system_users:
        return "system"

    return "user"


def get_process_list(limit=100):
    """Get running processes from the host via ps aux with Docker container detection"""
    processes = []

    # Get Docker container PID mapping
    docker_pids = get_docker_container_pids()

    output = run_command(["ps", "aux", "--sort=-pcpu"], timeout=15)
    if not output:
        return processes

    # ps aux status codes to readable names
    status_map = {
        "R": "running",
        "S": "sleeping",
        "D": "disk-sleep",
        "T": "stopped",
        "Z": "zombie",
        "I": "idle",
        "X": "dead",
        "W": "paging",
    }

    lines = output.split("\n")
    for line in lines[1:]:  # Skip header
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        try:
            raw_stat = parts[7]
            status = status_map.get(raw_stat[0], raw_stat) if raw_stat else "unknown"
            # Extract just the command name (not full path with args)
            command = parts[10]
            name = command.split()[0].rsplit("/", 1)[-1] if command else "unknown"
            pid = int(parts[1])
            user = parts[0]

            # Check if this PID belongs to a Docker container
            docker_info = docker_pids.get(pid, None)
            container = docker_info["container"] if docker_info else None
            container_display = docker_info["display_name"] if docker_info else None

            # Categorize the process
            if container:
                category = "docker"
            else:
                category = categorize_process(name, command, user)

            processes.append(
                {
                    "pid": pid,
                    "name": name[:80],
                    "command": command[:200],
                    "user": user,
                    "cpu_percent": float(parts[2]),
                    "memory_percent": float(parts[3]),
                    "rss_kb": int(parts[5]),
                    "status": status,
                    "container": container,
                    "container_display": container_display,
                    "category": category,
                }
            )
        except (ValueError, IndexError):
            continue

    return processes[:limit]


def update_bandwidth_history(network_data, container_bandwidth=None):
    """Update rolling bandwidth history buffer from current network data"""
    try:
        # Load existing history
        history = {"samples": [], "max_samples": 60}
        if os.path.exists(BANDWIDTH_FILE):
            try:
                with open(BANDWIDTH_FILE, "r") as f:
                    history = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        now = time.time()
        bandwidth_interfaces = network_data.get("bandwidth_interfaces", [])

        # Build current counters
        current = {}
        for iface in bandwidth_interfaces:
            current[iface["name"]] = {
                "rx_bytes": iface["rx_bytes"],
                "tx_bytes": iface["tx_bytes"],
                "rx_packets": iface["rx_packets"],
                "tx_packets": iface["tx_packets"],
            }

        # Calculate rates from previous sample
        rates = {}
        if history["samples"]:
            prev = history["samples"][-1]
            dt = now - prev.get("timestamp", now)
            if dt > 0:
                prev_counters = prev.get("counters", {})
                for name, cur_vals in current.items():
                    if name in prev_counters:
                        prev_vals = prev_counters[name]
                        rx_delta = max(
                            0, cur_vals["rx_bytes"] - prev_vals.get("rx_bytes", 0)
                        )
                        tx_delta = max(
                            0, cur_vals["tx_bytes"] - prev_vals.get("tx_bytes", 0)
                        )
                        rates[name] = {
                            "rx_bps": round(rx_delta / dt),
                            "tx_bps": round(tx_delta / dt),
                        }

        # Build sample
        sample = {"timestamp": now, "counters": current, "rates": rates}

        # Add container bandwidth if available
        if container_bandwidth:
            sample["containers"] = [
                {
                    "name": c["name"],
                    "rx_bytes": c["rx_bytes"],
                    "tx_bytes": c["tx_bytes"],
                }
                for c in container_bandwidth
            ]

        history["samples"].append(sample)

        # Trim to max samples
        max_s = history.get("max_samples", 60)
        if len(history["samples"]) > max_s:
            history["samples"] = history["samples"][-max_s:]

        # Write back
        with open(BANDWIDTH_FILE, "w") as f:
            json.dump(history, f, indent=2)
        os.chmod(BANDWIDTH_FILE, 0o644)

    except Exception as e:
        print(f"Error updating bandwidth history: {e}")


def get_container_bandwidth():
    """Get per-container network I/O from docker stats"""
    containers = []
    output = run_command(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.NetIO}}"],
        timeout=15,
    )
    if not output:
        return containers
    for line in output.strip().split("\n"):
        if "\t" not in line:
            continue
        parts = line.split("\t", 1)
        if len(parts) < 2:
            continue
        name = parts[0].strip()
        net_io = parts[1].strip()
        # Parse "1.23MB / 4.56GB" format
        rx_str, tx_str = "0B", "0B"
        if "/" in net_io:
            io_parts = net_io.split("/")
            rx_str = io_parts[0].strip()
            tx_str = io_parts[1].strip()

        def parse_size(s):
            s = s.strip().upper()
            multipliers = {
                "B": 1,
                "KB": 1024,
                "MB": 1024**2,
                "GB": 1024**3,
                "TB": 1024**4,
                "KIB": 1024,
                "MIB": 1024**2,
                "GIB": 1024**3,
                "TIB": 1024**4,
            }
            for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
                if s.endswith(suffix):
                    try:
                        return int(float(s[: -len(suffix)].strip()) * mult)
                    except ValueError:
                        return 0
            try:
                return int(float(s))
            except ValueError:
                return 0

        containers.append(
            {
                "name": name,
                "rx_bytes": parse_size(rx_str),
                "tx_bytes": parse_size(tx_str),
                "net_io_raw": net_io,
            }
        )
    return containers


# ============================================================================
# COMMAND QUEUE — run_command_full
# ============================================================================


def run_command_full(cmd, timeout=120):
    """Run a command and return full result dict with returncode, stdout, stderr.
    cmd must be a list (no shell=True) to prevent injection."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, shell=False
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
        }
    except Exception as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e)}


# ============================================================================
# DOCKER CONTAINER MANAGEMENT FOR STORAGE OPERATIONS
# ============================================================================


def get_mount_point_for_device(device):
    """Get the mount point for a device path."""
    result = run_command_full(["findmnt", "-n", "-o", "TARGET", device], timeout=10)
    if result["returncode"] == 0:
        return result["stdout"].strip()
    return None


def get_containers_using_path(path):
    """Get list of running Docker containers that have volumes mounted from the given path.

    Returns list of container names that should be stopped before storage operations.
    """
    if not path:
        return []

    containers = []
    try:
        # Get all running containers
        result = run_command_full(["docker", "ps", "-q"], timeout=30)
        if result["returncode"] != 0 or not result["stdout"].strip():
            return []

        container_ids = result["stdout"].strip().split("\n")

        for cid in container_ids:
            if not cid:
                continue
            # Get container name and mounts
            inspect = run_command_full(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.Name}} {{range .Mounts}}{{.Source}} {{end}}",
                    cid,
                ],
                timeout=10,
            )
            if inspect["returncode"] == 0:
                parts = inspect["stdout"].strip().split()
                if len(parts) > 0:
                    name = parts[0].lstrip("/")
                    mounts = parts[1:] if len(parts) > 1 else []
                    # Check if any mount is under the target path
                    for mount in mounts:
                        if mount.startswith(path) or path.startswith(mount):
                            containers.append(name)
                            break
    except Exception:
        pass

    return containers


def stop_containers(containers, timeout=60):
    """Stop a list of containers. Returns dict with results."""
    stopped = []
    failed = []

    for name in containers:
        result = run_command_full(["docker", "stop", "-t", "30", name], timeout=timeout)
        if result["returncode"] == 0:
            stopped.append(name)
        else:
            failed.append({"name": name, "error": result["stderr"]})

    return {"stopped": stopped, "failed": failed}


def start_containers(containers, timeout=30):
    """Start a list of containers. Returns dict with results."""
    started = []
    failed = []

    for name in containers:
        result = run_command_full(["docker", "start", name], timeout=timeout)
        if result["returncode"] == 0:
            started.append(name)
        else:
            failed.append({"name": name, "error": result["stderr"]})

    return {"started": started, "failed": failed}


# ============================================================================
# COMMAND REGISTRY — whitelist of allowed command types
# ============================================================================

# Param validators: dict of param_name -> {required: bool, regex: str, validator: callable}
_DEV_PATH_RE = r"^/dev/[a-zA-Z0-9_/\-]+$"
_MOUNT_PATH_RE = r"^/[a-zA-Z0-9_/.\-]+$"
_NAME_RE = r"^[a-zA-Z0-9_\-]+$"
_FSTYPE_RE = r"^(ext4|ext3|ext2|xfs|btrfs|vfat|ntfs|swap)$"

COMMAND_REGISTRY = {
    "service_control": {
        "handler": "handle_service_control",
        "params": {
            "service": {"required": True, "regex": _NAME_RE},
            "action": {"required": True, "regex": r"^(restart|start|stop|status)$"},
        },
    },
    "discover_media_services": {
        "handler": "handle_discover_media_services",
        "params": {},
    },
    "filesystem_resize": {
        "handler": "handle_filesystem_resize",
        "params": {
            "device": {"required": True, "regex": _DEV_PATH_RE},
        },
    },
    "lvm_extend": {
        "handler": "handle_lvm_extend",
        "params": {
            "vg_name": {"required": True, "regex": _NAME_RE},
            "lv_name": {"required": True, "regex": _NAME_RE},
            "size_gb": {
                "required": False,
                "validator": lambda v: isinstance(v, (int, float)) and 0 < v <= 10000,
            },
            "extend_all": {
                "required": False,
                "validator": lambda v: isinstance(v, bool),
            },
        },
    },
    "lvm_shrink": {
        "handler": "handle_lvm_shrink",
        "dangerous": True,
        "params": {
            "vg_name": {"required": True, "regex": _NAME_RE},
            "lv_name": {"required": True, "regex": _NAME_RE},
            "target_size_gb": {
                "required": True,
                "validator": lambda v: isinstance(v, (int, float)) and v > 0,
            },
        },
    },
    "lvm_rename": {
        "handler": "handle_lvm_rename",
        "params": {
            "vg_name": {"required": True, "regex": _NAME_RE},
            "old_lv_name": {"required": True, "regex": _NAME_RE},
            "new_lv_name": {"required": True, "regex": _NAME_RE},
        },
    },
    "lvm_create": {
        "handler": "handle_lvm_create",
        "params": {
            "vg_name": {"required": True, "regex": _NAME_RE},
            "lv_name": {"required": True, "regex": _NAME_RE},
            "size_gb": {
                "required": True,
                "validator": lambda v: isinstance(v, (int, float)) and v > 0,
            },
            "fstype": {"required": True, "regex": _FSTYPE_RE},
            "mountpoint": {"required": False, "regex": _MOUNT_PATH_RE},
        },
    },
    "vg_create": {
        "handler": "handle_vg_create",
        "dangerous": True,
        "params": {
            "vg_name": {"required": True, "regex": _NAME_RE},
            "pv_device": {"required": True, "regex": _DEV_PATH_RE},
        },
    },
    "lvm_snapshot": {
        "handler": "handle_lvm_snapshot",
        "params": {
            "vg_name": {"required": True, "regex": _NAME_RE},
            "lv_name": {"required": True, "regex": _NAME_RE},
            "snapshot_name": {"required": True, "regex": _NAME_RE},
            "size_gb": {
                "required": True,
                "validator": lambda v: isinstance(v, (int, float)) and v > 0,
            },
        },
    },
    "disk_mount": {
        "handler": "handle_disk_mount",
        "params": {
            "device": {"required": True, "regex": _DEV_PATH_RE},
            "mountpoint": {"required": True, "regex": _MOUNT_PATH_RE},
            "fstype": {"required": False, "regex": _FSTYPE_RE},
            "options": {"required": False, "regex": r"^[a-zA-Z0-9_,=]+$"},
            "persist": {"required": False, "validator": lambda v: isinstance(v, bool)},
        },
    },
    "disk_unmount": {
        "handler": "handle_disk_unmount",
        "params": {
            "device": {"required": True, "regex": _DEV_PATH_RE},
            "force": {"required": False, "validator": lambda v: isinstance(v, bool)},
            "remove_persist": {
                "required": False,
                "validator": lambda v: isinstance(v, bool),
            },
        },
    },
    "fix_stale_mount": {
        "handler": "handle_fix_stale_mount",
        "params": {
            "mountpoint": {"required": True, "regex": _MOUNT_PATH_RE},
            "new_device": {"required": True, "regex": _DEV_PATH_RE},
        },
    },
    "disk_format": {
        "handler": "handle_disk_format",
        "dangerous": True,
        "params": {
            "device": {"required": True, "regex": _DEV_PATH_RE},
            "fstype": {"required": True, "regex": _FSTYPE_RE},
            "label": {"required": False, "regex": r"^[a-zA-Z0-9_\-]{1,32}$"},
        },
    },
    "partition_create": {
        "handler": "handle_partition_create",
        "dangerous": True,
        "params": {
            "device": {"required": True, "regex": _DEV_PATH_RE},
            "size_gb": {
                "required": True,
                "validator": lambda v: isinstance(v, (int, float)) and v > 0,
            },
            "part_type": {"required": False, "regex": r"^(primary|logical|extended)$"},
        },
    },
    "pv_create_vg_extend": {
        "handler": "handle_pv_create_vg_extend",
        "dangerous": True,
        "params": {
            "device": {"required": True, "regex": _DEV_PATH_RE},
            "vg_name": {"required": True, "regex": _NAME_RE},
        },
    },
    "disk_prepare": {
        "handler": "handle_disk_prepare",
        "dangerous": True,
        "params": {
            "device": {"required": True, "regex": _DEV_PATH_RE},
            "fstype": {"required": True, "regex": _FSTYPE_RE},
            "partition_table": {"required": False, "regex": r"^(gpt|msdos)$"},
            "label": {"required": False, "regex": r"^[a-zA-Z0-9_\-]{1,32}$"},
        },
    },
    "disk_prepare_lvm": {
        "handler": "handle_disk_prepare_lvm",
        "dangerous": True,
        "params": {
            "device": {"required": True, "regex": _DEV_PATH_RE},
            "vg_name": {"required": True, "regex": _NAME_RE},
            "lv_name": {"required": True, "regex": _NAME_RE},
            "fstype": {"required": False, "regex": _FSTYPE_RE},
            "mountpoint": {"required": False, "regex": r"^/[a-zA-Z0-9_/\.\-]+$"},
        },
    },
    "disk_label": {
        "handler": "handle_disk_label",
        "params": {
            "device": {"required": True, "regex": _DEV_PATH_RE},
            "label": {"required": True, "regex": r"^[a-zA-Z0-9_\- ]{0,16}$"},
            "fstype": {"required": True, "regex": _FSTYPE_RE},
        },
    },
    "convert_to_lvm": {
        "handler": "handle_convert_to_lvm",
        "dangerous": True,
        "params": {
            "device": {"required": True, "regex": _DEV_PATH_RE},
            "vg_name": {"required": True, "regex": _NAME_RE},
            "lv_name": {"required": True, "regex": _NAME_RE},
            "fstype": {"required": True, "regex": _FSTYPE_RE},
        },
    },
    "disk_wipe": {
        "handler": "handle_disk_wipe",
        "dangerous": True,
        "params": {
            "device": {"required": True, "regex": _DEV_PATH_RE},
            "wipe_lvm": {"required": False, "validator": lambda v: isinstance(v, bool)},
            "wipe_partition_table": {
                "required": False,
                "validator": lambda v: isinstance(v, bool),
            },
        },
    },
    # -------------------------------------------------------------------------
    # FIREWALL (UFW) COMMANDS
    # -------------------------------------------------------------------------
    "firewall_status": {
        "handler": "handle_firewall_status",
        "params": {},
    },
    "firewall_rules": {
        "handler": "handle_firewall_rules",
        "params": {},
    },
    "firewall_enable": {
        "handler": "handle_firewall_enable",
        "dangerous": True,
        "params": {},
    },
    "firewall_disable": {
        "handler": "handle_firewall_disable",
        "dangerous": True,
        "params": {},
    },
    "firewall_allow": {
        "handler": "handle_firewall_allow",
        "params": {
            "port": {
                "required": True,
                "validator": lambda v: isinstance(v, int) and 1 <= v <= 65535,
            },
            "protocol": {"required": True, "regex": r"^(tcp|udp)$"},
        },
    },
    "firewall_deny": {
        "handler": "handle_firewall_deny",
        "params": {
            "port": {
                "required": True,
                "validator": lambda v: isinstance(v, int) and 1 <= v <= 65535,
            },
            "protocol": {"required": True, "regex": r"^(tcp|udp)$"},
        },
    },
    "firewall_delete": {
        "handler": "handle_firewall_delete",
        "dangerous": True,
        "params": {
            "rule_number": {
                "required": True,
                "validator": lambda v: isinstance(v, int) and v >= 1,
            },
        },
    },
    "firewall_update": {
        "handler": "handle_firewall_update",
        "params": {
            "old_port": {
                "required": True,
                "validator": lambda v: isinstance(v, int) and 1 <= v <= 65535,
            },
            "old_protocol": {"required": True, "regex": r"^(tcp|udp)$"},
            "new_port": {
                "required": True,
                "validator": lambda v: isinstance(v, int) and 1 <= v <= 65535,
            },
            "new_protocol": {"required": True, "regex": r"^(tcp|udp)$"},
            "action": {"required": True, "regex": r"^(allow|deny)$"},
        },
    },
    "fail2ban_status": {
        "handler": "handle_fail2ban_status",
        "params": {},
    },
    "fail2ban_jail_status": {
        "handler": "handle_fail2ban_jail_status",
        "params": {
            "jail": {"required": True, "regex": _NAME_RE},
        },
    },
    "fail2ban_ban": {
        "handler": "handle_fail2ban_ban",
        "params": {
            "jail": {"required": True, "regex": _NAME_RE},
            "ip": {"required": True, "regex": r"^[0-9a-fA-F.:]+$"},
        },
    },
    "fail2ban_unban": {
        "handler": "handle_fail2ban_unban",
        "params": {
            "jail": {"required": True, "regex": _NAME_RE},
            "ip": {"required": True, "regex": r"^[0-9a-fA-F.:]+$"},
        },
    },
    "arp_scan": {
        "handler": "handle_arp_scan",
        "params": {},
    },
    # -------------------------------------------------------------------------
    # NGINX COMMANDS
    # -------------------------------------------------------------------------
    "nginx_list_configs": {
        "handler": "handle_nginx_list_configs",
        "params": {},
    },
    "nginx_get_config": {
        "handler": "handle_nginx_get_config",
        "params": {
            "name": {"required": True, "regex": r"^[a-zA-Z0-9_.\-]+$"},
        },
    },
    "nginx_test": {
        "handler": "handle_nginx_test",
        "params": {},
    },
    "nginx_reload": {
        "handler": "handle_nginx_reload",
        "params": {},
    },
    # Compliance scanning — read-only checks
    "compliance_read_file": {
        "handler": "handle_compliance_read_file",
        "params": {
            "path": {"required": True, "regex": r"^/[a-zA-Z0-9_/.\-]+$"},
        },
    },
    "compliance_check_sysctl": {
        "handler": "handle_compliance_check_sysctl",
        "params": {
            "param": {"required": True, "regex": r"^[a-zA-Z0-9_.]+$"},
        },
    },
    "compliance_run_check": {
        "handler": "handle_compliance_run_check",
        "params": {
            "check_type": {"required": True, "regex": r"^(file_permission|service_status|package_installed|port_listening|command_safe)$"},
            "target": {"required": True, "regex": r"^[a-zA-Z0-9_/.\-: ]+$"},
        },
    },
    "compliance_fix_sysctl": {
        "handler": "handle_compliance_fix_sysctl",
        "params": {
            "param": {"required": True, "regex": r"^[a-zA-Z0-9_.]+$"},
            "value": {"required": True, "regex": r"^[a-zA-Z0-9_.\- ]+$"},
            "dry_run": {"required": False},
        },
    },
    "compliance_fix_file_line": {
        "handler": "handle_compliance_fix_file_line",
        "params": {
            "path": {"required": True, "regex": r"^/[a-zA-Z0-9_/.\-]+$"},
            "line": {"required": True},
            "restart_service": {"required": False},
            "dry_run": {"required": False},
        },
    },
    "compliance_fix_service": {
        "handler": "handle_compliance_fix_service",
        "params": {
            "service": {"required": True, "regex": r"^[a-zA-Z0-9_.\-]+$"},
            "action": {"required": True, "regex": r"^(start|stop|restart|enable|disable)$"},
            "dry_run": {"required": False},
        },
    },
    "compliance_restore_file": {
        "handler": "handle_compliance_restore_file",
        "params": {
            "path": {"required": True, "regex": r"^/[a-zA-Z0-9_/.\-]+$"},
            "content": {"required": True},
        },
    },
    "compliance_fix_command": {
        "handler": "handle_compliance_fix_command",
        "params": {
            "command": {"required": True},
            "dry_run": {"required": False},
        },
    },
    "compliance_check_command": {
        "handler": "handle_compliance_check_command",
        "params": {
            "command": {"required": True},
        },
    },
}


# ============================================================================
# COMMAND VALIDATION
# ============================================================================


def validate_command(cmd_data):
    """Validate a command dict against the registry.
    Returns (True, None) or (False, error_string)."""
    cmd_type = cmd_data.get("command_type")
    if cmd_type not in COMMAND_REGISTRY:
        return False, f"Unknown command type: {cmd_type}"

    spec = COMMAND_REGISTRY[cmd_type]
    params = cmd_data.get("params", {})

    # Check expiry
    submitted_at = cmd_data.get("submitted_at")
    if submitted_at:
        try:
            sub_time = datetime.fromisoformat(submitted_at).timestamp()
            if time.time() - sub_time > COMMAND_MAX_AGE_SECONDS:
                return False, "Command expired (older than 5 minutes)"
        except (ValueError, TypeError):
            return False, "Invalid submitted_at timestamp"

    # Validate params
    for param_name, param_spec in spec["params"].items():
        value = params.get(param_name)

        if param_spec.get("required") and value is None:
            return False, f"Missing required parameter: {param_name}"

        if value is not None:
            # Regex validation (string params)
            if "regex" in param_spec:
                if not isinstance(value, str):
                    return False, f"Parameter {param_name} must be a string"
                if not re.match(param_spec["regex"], value):
                    return (
                        False,
                        f'Invalid value for {param_name}: fails regex {param_spec["regex"]}',
                    )

            # Lambda validation
            if "validator" in param_spec:
                if not param_spec["validator"](value):
                    return False, f"Invalid value for {param_name}"

    # Check for unexpected params
    allowed = set(spec["params"].keys())
    extra = set(params.keys()) - allowed
    if extra:
        return False, f'Unexpected parameters: {", ".join(extra)}'

    return True, None


# ============================================================================
# COMMAND HANDLERS
# ============================================================================


def handle_fail2ban_status(params):
    """Get fail2ban overall status and jail list."""
    output = run_command(["fail2ban-client", "status"], timeout=10)
    if output is None:
        return {"success": False, "error": "fail2ban-client not available"}
    jails = []
    for line in output.splitlines():
        if "Jail list:" in line:
            jails_str = line.split("Jail list:")[1].strip()
            jails = [j.strip() for j in jails_str.split(",") if j.strip()]
    return {"success": True, "running": True, "jails": jails, "raw": output.strip()}


def handle_fail2ban_jail_status(params):
    """Get detailed status for a specific fail2ban jail."""
    jail = params["jail"]
    output = run_command(["fail2ban-client", "status", jail], timeout=10)
    if output is None:
        return {"success": False, "error": f"Failed to get status for jail {jail}"}
    status = {"jail": jail, "currently_failed": 0, "total_failed": 0,
              "currently_banned": 0, "total_banned": 0, "banned_ips": []}
    for line in output.splitlines():
        line = line.strip()
        if "Currently failed:" in line:
            status["currently_failed"] = int(line.split(":")[-1].strip())
        elif "Total failed:" in line:
            status["total_failed"] = int(line.split(":")[-1].strip())
        elif "Currently banned:" in line:
            status["currently_banned"] = int(line.split(":")[-1].strip())
        elif "Total banned:" in line:
            status["total_banned"] = int(line.split(":")[-1].strip())
        elif "Banned IP list:" in line:
            ips_str = line.split(":")[-1].strip()
            status["banned_ips"] = [ip.strip() for ip in ips_str.split() if ip.strip()]
    return {"success": True, **status}


def handle_fail2ban_ban(params):
    """Ban an IP in a specific fail2ban jail."""
    jail = params["jail"]
    ip = params["ip"]
    output = run_command(["fail2ban-client", "set", jail, "banip", ip], timeout=10)
    return {"success": output is not None, "output": (output or "").strip()}


def handle_fail2ban_unban(params):
    """Unban an IP from a specific fail2ban jail."""
    jail = params["jail"]
    ip = params["ip"]
    output = run_command(["fail2ban-client", "set", jail, "unbanip", ip], timeout=10)
    return {"success": output is not None, "output": (output or "").strip()}


def handle_arp_scan(params):
    """Return the host ARP table — IP to MAC mappings for LAN devices."""
    import subprocess

    try:
        result = subprocess.run(
            ["ip", "neigh", "show"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        entries = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            ip = parts[0]
            # Skip non-LAN IPs
            if not ip.startswith("192.168.") and not ip.startswith("10."):
                continue
            # Find MAC (comes after "lladdr")
            mac = ""
            if "lladdr" in parts:
                mac_idx = parts.index("lladdr") + 1
                if mac_idx < len(parts):
                    mac = parts[mac_idx]
            state = parts[-1] if parts[-1] in ("REACHABLE", "STALE", "DELAY", "PROBE", "FAILED", "INCOMPLETE") else "unknown"
            if state == "FAILED" or state == "INCOMPLETE":
                continue
            entries.append({"ip": ip, "mac": mac, "state": state})
        return {"success": True, "entries": entries, "count": len(entries)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_service_control(params):
    """Control a systemd service (start, stop, restart, status)."""
    service = params["service"]
    action = params["action"]

    # Whitelist of services that can be controlled
    allowed_services = ["host_monitor", "docker", "ssh", "cron", "ufw"]
    if service not in allowed_services:
        return {
            "success": False,
            "error": f"Service {service} not in allowed list: {allowed_services}",
        }

    if action == "status":
        result = run_command_full(
            ["systemctl", "is-active", f"{service}.service"], timeout=10
        )
        status = result["stdout"].strip() if result["returncode"] == 0 else "unknown"
        return {"success": True, "service": service, "status": status}

    # For restart/start/stop, use sudo
    result = run_command_full(
        ["sudo", "-n", "systemctl", action, f"{service}.service"], timeout=30
    )
    if result["returncode"] == 0:
        return {"success": True, "message": f"Service {service} {action} successful"}
    else:
        return {"success": False, "error": result["stderr"] or f"{action} failed"}


def handle_discover_media_services(params):
    """Discover API keys from media service config files on the host.

    This allows archie_platform to get API keys without needing mounts to config directories.
    Returns discovered API keys for Plex, Sonarr, Radarr, Prowlarr, Overseerr, SABnzbd.
    """
    import configparser
    from xml.etree import ElementTree as ET

    CONFIG_PATHS = {
        "plex": "/mnt/Plex/config/plex/Library/Application Support/Plex Media Server/Preferences.xml",
        "sonarr": "/mnt/Plex/config/sonarr/config.xml",
        "radarr": "/mnt/Plex/config/radarr/config.xml",
        "prowlarr": "/mnt/Plex/config/prowlarr/config.xml",
        "overseerr": "/mnt/Plex/config/overseerr/settings.json",
        "jellyseerr": "/mnt/Plex/config/jellyseerr/settings.json",
        "sabnzbd": "/mnt/Plex/config/sabnzbd/sabnzbd.ini",
    }

    results = {}

    # Plex - get token from Preferences.xml
    try:
        path = CONFIG_PATHS["plex"]
        if os.path.exists(path):
            tree = ET.parse(path)
            root = tree.getroot()
            token = root.get("PlexOnlineToken") or root.get("PlexOnlineHome")
            results["plex"] = {"found": bool(token), "api_key": token, "port": 32400}
        else:
            results["plex"] = {
                "found": False,
                "api_key": None,
                "port": 32400,
                "error": "Config not found",
            }
    except Exception as e:
        results["plex"] = {
            "found": False,
            "api_key": None,
            "port": 32400,
            "error": str(e),
        }

    # Arr services (Sonarr, Radarr, Prowlarr) - get API key from config.xml
    for service, port in [("sonarr", 8989), ("radarr", 7878), ("prowlarr", 9696)]:
        try:
            path = CONFIG_PATHS[service]
            if os.path.exists(path):
                tree = ET.parse(path)
                root = tree.getroot()
                api_key_elem = root.find(".//ApiKey")
                api_key = api_key_elem.text if api_key_elem is not None else None
                results[service] = {
                    "found": bool(api_key),
                    "api_key": api_key,
                    "port": port,
                }
            else:
                results[service] = {
                    "found": False,
                    "api_key": None,
                    "port": port,
                    "error": "Config not found",
                }
        except Exception as e:
            results[service] = {
                "found": False,
                "api_key": None,
                "port": port,
                "error": str(e),
            }

    # Overseerr/Jellyseerr - get API key from settings.json
    for service, port in [("overseerr", 5055), ("jellyseerr", 5055)]:
        try:
            path = CONFIG_PATHS.get(service)
            if path and os.path.exists(path):
                with open(path, "r") as f:
                    settings = json.load(f)
                main = settings.get("main", {})
                api_key = main.get("apiKey")
                results[service] = {
                    "found": bool(api_key),
                    "api_key": api_key,
                    "port": port,
                }
            else:
                results[service] = {
                    "found": False,
                    "api_key": None,
                    "port": port,
                    "error": "Config not found",
                }
        except Exception as e:
            results[service] = {
                "found": False,
                "api_key": None,
                "port": port,
                "error": str(e),
            }

    # SABnzbd - get API key from sabnzbd.ini
    try:
        path = CONFIG_PATHS["sabnzbd"]
        if os.path.exists(path):
            config = configparser.ConfigParser()
            config.read(path)
            api_key = config.get("misc", "api_key", fallback=None)
            results["sabnzbd"] = {
                "found": bool(api_key),
                "api_key": api_key,
                "port": 8083,
            }
        else:
            results["sabnzbd"] = {
                "found": False,
                "api_key": None,
                "port": 8083,
                "error": "Config not found",
            }
    except Exception as e:
        results["sabnzbd"] = {
            "found": False,
            "api_key": None,
            "port": 8083,
            "error": str(e),
        }

    found_count = sum(1 for r in results.values() if r.get("found"))
    return {
        "success": True,
        "message": f"Discovered {found_count}/{len(results)} services",
        "services": results,
    }


def handle_filesystem_resize(params):
    """Resize a filesystem to fill its container (partition/LV).

    This is useful when the underlying device was extended but the
    filesystem wasn't resized to use the new space.

    Automatically stops Docker containers using the mount point and restarts them after.
    """
    import time

    device = params["device"]
    stop_containers_flag = params.get("stop_containers", True)  # Default to True

    # Detect filesystem type
    fs_result = run_command_full(
        ["sudo", "-n", "blkid", "-o", "value", "-s", "TYPE", device], timeout=10
    )
    if fs_result["returncode"] != 0:
        return {
            "success": False,
            "error": f'Could not detect filesystem type: {fs_result["stderr"]}',
        }

    fs_type = fs_result["stdout"].strip()
    if not fs_type:
        return {"success": False, "error": "No filesystem detected on device"}

    # Get mount point and containers using it
    mountpoint = get_mount_point_for_device(device)
    containers_to_restart = []
    restart_result = None

    if stop_containers_flag and mountpoint:
        containers = get_containers_using_path(mountpoint)
        if containers:
            # Stop containers before resize
            stop_result = stop_containers(containers, timeout=60)
            containers_to_restart = stop_result["stopped"]
            # Give containers time to fully stop
            time.sleep(2)

    # Sync filesystem before resize
    run_command_full(["sudo", "-n", "sync"], timeout=30)
    time.sleep(1)

    resize_result = None
    try:
        if fs_type in ("ext4", "ext3", "ext2"):
            # Try up to 3 times with delays if device is busy
            for attempt in range(3):
                resize_result = run_command_full(
                    ["sudo", "-n", "resize2fs", device], timeout=300
                )
                if resize_result["returncode"] == 0:
                    break
                if "busy" in resize_result["stderr"].lower():
                    time.sleep(3)  # Wait and retry
                else:
                    break  # Non-busy error, don't retry
        elif fs_type == "xfs":
            # xfs_growfs requires the mount point, not device path
            if mountpoint:
                resize_result = run_command_full(
                    ["sudo", "-n", "xfs_growfs", mountpoint], timeout=300
                )
            else:
                resize_result = {
                    "returncode": -1,
                    "stdout": "",
                    "stderr": "XFS volume is not mounted (cannot resize)",
                }
        elif fs_type == "btrfs":
            if mountpoint:
                resize_result = run_command_full(
                    ["sudo", "-n", "btrfs", "filesystem", "resize", "max", mountpoint],
                    timeout=300,
                )
            else:
                resize_result = {
                    "returncode": -1,
                    "stdout": "",
                    "stderr": "Btrfs volume is not mounted (cannot resize)",
                }
        else:
            resize_result = {
                "returncode": -1,
                "stdout": "",
                "stderr": f"Filesystem resize not supported for {fs_type}",
            }

    finally:
        # Always restart containers that were stopped
        if containers_to_restart:
            time.sleep(1)
            restart_result = start_containers(containers_to_restart, timeout=30)

    # Build result
    if resize_result is None or resize_result["returncode"] != 0:
        error_msg = resize_result["stderr"] if resize_result else "No resize attempted"
        result = {
            "success": False,
            "error": f"Filesystem resize failed: {error_msg}",
            "containers_stopped": containers_to_restart,
        }
        if restart_result:
            result["containers_restarted"] = restart_result.get("started", [])
        return result

    result = {
        "success": True,
        "message": f"Successfully resized {fs_type} filesystem on {device}",
        "filesystem": fs_type,
        "output": resize_result.get("stdout", ""),
        "containers_stopped": containers_to_restart,
    }
    if restart_result:
        result["containers_restarted"] = restart_result.get("started", [])
    return result


def handle_lvm_extend(params):
    """Extend an LVM logical volume with Docker container handling"""
    vg_name = params["vg_name"]
    lv_name = params["lv_name"]
    lv_path = f"/dev/{vg_name}/{lv_name}"

    # Get mountpoint for container detection
    mount_result = run_command_full(
        ["findmnt", "-n", "-o", "TARGET", lv_path], timeout=10
    )
    mountpoint = mount_result["stdout"].strip()

    # Protected system paths - don't stop containers for these (too risky)
    PROTECTED_PATHS = [
        "/",
        "/boot",
        "/boot/efi",
        "/var",
        "/usr",
        "/etc",
        "/home",
        "/tmp",
    ]

    # Track containers to restart
    containers_to_restart = []
    restart_result = None
    skip_container_stop = False

    # Check if this is a protected system path
    if mountpoint in PROTECTED_PATHS:
        print(
            f"Mountpoint {mountpoint} is a protected system path - skipping container stop"
        )
        skip_container_stop = True

    # Stop containers using this mountpoint for safer resize (unless protected)
    if mountpoint and not skip_container_stop:
        try:
            containers = get_containers_using_path(mountpoint)
            # Never stop archie_platform - it's running this operation!
            containers = [c for c in containers if c != "archie_platform"]
            if containers:
                print(
                    f"Stopping {len(containers)} containers using {mountpoint} for extend operation"
                )
                stop_result = stop_containers(containers, timeout=120)
                containers_to_restart = stop_result.get("stopped", [])
                time.sleep(2)  # Give containers time to fully stop
        except Exception as e:
            print(f"Container stop check failed: {e}")

    try:
        # Build lvextend command
        if params.get("extend_all"):
            cmd = ["sudo", "-n", "lvextend", "-l", "+100%FREE", lv_path]
        elif params.get("size_gb"):
            cmd = ["sudo", "-n", "lvextend", "-L", f'+{params["size_gb"]}G', lv_path]
        else:
            return {
                "success": False,
                "error": "Must specify size_gb or extend_all",
                "containers_stopped": containers_to_restart,
            }

        result = run_command_full(cmd, timeout=60)
        lv_extended = result["returncode"] == 0
        lv_already_max = False

        if result["returncode"] != 0:
            stderr = result["stderr"].lower()
            # Check if failure is because there's no space to extend (LV already at max)
            # This is OK - we still want to try resizing the filesystem
            if (
                "no space" in stderr
                or "insufficient" in stderr
                or "not enough" in stderr
                or "matches existing size" in stderr
            ):
                lv_already_max = True
            else:
                return {
                    "success": False,
                    "error": result["stderr"] or "lvextend failed",
                    "stdout": result["stdout"],
                    "containers_stopped": containers_to_restart,
                }

        # Detect filesystem type and resize
        fs_result = run_command_full(
            ["sudo", "-n", "blkid", "-o", "value", "-s", "TYPE", lv_path], timeout=10
        )
        fs_type = fs_result["stdout"].strip()

        # Try both the symlink path and the device mapper path for resize
        device_paths = [lv_path]
        # Add device mapper path as fallback (handles dashes in names)
        dm_name = f"{vg_name.replace('-', '--')}-{lv_name.replace('-', '--')}"
        device_paths.append(f"/dev/mapper/{dm_name}")

        resize_result = None
        if fs_type in ("ext4", "ext3", "ext2"):
            for dev_path in device_paths:
                resize_result = run_command_full(
                    ["sudo", "-n", "resize2fs", dev_path], timeout=120
                )
                if resize_result["returncode"] == 0:
                    break
                # If "busy", wait and retry once
                if "busy" in resize_result["stderr"].lower():
                    time.sleep(2)
                    resize_result = run_command_full(
                        ["sudo", "-n", "resize2fs", dev_path], timeout=120
                    )
                    if resize_result["returncode"] == 0:
                        break
        elif fs_type == "xfs":
            # xfs_growfs requires the mount point, not device path
            if not mountpoint:
                return {
                    "success": True,
                    "message": f"LV extended but XFS volume is not mounted (cannot resize)",
                    "filesystem": fs_type,
                    "containers_stopped": containers_to_restart,
                }
            resize_result = run_command_full(
                ["sudo", "-n", "xfs_growfs", mountpoint], timeout=120
            )
        else:
            lv_status = (
                "LV extended"
                if lv_extended
                else ("LV already at max size" if lv_already_max else "LV unchanged")
            )
            return {
                "success": True,
                "message": f"{lv_status} but filesystem resize not supported for {fs_type}",
                "filesystem": fs_type,
                "containers_stopped": containers_to_restart,
            }

        if resize_result is None or resize_result["returncode"] != 0:
            lv_status = (
                "LV extended"
                if lv_extended
                else ("LV already at max size" if lv_already_max else "LV unchanged")
            )
            return {
                "success": False,
                "message": f"{lv_status} but filesystem resize failed",
                "error": (
                    resize_result["stderr"] if resize_result else "No resize attempted"
                ),
                "containers_stopped": containers_to_restart,
            }

        lv_status = (
            "Extended LV and"
            if lv_extended
            else ("LV already at max size," if lv_already_max else "")
        )
        final_result = {
            "success": True,
            "message": f"{lv_status} resized {fs_type} filesystem on {lv_path}".strip(),
            "filesystem": fs_type,
            "containers_stopped": containers_to_restart,
        }

    finally:
        # Always restart containers that were stopped
        if containers_to_restart:
            time.sleep(1)
            restart_result = start_containers(containers_to_restart, timeout=60)

    if restart_result:
        final_result["containers_restarted"] = restart_result.get("started", [])
    return final_result


def handle_lvm_shrink(params):
    """Shrink an LVM logical volume (ext4 only, with 10% safety buffer)"""
    vg_name = params["vg_name"]
    lv_name = params["lv_name"]
    target_size_gb = params["target_size_gb"]
    lv_path = f"/dev/{vg_name}/{lv_name}"

    # Check filesystem type (only ext4 supports shrink)
    fs_result = run_command_full(
        ["sudo", "-n", "blkid", "-o", "value", "-s", "TYPE", lv_path], timeout=10
    )
    fs_type = fs_result["stdout"].strip()
    if fs_type not in ("ext4", "ext3", "ext2"):
        return {
            "success": False,
            "error": f"Shrink only supported for ext filesystems, found {fs_type}. XFS cannot be shrunk.",
        }

    # Check if mounted - get mountpoint
    mount_check = run_command_full(
        ["findmnt", "-n", "-o", "TARGET", lv_path], timeout=10
    )
    mountpoint = mount_check["stdout"].strip()

    # Protected system paths - cannot shrink these while system is running
    PROTECTED_PATHS = [
        "/",
        "/boot",
        "/boot/efi",
        "/var",
        "/usr",
        "/etc",
        "/home",
        "/tmp",
    ]
    if mountpoint in PROTECTED_PATHS:
        return {
            "success": False,
            "error": f"Cannot shrink {mountpoint} filesystem while system is running",
        }

    # Track containers to restart
    containers_to_restart = []
    restart_result = None

    # Get current usage to enforce 10% safety buffer
    if mountpoint:
        df_result = run_command_full(["df", "-B1", lv_path], timeout=10)
        if df_result["returncode"] == 0:
            lines = df_result["stdout"].strip().split("\n")
            if len(lines) >= 2:
                parts = lines[1].split()
                if len(parts) >= 3:
                    used_bytes = int(parts[2])
                    used_gb = used_bytes / (1024**3)
                    min_safe_gb = used_gb * 1.10  # 10% buffer
                    if target_size_gb < min_safe_gb:
                        return {
                            "success": False,
                            "error": f"Target size {target_size_gb:.1f}GB is too small. Minimum safe size is {min_safe_gb:.1f}GB (used: {used_gb:.1f}GB + 10% buffer)",
                        }

        # Stop containers using this mountpoint before unmount
        try:
            containers = get_containers_using_path(mountpoint)
            # Never stop archie_platform - it's running this operation!
            containers = [c for c in containers if c != "archie_platform"]
            if containers:
                print(
                    f"Stopping {len(containers)} containers using {mountpoint} for shrink operation"
                )
                stop_result = stop_containers(containers, timeout=120)
                containers_to_restart = stop_result.get("stopped", [])
                time.sleep(2)  # Give containers time to fully stop
        except Exception as e:
            print(f"Container stop check failed: {e}")

        # Unmount first
        umount = run_command_full(["sudo", "-n", "umount", lv_path], timeout=30)
        if umount["returncode"] != 0:
            # Restart containers if we stopped them
            if containers_to_restart:
                start_containers(containers_to_restart, timeout=60)
            return {
                "success": False,
                "error": f'Failed to unmount {mountpoint}: {umount["stderr"]}. Close any applications using this volume.',
            }

    try:
        # e2fsck first (required before shrinking)
        fsck = run_command_full(
            ["sudo", "-n", "e2fsck", "-f", "-y", lv_path], timeout=300
        )
        if fsck["returncode"] not in (0, 1):  # 1 = errors corrected
            # Try to remount if we unmounted
            if mountpoint:
                run_command_full(
                    ["sudo", "-n", "mount", lv_path, mountpoint], timeout=30
                )
            return {
                "success": False,
                "error": f'e2fsck failed: {fsck["stderr"]}',
                "containers_stopped": containers_to_restart,
            }

        # Shrink filesystem first (must be done before LV shrink)
        resize = run_command_full(
            ["sudo", "-n", "resize2fs", lv_path, f"{target_size_gb}G"], timeout=300
        )
        if resize["returncode"] != 0:
            # Try to remount if we unmounted
            if mountpoint:
                run_command_full(
                    ["sudo", "-n", "mount", lv_path, mountpoint], timeout=30
                )
            return {
                "success": False,
                "error": f'resize2fs shrink failed: {resize["stderr"]}',
                "containers_stopped": containers_to_restart,
            }

        # Shrink LV
        lv_shrink = run_command_full(
            ["sudo", "-n", "lvreduce", "-f", "-L", f"{target_size_gb}G", lv_path],
            timeout=60,
        )
        if lv_shrink["returncode"] != 0:
            # Filesystem is already shrunk, this is problematic but try to remount
            if mountpoint:
                run_command_full(
                    ["sudo", "-n", "mount", lv_path, mountpoint], timeout=30
                )
            return {
                "success": False,
                "error": f'lvreduce failed: {lv_shrink["stderr"]}',
                "containers_stopped": containers_to_restart,
            }

        # Remount if it was mounted
        if mountpoint:
            remount = run_command_full(
                ["sudo", "-n", "mount", lv_path, mountpoint], timeout=30
            )
            if remount["returncode"] != 0:
                return {
                    "success": True,
                    "message": f"Shrunk {lv_path} to {target_size_gb}GB but failed to remount at {mountpoint}",
                    "containers_stopped": containers_to_restart,
                }

    finally:
        # Always restart containers that were stopped
        if containers_to_restart:
            time.sleep(1)
            restart_result = start_containers(containers_to_restart, timeout=60)

    result = {
        "success": True,
        "message": f"Shrunk {lv_path} to {target_size_gb}GB",
        "filesystem": fs_type,
        "containers_stopped": containers_to_restart,
    }
    if restart_result:
        result["containers_restarted"] = restart_result.get("started", [])
    return result


def handle_lvm_rename(params):
    """Rename an LVM logical volume, update /etc/fstab, and remount if needed"""
    vg_name = params["vg_name"]
    old_lv_name = params["old_lv_name"]
    new_lv_name = params["new_lv_name"]

    old_path = f"/dev/{vg_name}/{old_lv_name}"
    new_path = f"/dev/{vg_name}/{new_lv_name}"
    # Mapper paths use double-dash to escape dashes in names
    old_mapper = (
        f'/dev/mapper/{vg_name.replace("-", "--")}-{old_lv_name.replace("-", "--")}'
    )
    new_mapper = (
        f'/dev/mapper/{vg_name.replace("-", "--")}-{new_lv_name.replace("-", "--")}'
    )

    # Check if old LV exists
    check = run_command_full(["sudo", "-n", "lvs", old_path], timeout=10)
    if check["returncode"] != 0:
        return {"success": False, "error": f"Logical volume {old_path} not found"}

    # Check if new name already exists
    check_new = run_command_full(["sudo", "-n", "lvs", new_path], timeout=10)
    if check_new["returncode"] == 0:
        return {
            "success": False,
            "error": f"A logical volume named {new_lv_name} already exists in {vg_name}",
        }

    # Get current mountpoint (if any) before rename - check multiple path formats
    mountpoint = None
    mount_source = None
    for check_path in [old_path, old_mapper]:
        mount_check = run_command_full(
            ["findmnt", "-n", "-o", "TARGET", check_path], timeout=10
        )
        if mount_check["returncode"] == 0 and mount_check["stdout"].strip():
            mountpoint = mount_check["stdout"].strip()
            mount_source = check_path
            break

    # If mounted, we need to unmount before rename
    was_mounted = False
    if mountpoint:
        was_mounted = True
        # Get mount options to preserve them
        opts_check = run_command_full(
            ["findmnt", "-n", "-o", "OPTIONS", mountpoint], timeout=10
        )
        mount_options = (
            opts_check["stdout"].strip()
            if opts_check["returncode"] == 0
            else "defaults"
        )

        # Unmount the filesystem
        print(f"Unmounting {mountpoint} before LV rename")
        unmount_result = run_command_full(
            ["sudo", "-n", "umount", mountpoint], timeout=60
        )
        if unmount_result["returncode"] != 0:
            # Try lazy unmount if regular unmount fails
            print(
                f"Regular unmount failed, trying lazy unmount: {unmount_result['stderr']}"
            )
            unmount_result = run_command_full(
                ["sudo", "-n", "umount", "-l", mountpoint], timeout=30
            )
            if unmount_result["returncode"] != 0:
                return {
                    "success": False,
                    "error": f'Cannot unmount {mountpoint} for rename: {unmount_result["stderr"]}',
                }

    # Rename the LV
    rename_result = run_command_full(
        ["sudo", "-n", "lvrename", vg_name, old_lv_name, new_lv_name], timeout=30
    )
    if rename_result["returncode"] != 0:
        # If rename fails and we unmounted, try to remount with old path
        if was_mounted:
            run_command_full(["sudo", "-n", "mount", old_path, mountpoint], timeout=30)
        return {
            "success": False,
            "error": f'lvrename failed: {rename_result["stderr"]}',
        }

    # Update /etc/fstab if needed
    fstab_updated = False
    try:
        with open("/etc/fstab", "r") as f:
            fstab_content = f.read()

        # Check for various path formats in fstab
        old_patterns = [old_path, old_mapper, f"/dev/mapper/{vg_name}-{old_lv_name}"]
        new_replacement = new_path

        new_fstab = fstab_content
        for pattern in old_patterns:
            if pattern in new_fstab:
                new_fstab = new_fstab.replace(pattern, new_replacement)
                fstab_updated = True

        if fstab_updated:
            # Backup fstab first
            run_command_full(
                ["sudo", "-n", "cp", "/etc/fstab", "/etc/fstab.bak"], timeout=10
            )
            # Write new fstab
            with open("/tmp/fstab.new", "w") as f:
                f.write(new_fstab)
            run_command_full(
                ["sudo", "-n", "cp", "/tmp/fstab.new", "/etc/fstab"], timeout=10
            )
    except Exception as e:
        print(f"fstab update failed: {e}")
        # Continue - we'll try to remount anyway

    # Remount with new path if it was previously mounted
    remount_success = False
    if was_mounted:
        print(f"Remounting {mountpoint} with new path {new_path}")
        # Ensure mount point exists
        run_command_full(["sudo", "-n", "mkdir", "-p", mountpoint], timeout=10)
        mount_result = run_command_full(
            ["sudo", "-n", "mount", new_path, mountpoint], timeout=60
        )
        if mount_result["returncode"] != 0:
            # Try mapper path
            mount_result = run_command_full(
                ["sudo", "-n", "mount", new_mapper, mountpoint], timeout=60
            )

        remount_success = mount_result["returncode"] == 0
        if not remount_success:
            print(f"Remount failed: {mount_result['stderr']}")
            return {
                "success": True,
                "warning": f'LV renamed but remount failed: {mount_result["stderr"]}. Run: sudo mount {new_path} {mountpoint}',
                "fstab_updated": fstab_updated,
                "new_path": new_path,
                "remounted": False,
            }

    msg = f"Renamed {old_lv_name} to {new_lv_name}"
    if fstab_updated:
        msg += " (fstab updated)"
    if was_mounted and remount_success:
        msg += f" (remounted at {mountpoint})"

    return {
        "success": True,
        "message": msg,
        "fstab_updated": fstab_updated,
        "new_path": new_path,
        "remounted": remount_success if was_mounted else None,
    }


def handle_lvm_create(params):
    """Create a new LVM logical volume"""
    vg_name = params["vg_name"]
    lv_name = params["lv_name"]
    size_gb = params["size_gb"]
    fstype = params["fstype"]
    mountpoint = params.get("mountpoint")

    lv_path = f"/dev/{vg_name}/{lv_name}"

    # Create LV
    result = run_command_full(
        ["sudo", "-n", "lvcreate", "-L", f"{size_gb}G", "-n", lv_name, vg_name],
        timeout=60,
    )
    if result["returncode"] != 0:
        return {"success": False, "error": f'lvcreate failed: {result["stderr"]}'}

    # Format
    mkfs_cmd = ["sudo", "-n", f"mkfs.{fstype}", lv_path]
    fmt = run_command_full(mkfs_cmd, timeout=120)
    if fmt["returncode"] != 0:
        return {"success": False, "error": f'mkfs failed: {fmt["stderr"]}'}

    # Optional mount
    if mountpoint:
        run_command_full(["sudo", "-n", "mkdir", "-p", mountpoint], timeout=10)
        mnt = run_command_full(["sudo", "-n", "mount", lv_path, mountpoint], timeout=30)
        if mnt["returncode"] != 0:
            return {
                "success": True,
                "message": f'LV created and formatted but mount failed: {mnt["stderr"]}',
            }

    return {
        "success": True,
        "message": f"Created {lv_path} ({size_gb}GB, {fstype})",
        "device": lv_path,
    }


def handle_vg_create(params):
    """Create a new LVM volume group from a physical volume device.

    Steps:
    1. Verify device exists and is not mounted
    2. Create physical volume (pvcreate)
    3. Create volume group (vgcreate)
    """
    vg_name = params["vg_name"]
    pv_device = params["pv_device"]

    # Check device exists
    if not os.path.exists(pv_device):
        return {"success": False, "error": f"Device {pv_device} does not exist"}

    # Check if device is mounted
    result = run_command_full(["findmnt", "-n", pv_device], timeout=10)
    if result["returncode"] == 0 and result["stdout"].strip():
        return {
            "success": False,
            "error": f"Device {pv_device} is currently mounted. Unmount first.",
        }

    # Check if VG name already exists
    result = run_command_full(["sudo", "-n", "vgdisplay", vg_name], timeout=10)
    if result["returncode"] == 0:
        return {"success": False, "error": f"Volume group {vg_name} already exists"}

    # Check if device is already a PV
    result = run_command_full(["sudo", "-n", "pvdisplay", pv_device], timeout=10)
    if result["returncode"] == 0:
        return {
            "success": False,
            "error": f"Device {pv_device} is already a physical volume",
        }

    # Create physical volume
    result = run_command_full(["sudo", "-n", "pvcreate", pv_device], timeout=60)
    if result["returncode"] != 0:
        return {"success": False, "error": f'pvcreate failed: {result["stderr"]}'}

    # Create volume group
    result = run_command_full(
        ["sudo", "-n", "vgcreate", vg_name, pv_device], timeout=60
    )
    if result["returncode"] != 0:
        return {"success": False, "error": f'vgcreate failed: {result["stderr"]}'}

    return {
        "success": True,
        "message": f"Created volume group {vg_name} on {pv_device}",
        "vg_name": vg_name,
        "pv_device": pv_device,
    }


def handle_lvm_snapshot(params):
    """Create a snapshot of an LVM logical volume.

    Snapshots are copy-on-write and useful for backups.
    The snapshot needs its own space allocation.
    """
    vg_name = params["vg_name"]
    lv_name = params["lv_name"]
    snapshot_name = params["snapshot_name"]
    size_gb = params["size_gb"]

    source_lv = f"/dev/{vg_name}/{lv_name}"
    snapshot_lv = f"/dev/{vg_name}/{snapshot_name}"

    # Verify source LV exists
    if not os.path.exists(source_lv):
        return {"success": False, "error": f"Source LV {source_lv} does not exist"}

    # Check snapshot doesn't already exist
    if os.path.exists(snapshot_lv):
        return {
            "success": False,
            "error": f"Snapshot {snapshot_name} already exists in VG {vg_name}",
        }

    # Check VG has enough free space
    result = run_command_full(
        ["sudo", "-n", "vgs", "--noheadings", "--units", "g", "-o", "vg_free", vg_name],
        timeout=10,
    )
    if result["returncode"] != 0:
        return {
            "success": False,
            "error": f'Cannot check VG free space: {result["stderr"]}',
        }

    try:
        free_gb = float(result["stdout"].strip().rstrip("gG"))
        if free_gb < size_gb:
            return {
                "success": False,
                "error": f"Insufficient VG free space. Need {size_gb}GB, have {free_gb:.1f}GB",
            }
    except (ValueError, AttributeError):
        # Can't parse, let lvcreate fail with its own error
        pass

    # Create snapshot
    result = run_command_full(
        [
            "sudo",
            "-n",
            "lvcreate",
            "--snapshot",
            "--name",
            snapshot_name,
            "--size",
            f"{size_gb}G",
            source_lv,
        ],
        timeout=120,
    )

    if result["returncode"] != 0:
        return {
            "success": False,
            "error": f'Snapshot creation failed: {result["stderr"]}',
        }

    return {
        "success": True,
        "message": f"Created snapshot {snapshot_name} ({size_gb}GB) of {lv_name}",
        "snapshot_device": snapshot_lv,
        "source_device": source_lv,
    }


# ============================================================================
# FSTAB MANAGEMENT HELPERS
# ============================================================================


def get_device_uuid(device):
    """Get UUID for a device using blkid"""
    result = run_command_full(
        ["blkid", "-s", "UUID", "-o", "value", device], timeout=10
    )
    if result["returncode"] == 0 and result["stdout"].strip():
        return result["stdout"].strip()
    return None


def get_device_fstype(device):
    """Get filesystem type for a device using blkid"""
    result = run_command_full(
        ["blkid", "-s", "TYPE", "-o", "value", device], timeout=10
    )
    if result["returncode"] == 0 and result["stdout"].strip():
        return result["stdout"].strip()
    return None


def resolve_stable_id_to_device(stable_id):
    """Resolve stable_id to current /dev/sdX path.

    Args:
        stable_id: 'MODEL_SERIAL' (e.g., 'ST3000DM008 2DM166_Z504K0DJ') or '/dev/sdX' (passthrough)
    Returns:
        tuple: (device_path, error) - e.g., ('/dev/sda', None) or (None, 'error message')
    """
    if not stable_id:
        return (None, "No device identifier provided")

    # If already a device path, validate and return
    if stable_id.startswith("/dev/"):
        if os.path.exists(stable_id):
            return (stable_id, None)
        return (None, f"Device {stable_id} not found")

    # Search /dev/disk/by-id/ for matching entry
    by_id_dir = "/dev/disk/by-id"
    if not os.path.exists(by_id_dir):
        return (None, "/dev/disk/by-id not available")

    try:
        for entry in os.listdir(by_id_dir):
            # Skip dm-, lvm-, wwn-, md- entries
            if entry.startswith(("dm-", "lvm-", "wwn-", "md-")):
                continue

            # Check if the stable_id matches this entry
            # stable_id format: 'MODEL SERIAL_SERIAL' or 'MODEL_SERIAL-partN'
            # entry format: 'ata-MODEL_SERIAL' or 'ata-MODEL_SERIAL-partN'

            # Try to match by comparing the name portion after the bus prefix
            if "-" in entry:
                entry_name = entry.split("-", 1)[1]  # Remove ata-, nvme-, etc.

                # Normalize both for comparison
                normalized_stable = stable_id.replace(" ", "_")
                normalized_entry = entry_name

                if normalized_stable == normalized_entry:
                    full_path = os.path.join(by_id_dir, entry)
                    try:
                        target = os.readlink(full_path)
                        device_path = os.path.join("/dev", os.path.basename(target))
                        if os.path.exists(device_path):
                            return (device_path, None)
                    except OSError:
                        continue
    except OSError as e:
        return (None, f"Failed to read /dev/disk/by-id: {e}")

    return (None, f"No device found matching stable_id: {stable_id}")


def resolve_device_param(params, key="device"):
    """Resolve device parameter in-place. Returns error message or None on success.

    If the device value is a stable_id (not starting with /dev/), it will be
    resolved to the current device path and params[key] will be updated.
    The original stable_id is preserved in params['resolved_from'].

    Args:
        params: dict containing device parameter to resolve
        key: the key name for the device parameter (default: 'device')

    Returns:
        str or None: Error message if resolution failed, None on success
    """
    value = params.get(key)
    if not value:
        return f"Missing required parameter: {key}"

    # If already a /dev/ path, just validate it exists
    if value.startswith("/dev/"):
        if os.path.exists(value):
            return None
        return f"Device not found: {value}"

    # Resolve stable_id to device path
    device_path, error = resolve_stable_id_to_device(value)
    if error:
        return error

    # Update params with resolved path, preserve original
    params["resolved_from"] = value
    params[key] = device_path
    return None


def add_fstab_entry(device_uuid, mountpoint, fstype="ext4"):
    """
    Add a persistent mount entry to /etc/fstab using UUID.
    Uses 'nofail' option so system boots even if drive is missing.

    Returns:
        dict: {'success': bool, 'message': str, 'error': str}
    """
    if not device_uuid:
        return {"success": False, "error": "No UUID provided"}

    fstab_path = "/etc/fstab"
    fstab_entry = f"UUID={device_uuid} {mountpoint} {fstype} defaults,nofail 0 2"

    try:
        # Read current fstab
        with open(fstab_path, "r") as f:
            fstab_content = f.read()

        # Check if entry already exists (by UUID or mountpoint)
        for line in fstab_content.split("\n"):
            if line.strip().startswith("#") or not line.strip():
                continue
            if device_uuid in line or f" {mountpoint} " in line:
                return {"success": True, "message": "Entry already exists in fstab"}

        # Add the entry
        if not fstab_content.endswith("\n"):
            fstab_content += "\n"
        fstab_content += f"{fstab_entry}\n"

        # Write back (need sudo)
        result = run_command_full(["sudo", "-n", "tee", fstab_path], timeout=10)
        # Use a different approach - write to temp and move
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".fstab"
        ) as tmp:
            tmp.write(fstab_content)
            tmp_path = tmp.name

        # Copy with sudo
        result = run_command_full(
            ["sudo", "-n", "cp", tmp_path, fstab_path], timeout=10
        )
        os.unlink(tmp_path)

        if result["returncode"] != 0:
            return {
                "success": False,
                "error": f'Failed to write fstab: {result["stderr"]}',
            }

        print(f"[fstab] Added entry: {fstab_entry}")
        return {"success": True, "message": f"Added fstab entry for UUID={device_uuid}"}

    except Exception as e:
        return {"success": False, "error": str(e)}


def remove_fstab_entry(device=None, mountpoint=None, device_uuid=None):
    """
    Remove a mount entry from /etc/fstab by device, mountpoint, or UUID.

    Returns:
        dict: {'success': bool, 'message': str, 'error': str}
    """
    if not any([device, mountpoint, device_uuid]):
        return {"success": False, "error": "Must provide device, mountpoint, or UUID"}

    # If device provided but no UUID, look it up
    if device and not device_uuid:
        device_uuid = get_device_uuid(device)

    fstab_path = "/etc/fstab"

    try:
        with open(fstab_path, "r") as f:
            lines = f.readlines()

        new_lines = []
        removed = False

        for line in lines:
            stripped = line.strip()
            # Skip comments and empty lines - keep them
            if stripped.startswith("#") or not stripped:
                new_lines.append(line)
                continue

            # Check if this line should be removed
            should_remove = False
            if device_uuid and f"UUID={device_uuid}" in line:
                should_remove = True
            elif mountpoint and f" {mountpoint} " in line:
                should_remove = True
            elif device and line.startswith(device):
                should_remove = True

            if should_remove:
                print(f"[fstab] Removing entry: {stripped}")
                removed = True
            else:
                new_lines.append(line)

        if not removed:
            return {"success": True, "message": "No matching fstab entry found"}

        # Write back
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".fstab"
        ) as tmp:
            tmp.writelines(new_lines)
            tmp_path = tmp.name

        result = run_command_full(
            ["sudo", "-n", "cp", tmp_path, fstab_path], timeout=10
        )
        os.unlink(tmp_path)

        if result["returncode"] != 0:
            return {
                "success": False,
                "error": f'Failed to write fstab: {result["stderr"]}',
            }

        return {"success": True, "message": "Removed fstab entry"}

    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_disk_mount(params):
    """Mount a disk device"""
    # Resolve stable_id to device path if needed
    error = resolve_device_param(params, "device")
    if error:
        return {"success": False, "error": error}

    device = params["device"]
    mountpoint = params["mountpoint"]
    fstype = params.get("fstype")
    options = params.get("options")

    # Create mountpoint
    run_command_full(["sudo", "-n", "mkdir", "-p", mountpoint], timeout=10)

    # Build mount command
    cmd = ["sudo", "-n", "mount"]
    if fstype:
        cmd.extend(["-t", fstype])
    if options:
        cmd.extend(["-o", options])
    cmd.extend([device, mountpoint])

    result = run_command_full(cmd, timeout=30)
    if result["returncode"] != 0:
        return {"success": False, "error": result["stderr"] or "mount failed"}

    # Add fstab entry for persistence (using UUID to avoid device letter shifting)
    persist = params.get("persist", True)  # Default to persisting mounts
    fstab_message = ""
    if persist:
        device_uuid = get_device_uuid(device)
        if device_uuid:
            detected_fstype = fstype or get_device_fstype(device) or "ext4"
            fstab_result = add_fstab_entry(device_uuid, mountpoint, detected_fstype)
            if fstab_result["success"]:
                fstab_message = f" (persisted to fstab with UUID={device_uuid})"
            else:
                fstab_message = f' (warning: fstab entry failed: {fstab_result.get("error", "unknown")})'
        else:
            fstab_message = " (warning: could not get UUID for fstab persistence)"

    return {
        "success": True,
        "message": f"Mounted {device} at {mountpoint}{fstab_message}",
    }


def handle_disk_unmount(params):
    """Unmount a disk device"""
    # Resolve stable_id to device path if needed
    error = resolve_device_param(params, "device")
    if error:
        return {"success": False, "error": error}

    device = params["device"]
    force = params.get("force", False)
    remove_persist = params.get(
        "remove_persist", True
    )  # Default to removing fstab entry

    # Get UUID before unmounting (needed for fstab removal)
    device_uuid = get_device_uuid(device) if remove_persist else None

    # Get mountpoint before unmounting (for fstab removal fallback)
    mountpoint = None
    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == device:
                    mountpoint = parts[1]
                    break
    except Exception:
        pass

    cmd = ["sudo", "-n", "umount"]
    if force:
        cmd.append("-f")
    cmd.append(device)

    result = run_command_full(cmd, timeout=30)
    if result["returncode"] != 0:
        return {"success": False, "error": result["stderr"] or "umount failed"}

    # Remove fstab entry
    fstab_message = ""
    if remove_persist:
        fstab_result = remove_fstab_entry(
            device=device, mountpoint=mountpoint, device_uuid=device_uuid
        )
        if fstab_result["success"]:
            if "No matching" not in fstab_result.get("message", ""):
                fstab_message = " (removed from fstab)"
        else:
            fstab_message = f' (warning: fstab removal failed: {fstab_result.get("error", "unknown")})'

    return {"success": True, "message": f"Unmounted {device}{fstab_message}"}


def handle_fix_stale_mount(params):
    """Fix a stale mount by lazy-unmounting and remounting with the correct device.

    This is used when a device path changes (e.g., after LV rename) but the mount
    is still pointing to the old path that no longer exists.
    """
    mountpoint = params["mountpoint"]
    new_device = params["new_device"]

    # Check if mountpoint is currently mounted
    mount_check = run_command_full(
        ["findmnt", "-n", "-o", "SOURCE", mountpoint], timeout=10
    )
    if mount_check["returncode"] != 0:
        return {"success": False, "error": f"{mountpoint} is not mounted"}

    old_source = mount_check["stdout"].strip()
    print(f"Fixing stale mount at {mountpoint}: {old_source} -> {new_device}")

    # Get current mount options
    opts_check = run_command_full(
        ["findmnt", "-n", "-o", "OPTIONS", mountpoint], timeout=10
    )
    mount_options = (
        opts_check["stdout"].strip() if opts_check["returncode"] == 0 else "defaults"
    )

    # Get filesystem type
    fstype_check = run_command_full(
        ["findmnt", "-n", "-o", "FSTYPE", mountpoint], timeout=10
    )
    fstype = (
        fstype_check["stdout"].strip() if fstype_check["returncode"] == 0 else "auto"
    )

    # Verify the new device exists
    if not os.path.exists(new_device):
        return {"success": False, "error": f"New device {new_device} does not exist"}

    # Stop any containers using this mountpoint
    containers_stopped = []
    try:
        containers = get_containers_using_path(mountpoint)
        if containers:
            print(f"Stopping {len(containers)} containers using {mountpoint}")
            stop_result = stop_containers(containers, timeout=120)
            containers_stopped = stop_result.get("stopped", [])
    except Exception as e:
        print(f"Container stop check failed: {e}")

    # Lazy unmount the stale mount
    unmount_result = run_command_full(
        ["sudo", "-n", "umount", "-l", mountpoint], timeout=30
    )
    if unmount_result["returncode"] != 0:
        # Try force unmount
        unmount_result = run_command_full(
            ["sudo", "-n", "umount", "-f", mountpoint], timeout=30
        )
        if unmount_result["returncode"] != 0:
            return {
                "success": False,
                "error": f'Cannot unmount {mountpoint}: {unmount_result["stderr"]}',
            }

    # Ensure mount point exists
    run_command_full(["sudo", "-n", "mkdir", "-p", mountpoint], timeout=10)

    # Mount with new device
    mount_cmd = [
        "sudo",
        "-n",
        "mount",
        "-t",
        fstype,
        "-o",
        mount_options,
        new_device,
        mountpoint,
    ]
    mount_result = run_command_full(mount_cmd, timeout=60)

    if mount_result["returncode"] != 0:
        return {
            "success": False,
            "error": f'Unmounted but remount failed: {mount_result["stderr"]}',
            "containers_stopped": containers_stopped,
        }

    # Update fstab to use new device path
    fstab_updated = False
    try:
        with open("/etc/fstab", "r") as f:
            fstab_content = f.read()

        if old_source in fstab_content or mountpoint in fstab_content:
            new_fstab = fstab_content.replace(old_source, new_device)
            # Backup and write
            run_command_full(
                ["sudo", "-n", "cp", "/etc/fstab", "/etc/fstab.bak"], timeout=10
            )
            with open("/tmp/fstab.new", "w") as f:
                f.write(new_fstab)
            run_command_full(
                ["sudo", "-n", "cp", "/tmp/fstab.new", "/etc/fstab"], timeout=10
            )
            fstab_updated = True
    except Exception as e:
        print(f"fstab update failed: {e}")

    # Restart containers that were stopped
    containers_restarted = []
    if containers_stopped:
        try:
            start_result = start_containers(containers_stopped, timeout=60)
            containers_restarted = start_result.get("started", [])
        except Exception as e:
            print(f"Container restart failed: {e}")

    return {
        "success": True,
        "message": f"Fixed mount {mountpoint}: {old_source} -> {new_device}",
        "fstab_updated": fstab_updated,
        "containers_stopped": containers_stopped,
        "containers_restarted": containers_restarted,
    }


def restore_managed_mounts():
    """
    Restore managed mounts from storage_mounts database table on startup.

    Queries the database for all managed mounts and attempts to remount
    any that aren't currently mounted. This ensures drives persist across reboots.

    Returns:
        dict: Summary of mount restoration results
    """
    results = {
        "attempted": 0,
        "mounted": 0,
        "already_mounted": 0,
        "failed": 0,
        "skipped": 0,
        "details": [],
    }

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Get all managed mounts (excluding root filesystem)
        cur.execute("""
            SELECT device, mount_point, filesystem, label
            FROM storage_mounts
            WHERE is_managed = true AND mount_point != '/'
            ORDER BY mount_point
        """)

        managed_mounts = cur.fetchall()
        cur.close()
        conn.close()

        if not managed_mounts:
            print("[Mount Restore] No managed mounts found in database")
            return results

        print(f"[Mount Restore] Found {len(managed_mounts)} managed mount(s) to check")

        # Get currently mounted filesystems
        current_mounts = set()
        try:
            with open("/proc/mounts", "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        current_mounts.add(parts[1])  # mount point
        except Exception as e:
            print(f"[Mount Restore] Warning: Could not read /proc/mounts: {e}")

        for device, mount_point, filesystem, label in managed_mounts:
            mount_info = {
                "device": device,
                "mount_point": mount_point,
                "status": None,
                "message": None,
            }

            # Check if already mounted
            if mount_point in current_mounts:
                mount_info["status"] = "already_mounted"
                mount_info["message"] = "Already mounted"
                results["already_mounted"] += 1
                results["details"].append(mount_info)
                print(f"[Mount Restore] {device} -> {mount_point}: Already mounted")
                continue

            # Check if device exists
            device_exists = False

            # Handle LVM paths
            if device.startswith("/dev/mapper/"):
                device_exists = os.path.exists(device)
            elif device.startswith("/dev/"):
                device_exists = os.path.exists(device)
                # Also check by UUID if we have blkid info
                if not device_exists:
                    # Try to find device by checking lsblk
                    lsblk_out = run_command(["lsblk", "-o", "NAME,PATH", "-n", "-l"])
                    if lsblk_out:
                        for line in lsblk_out.split("\n"):
                            if device in line:
                                device_exists = True
                                break

            if not device_exists:
                mount_info["status"] = "skipped"
                mount_info["message"] = "Device not found (may have been removed)"
                results["skipped"] += 1
                results["details"].append(mount_info)
                print(
                    f"[Mount Restore] {device} -> {mount_point}: Skipped (device not found)"
                )
                continue

            # Attempt to mount
            results["attempted"] += 1
            print(f"[Mount Restore] Attempting to mount {device} -> {mount_point}")

            try:
                mount_result = handle_disk_mount(
                    {"device": device, "mountpoint": mount_point, "fstype": filesystem}
                )

                if mount_result.get("success"):
                    mount_info["status"] = "mounted"
                    mount_info["message"] = mount_result.get(
                        "message", "Mounted successfully"
                    )
                    results["mounted"] += 1
                    print(f"[Mount Restore] {device} -> {mount_point}: SUCCESS")
                else:
                    mount_info["status"] = "failed"
                    mount_info["message"] = mount_result.get("error", "Unknown error")
                    results["failed"] += 1
                    print(
                        f"[Mount Restore] {device} -> {mount_point}: FAILED - {mount_info['message']}"
                    )
            except Exception as e:
                mount_info["status"] = "failed"
                mount_info["message"] = str(e)
                results["failed"] += 1
                print(f"[Mount Restore] {device} -> {mount_point}: ERROR - {e}")

            results["details"].append(mount_info)

        # Summary
        print(
            f"[Mount Restore] Complete: {results['mounted']} mounted, {results['already_mounted']} already mounted, {results['failed']} failed, {results['skipped']} skipped"
        )

    except ImportError:
        print("[Mount Restore] psycopg2 not available, skipping mount restoration")
    except Exception as e:
        print(f"[Mount Restore] Error restoring mounts: {e}")
        results["error"] = str(e)

    return results


def handle_disk_format(params):
    """Format a disk device (DANGEROUS)"""
    # Resolve stable_id to device path if needed
    error = resolve_device_param(params, "device")
    if error:
        return {"success": False, "error": error}

    device = params["device"]
    fstype = params["fstype"]
    label = params.get("label")

    # Verify not currently mounted
    mount_check = run_command_full(
        ["findmnt", "-n", "-o", "TARGET", device], timeout=10
    )
    if mount_check["returncode"] == 0 and mount_check["stdout"].strip():
        return {
            "success": False,
            "error": f'Device {device} is currently mounted at {mount_check["stdout"].strip()}. Unmount first.',
        }

    cmd = ["sudo", "-n", f"mkfs.{fstype}"]
    if label:
        if fstype in ("ext4", "ext3", "ext2"):
            cmd.extend(["-L", label])
        elif fstype == "xfs":
            cmd.extend(["-L", label])
        elif fstype == "vfat":
            cmd.extend(["-n", label])
    cmd.append(device)

    result = run_command_full(cmd, timeout=120)
    if result["returncode"] != 0:
        return {"success": False, "error": result["stderr"] or "mkfs failed"}

    return {"success": True, "message": f"Formatted {device} as {fstype}"}


def handle_disk_wipe(params):
    """Wipe a disk - remove LVM structures, unmount, and optionally wipe partition table (DANGEROUS)"""
    # Resolve stable_id to device path if needed
    error = resolve_device_param(params, "device")
    if error:
        return {"success": False, "error": error}

    device = params["device"]
    wipe_lvm = params.get("wipe_lvm", True)
    wipe_partition_table = params.get("wipe_partition_table", False)

    # Protect boot drive
    boot_device = run_command(["findmnt", "-n", "-o", "SOURCE", "/"], timeout=10)
    if boot_device:
        boot_disk = boot_device.strip().replace("/dev/", "").rstrip("0123456789")
        if boot_disk and boot_disk in device:
            return {"success": False, "error": f"Cannot wipe boot device {device}"}

    messages = []

    # Get device info
    is_partition = any(c.isdigit() for c in device.split("/")[-1])
    base_device = device.rstrip("0123456789") if is_partition else device

    # Step 1: Find and unmount any mounts on this device or its children
    lsblk_out = run_command(
        ["lsblk", "-J", "-o", "NAME,MOUNTPOINT,TYPE", device], timeout=30
    )
    if lsblk_out:
        try:
            import json

            lsblk_data = json.loads(lsblk_out)

            def find_mounts(devices):
                mounts = []
                for dev in devices:
                    if dev.get("mountpoint"):
                        mounts.append(dev["mountpoint"])
                    if "children" in dev:
                        mounts.extend(find_mounts(dev["children"]))
                return mounts

            mounts = find_mounts(lsblk_data.get("blockdevices", []))
            for mount in mounts:
                umount_result = run_command_full(
                    ["sudo", "-n", "umount", mount], timeout=30
                )
                if umount_result["returncode"] == 0:
                    messages.append(f"Unmounted {mount}")
                else:
                    # Try lazy unmount
                    run_command_full(["sudo", "-n", "umount", "-l", mount], timeout=30)
                    messages.append(f"Lazy unmounted {mount}")
        except Exception as e:
            messages.append(f"Warning: Error parsing mounts: {e}")

    # Step 2: Remove LVM structures if requested
    if wipe_lvm:
        # Find VGs that use this device as PV
        pvs_out = run_command(
            ["sudo", "-n", "pvs", "--noheadings", "-o", "pv_name,vg_name"], timeout=30
        )
        if pvs_out:
            for line in pvs_out.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 2:
                    pv_name, vg_name = parts[0], parts[1]
                    # Check if this PV is on our device
                    if device in pv_name or (is_partition and base_device in pv_name):
                        # Get LVs in this VG
                        lvs_out = run_command(
                            [
                                "sudo",
                                "-n",
                                "lvs",
                                "--noheadings",
                                "-o",
                                "lv_name",
                                vg_name,
                            ],
                            timeout=30,
                        )
                        if lvs_out:
                            for lv_line in lvs_out.strip().split("\n"):
                                lv_name = lv_line.strip()
                                if lv_name:
                                    lv_path = f"/dev/{vg_name}/{lv_name}"
                                    # Deactivate LV
                                    run_command_full(
                                        ["sudo", "-n", "lvchange", "-an", lv_path],
                                        timeout=30,
                                    )
                                    # Remove LV
                                    result = run_command_full(
                                        ["sudo", "-n", "lvremove", "-f", lv_path],
                                        timeout=60,
                                    )
                                    if result["returncode"] == 0:
                                        messages.append(f"Removed LV {lv_path}")

                        # Remove VG
                        result = run_command_full(
                            ["sudo", "-n", "vgremove", "-f", vg_name], timeout=60
                        )
                        if result["returncode"] == 0:
                            messages.append(f"Removed VG {vg_name}")

                        # Remove PV
                        result = run_command_full(
                            ["sudo", "-n", "pvremove", "-f", pv_name], timeout=60
                        )
                        if result["returncode"] == 0:
                            messages.append(f"Removed PV {pv_name}")

    # Step 3: Wipe partition table if requested (for whole disks only)
    if wipe_partition_table and not is_partition:
        # Use wipefs to remove filesystem signatures
        result = run_command_full(["sudo", "-n", "wipefs", "-a", device], timeout=60)
        if result["returncode"] == 0:
            messages.append(f"Wiped filesystem signatures from {device}")

        # Zero out partition table area
        result = run_command_full(
            [
                "sudo",
                "-n",
                "dd",
                "if=/dev/zero",
                f"of={device}",
                "bs=512",
                "count=2048",
            ],
            timeout=60,
        )
        if result["returncode"] == 0:
            messages.append(f"Zeroed partition table on {device}")

        # Inform kernel of partition changes
        run_command_full(["sudo", "-n", "partprobe", device], timeout=30)

    if not messages:
        return {"success": True, "message": f"No operations performed on {device}"}

    return {"success": True, "message": "\n".join(messages)}


def handle_partition_create(params):
    """Create a new partition on a device"""
    # Resolve stable_id to device path if needed
    error = resolve_device_param(params, "device")
    if error:
        return {"success": False, "error": error}

    device = params["device"]
    size_gb = params["size_gb"]
    part_type = params.get("part_type", "primary")

    # Check if device has a partition table
    check_label = run_command_full(
        ["sudo", "-n", "parted", "-s", device, "print"], timeout=30
    )
    if "unrecognised disk label" in check_label.get("stderr", ""):
        # No partition table - create GPT by default
        create_label = run_command_full(
            ["sudo", "-n", "parted", "-s", device, "mklabel", "gpt"], timeout=30
        )
        if create_label["returncode"] != 0:
            return {
                "success": False,
                "error": f'Failed to create partition table: {create_label["stderr"]}',
            }

    # Use parted to create partition
    result = run_command_full(
        [
            "sudo",
            "-n",
            "parted",
            "-s",
            device,
            "mkpart",
            part_type,
            "0%",
            f"{size_gb}GiB",
        ],
        timeout=60,
    )

    if result["returncode"] != 0:
        return {"success": False, "error": result["stderr"] or "parted mkpart failed"}

    return {
        "success": True,
        "message": f"Created {part_type} partition ({size_gb}GB) on {device}",
    }


def handle_pv_create_vg_extend(params):
    """Create PV and extend VG"""
    # Resolve stable_id to device path if needed
    error = resolve_device_param(params, "device")
    if error:
        return {"success": False, "error": error}

    device = params["device"]
    vg_name = params["vg_name"]

    # pvcreate
    pv_result = run_command_full(["sudo", "-n", "pvcreate", device], timeout=60)
    if pv_result["returncode"] != 0:
        return {"success": False, "error": f'pvcreate failed: {pv_result["stderr"]}'}

    # vgextend
    vg_result = run_command_full(
        ["sudo", "-n", "vgextend", vg_name, device], timeout=60
    )
    if vg_result["returncode"] != 0:
        return {"success": False, "error": f'vgextend failed: {vg_result["stderr"]}'}

    return {
        "success": True,
        "message": f"Added {device} as PV and extended VG {vg_name}",
    }


def handle_disk_prepare(params):
    """Prepare an entire drive: create partition table, single partition, and format (DANGEROUS)"""
    # Resolve stable_id to device path if needed
    error = resolve_device_param(params, "device")
    if error:
        return {"success": False, "error": error}

    device = params["device"]
    fstype = params["fstype"]
    partition_table = params.get("partition_table", "gpt")
    label = params.get("label")

    # Safety: must be a whole disk (e.g., /dev/sdb, not /dev/sdb1)
    import re

    if re.search(r"\d+$", device):
        return {
            "success": False,
            "error": f"{device} appears to be a partition, not a whole disk",
        }

    # Verify nothing is mounted from this disk
    mount_check = run_command_full(
        ["lsblk", "-n", "-o", "MOUNTPOINT", device], timeout=10
    )
    if mount_check["returncode"] == 0:
        mounts = [m.strip() for m in mount_check["stdout"].split("\n") if m.strip()]
        if mounts:
            return {
                "success": False,
                "error": f'Device {device} has mounted partitions: {", ".join(mounts)}. Unmount first.',
            }

    # Step 1: Create new partition table (wipes existing partitions)
    parted_label = run_command_full(
        ["sudo", "-n", "parted", "-s", device, "mklabel", partition_table], timeout=30
    )
    if parted_label["returncode"] != 0:
        return {
            "success": False,
            "error": f'Failed to create partition table: {parted_label["stderr"]}',
        }

    # Step 2: Create single partition using 100% of disk
    parted_mkpart = run_command_full(
        ["sudo", "-n", "parted", "-s", device, "mkpart", "primary", "0%", "100%"],
        timeout=30,
    )
    if parted_mkpart["returncode"] != 0:
        return {
            "success": False,
            "error": f'Failed to create partition: {parted_mkpart["stderr"]}',
        }

    # Wait for partition to appear
    import time

    time.sleep(1)
    run_command_full(["sudo", "-n", "partprobe", device], timeout=10)
    time.sleep(1)

    # Step 3: Format the new partition (device + "1" for first partition)
    new_partition = f"{device}1"

    # Verify partition exists
    if not os.path.exists(new_partition):
        return {
            "success": False,
            "error": f"Partition {new_partition} did not appear after creation",
        }

    cmd = ["sudo", "-n", f"mkfs.{fstype}"]
    if label:
        if fstype in ("ext4", "ext3", "ext2", "xfs", "btrfs"):
            cmd.extend(["-L", label])
        elif fstype == "vfat":
            cmd.extend(["-n", label])
    cmd.append(new_partition)

    format_result = run_command_full(
        cmd, timeout=300
    )  # Formatting can take time on large drives
    if format_result["returncode"] != 0:
        return {"success": False, "error": f'Format failed: {format_result["stderr"]}'}

    return {
        "success": True,
        "message": f"Prepared {device}: {partition_table} partition table, single partition {new_partition} formatted as {fstype}",
        "partition": new_partition,
        "filesystem": fstype,
    }


def handle_disk_prepare_lvm(params):
    """Prepare an entire drive as LVM: partition table, partition, PV, VG, LV, format, mount (DANGEROUS)"""
    # Resolve stable_id to device path if needed
    error = resolve_device_param(params, "device")
    if error:
        return {"success": False, "error": error}

    device = params["device"]
    vg_name = params["vg_name"]
    lv_name = params["lv_name"]
    fstype = params.get("fstype", "ext4")
    mountpoint = params.get("mountpoint")

    # Safety: must be a whole disk (e.g., /dev/sdb, not /dev/sdb1)
    if re.search(r"\d+$", device):
        return {
            "success": False,
            "error": f"{device} appears to be a partition, not a whole disk",
        }

    # Check if VG name already exists
    vg_check = run_command_full(["sudo", "-n", "vgs", vg_name], timeout=10)
    if vg_check["returncode"] == 0:
        return {
            "success": False,
            "error": f'Volume group "{vg_name}" already exists. Choose a different name.',
        }

    # Unmount any mounted partitions from this disk
    mount_check = run_command_full(
        ["lsblk", "-n", "-o", "NAME,MOUNTPOINT", device], timeout=10
    )
    if mount_check["returncode"] == 0:
        for line in mount_check["stdout"].split("\n"):
            parts = line.split()
            if len(parts) >= 2 and parts[1]:
                mp = parts[1]
                part_name = parts[0].lstrip("└─├─")
                part_device = f"/dev/{part_name}"
                print(f"[prepare_lvm] Unmounting {part_device} from {mp}")
                umount_result = run_command_full(
                    ["sudo", "-n", "umount", part_device], timeout=30
                )
                if umount_result["returncode"] != 0:
                    return {
                        "success": False,
                        "error": f'Failed to unmount {part_device}: {umount_result["stderr"]}',
                    }

    # Step 1: Wipe any existing signatures
    wipefs = run_command_full(["sudo", "-n", "wipefs", "-a", device], timeout=30)
    # Ignore wipefs errors - drive might be empty

    # Step 2: Create new GPT partition table
    parted_label = run_command_full(
        ["sudo", "-n", "parted", "-s", device, "mklabel", "gpt"], timeout=30
    )
    if parted_label["returncode"] != 0:
        return {
            "success": False,
            "error": f'Failed to create partition table: {parted_label["stderr"]}',
        }

    # Step 3: Create single partition using 100% of disk
    parted_mkpart = run_command_full(
        ["sudo", "-n", "parted", "-s", device, "mkpart", "primary", "0%", "100%"],
        timeout=30,
    )
    if parted_mkpart["returncode"] != 0:
        return {
            "success": False,
            "error": f'Failed to create partition: {parted_mkpart["stderr"]}',
        }

    # Wait for partition to appear
    time.sleep(1)
    run_command_full(["sudo", "-n", "partprobe", device], timeout=10)
    time.sleep(1)

    new_partition = f"{device}1"
    if not os.path.exists(new_partition):
        return {
            "success": False,
            "error": f"Partition {new_partition} did not appear after creation",
        }

    # Step 4: Create Physical Volume
    pvcreate = run_command_full(
        ["sudo", "-n", "pvcreate", "-f", new_partition], timeout=60
    )
    if pvcreate["returncode"] != 0:
        return {"success": False, "error": f'pvcreate failed: {pvcreate["stderr"]}'}

    # Step 5: Create Volume Group
    vgcreate = run_command_full(
        ["sudo", "-n", "vgcreate", vg_name, new_partition], timeout=60
    )
    if vgcreate["returncode"] != 0:
        run_command_full(["sudo", "-n", "pvremove", "-f", new_partition], timeout=30)
        return {"success": False, "error": f'vgcreate failed: {vgcreate["stderr"]}'}

    # Step 6: Create Logical Volume using 100% of space
    lvcreate = run_command_full(
        ["sudo", "-n", "lvcreate", "-l", "100%FREE", "-n", lv_name, vg_name], timeout=60
    )
    if lvcreate["returncode"] != 0:
        run_command_full(["sudo", "-n", "vgremove", "-f", vg_name], timeout=30)
        run_command_full(["sudo", "-n", "pvremove", "-f", new_partition], timeout=30)
        return {"success": False, "error": f'lvcreate failed: {lvcreate["stderr"]}'}

    lv_path = f"/dev/{vg_name}/{lv_name}"

    # Step 7: Format the LV
    mkfs = run_command_full(["sudo", "-n", f"mkfs.{fstype}", lv_path], timeout=300)
    if mkfs["returncode"] != 0:
        return {
            "success": False,
            "error": f'LVM created but format failed: {mkfs["stderr"]}. LV exists at {lv_path}',
        }

    # Step 8: Mount if mountpoint provided
    mounted = False
    if mountpoint:
        run_command_full(["sudo", "-n", "mkdir", "-p", mountpoint], timeout=10)
        mount_result = run_command_full(
            ["sudo", "-n", "mount", lv_path, mountpoint], timeout=30
        )
        if mount_result["returncode"] == 0:
            mounted = True
            # Add to fstab
            lv_uuid = get_device_uuid(lv_path)
            if lv_uuid:
                add_fstab_entry(lv_uuid, mountpoint, fstype)

    return {
        "success": True,
        "message": f"Prepared {device} as LVM: VG={vg_name}, LV={lv_name}, formatted as {fstype}"
        + (f", mounted at {mountpoint}" if mounted else ""),
        "lv_path": lv_path,
        "vg_name": vg_name,
        "lv_name": lv_name,
        "partition": new_partition,
        "mounted": mounted,
        "mountpoint": mountpoint if mounted else None,
    }


def handle_disk_label(params):
    """Change the label of a filesystem"""
    device = params["device"]
    label = params["label"]
    fstype = params["fstype"]

    # Choose the right tool based on filesystem
    if fstype in ("ext4", "ext3", "ext2"):
        cmd = ["sudo", "-n", "e2label", device, label]
    elif fstype == "xfs":
        cmd = ["sudo", "-n", "xfs_admin", "-L", label, device]
    elif fstype == "btrfs":
        cmd = ["sudo", "-n", "btrfs", "filesystem", "label", device, label]
    elif fstype == "vfat":
        cmd = ["sudo", "-n", "fatlabel", device, label]
    else:
        return {"success": False, "error": f"Label change not supported for {fstype}"}

    result = run_command_full(cmd, timeout=30)
    if result["returncode"] != 0:
        return {
            "success": False,
            "error": f'Failed to change label: {result["stderr"]}',
        }

    return {"success": True, "message": f'Changed label of {device} to "{label}"'}


def handle_convert_to_lvm(params):
    """Convert a regular partition to LVM (DESTRUCTIVE - erases all data)"""
    # Resolve stable_id to device path if needed
    error = resolve_device_param(params, "device")
    if error:
        return {"success": False, "error": error}

    device = params["device"]
    vg_name = params["vg_name"]
    lv_name = params["lv_name"]
    fstype = params["fstype"]

    # Get current mountpoint before we unmount
    mount_check = run_command_full(
        ["findmnt", "-n", "-o", "TARGET", device], timeout=10
    )
    original_mountpoint = (
        mount_check["stdout"].strip() if mount_check["returncode"] == 0 else None
    )

    # Step 1: Unmount if mounted
    if original_mountpoint:
        umount = run_command_full(["sudo", "-n", "umount", device], timeout=30)
        if umount["returncode"] != 0:
            return {
                "success": False,
                "error": f'Failed to unmount {device}: {umount["stderr"]}',
            }

    # Step 2: Wipe filesystem signature
    wipefs = run_command_full(["sudo", "-n", "wipefs", "-a", device], timeout=30)
    if wipefs["returncode"] != 0:
        return {
            "success": False,
            "error": f'Failed to wipe signatures: {wipefs["stderr"]}',
        }

    # Step 3: Create Physical Volume
    pvcreate = run_command_full(["sudo", "-n", "pvcreate", "-f", device], timeout=60)
    if pvcreate["returncode"] != 0:
        return {"success": False, "error": f'pvcreate failed: {pvcreate["stderr"]}'}

    # Step 4: Create Volume Group
    vgcreate = run_command_full(["sudo", "-n", "vgcreate", vg_name, device], timeout=60)
    if vgcreate["returncode"] != 0:
        # Cleanup PV on failure
        run_command_full(["sudo", "-n", "pvremove", "-f", device], timeout=30)
        return {"success": False, "error": f'vgcreate failed: {vgcreate["stderr"]}'}

    # Step 5: Create Logical Volume using 100% of space
    lvcreate = run_command_full(
        ["sudo", "-n", "lvcreate", "-l", "100%FREE", "-n", lv_name, vg_name], timeout=60
    )
    if lvcreate["returncode"] != 0:
        # Cleanup VG and PV on failure
        run_command_full(["sudo", "-n", "vgremove", "-f", vg_name], timeout=30)
        run_command_full(["sudo", "-n", "pvremove", "-f", device], timeout=30)
        return {"success": False, "error": f'lvcreate failed: {lvcreate["stderr"]}'}

    lv_path = f"/dev/{vg_name}/{lv_name}"

    # Step 6: Format the LV
    mkfs = run_command_full(["sudo", "-n", f"mkfs.{fstype}", lv_path], timeout=300)
    if mkfs["returncode"] != 0:
        return {
            "success": False,
            "error": f'LVM created but format failed: {mkfs["stderr"]}. LV exists at {lv_path}',
        }

    # Step 7: Remount at original location if it was mounted
    if original_mountpoint:
        # Update fstab to use new LV path
        try:
            with open("/etc/fstab", "r") as f:
                fstab = f.read()
            if device in fstab:
                new_fstab = fstab.replace(device, lv_path)
                run_command_full(
                    ["sudo", "-n", "cp", "/etc/fstab", "/etc/fstab.bak"], timeout=10
                )
                with open("/tmp/fstab.new", "w") as f:
                    f.write(new_fstab)
                run_command_full(
                    ["sudo", "-n", "cp", "/tmp/fstab.new", "/etc/fstab"], timeout=10
                )
        except Exception:
            pass  # fstab update is best-effort

        mount = run_command_full(
            ["sudo", "-n", "mount", lv_path, original_mountpoint], timeout=30
        )
        if mount["returncode"] != 0:
            return {
                "success": True,
                "message": f"Converted to LVM: {lv_path}. Mount failed - manually mount to {original_mountpoint}",
                "lv_path": lv_path,
                "mounted": False,
            }

    return {
        "success": True,
        "message": f"Converted {device} to LVM: VG={vg_name}, LV={lv_name}, formatted as {fstype}",
        "lv_path": lv_path,
        "vg_name": vg_name,
        "lv_name": lv_name,
        "mounted": original_mountpoint is not None,
    }


# ============================================================================
# FIREWALL (UFW) HANDLERS
# ============================================================================


def handle_firewall_status(params):
    """Get UFW firewall status"""
    result = run_command_full(["sudo", "-n", "ufw", "status", "verbose"], timeout=10)
    if result["returncode"] != 0:
        return {
            "success": False,
            "error": result["stderr"] or "Failed to get UFW status",
        }

    output = result["stdout"]
    # Parse status line to determine if active
    active = "Status: active" in output

    return {"success": True, "active": active, "output": output}


def handle_firewall_rules(params):
    """Get numbered list of UFW firewall rules - works even when inactive"""
    # First try ufw status numbered (works when active)
    result = run_command_full(["sudo", "-n", "ufw", "status", "numbered"], timeout=10)
    if result["returncode"] != 0:
        return {
            "success": False,
            "error": result["stderr"] or "Failed to get UFW rules",
        }

    # Check if inactive - if so, parse config file directly
    if "Status: inactive" in result["stdout"]:
        rules = _parse_ufw_config_rules()
        return {
            "success": True,
            "output": result["stdout"],
            "rules": rules,
            "from_config": True,
        }

    return {"success": True, "output": result["stdout"], "from_config": False}


def _parse_ufw_config_rules():
    """Parse UFW rules from config file (for when UFW is inactive)"""
    rules = []
    rule_num = 1

    # Parse IPv4 rules
    try:
        with open("/etc/ufw/user.rules", "r") as f:
            for line in f:
                if line.startswith("### tuple ###"):
                    rule = _parse_ufw_tuple_line(line, rule_num)
                    if rule:
                        rules.append(rule)
                        rule_num += 1
    except (IOError, PermissionError):
        pass

    return rules


def _parse_ufw_tuple_line(line, rule_num):
    """Parse a UFW tuple line from config file.
    Format: ### tuple ### allow tcp 22 0.0.0.0/0 any 0.0.0.0/0 in comment=...
    """
    import re

    # Pattern: action protocol port dest_addr dest_port src_addr direction
    match = re.match(
        r"### tuple ### (allow|deny|reject|limit) (tcp|udp) (\d+) ([\d./]+) (\w+) ([\d./]+) (in|out)",
        line,
    )
    if match:
        action = match.group(1).upper()
        protocol = match.group(2)
        port = match.group(3)
        direction = match.group(7).upper()
        src = match.group(6)

        # Extract comment if present
        comment_match = re.search(r"comment=([a-fA-F0-9]+)", line)
        comment = ""
        if comment_match:
            try:
                comment = bytes.fromhex(comment_match.group(1)).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                pass

        return {
            "number": rule_num,
            "port": f"{port}/{protocol}",
            "action": action,
            "direction": direction,
            "from": "Anywhere" if src == "0.0.0.0/0" else src,
            "comment": comment,
        }
    return None


def handle_firewall_enable(params):
    """Enable UFW firewall (DANGEROUS - may lock out if SSH not allowed)"""
    result = run_command_full(["sudo", "-n", "ufw", "--force", "enable"], timeout=30)
    if result["returncode"] != 0:
        return {"success": False, "error": result["stderr"] or "Failed to enable UFW"}

    return {"success": True, "message": "UFW firewall enabled"}


def handle_firewall_disable(params):
    """Disable UFW firewall (DANGEROUS - exposes all ports)"""
    result = run_command_full(["sudo", "-n", "ufw", "--force", "disable"], timeout=30)
    if result["returncode"] != 0:
        return {"success": False, "error": result["stderr"] or "Failed to disable UFW"}

    return {"success": True, "message": "UFW firewall disabled"}


def handle_firewall_allow(params):
    """Allow a port through the firewall"""
    port = params["port"]
    protocol = params["protocol"]

    result = run_command_full(
        ["sudo", "-n", "ufw", "allow", f"{port}/{protocol}"], timeout=30
    )
    if result["returncode"] != 0:
        return {
            "success": False,
            "error": result["stderr"] or f"Failed to allow {port}/{protocol}",
        }

    return {"success": True, "message": f"Allowed {port}/{protocol} through firewall"}


def handle_firewall_deny(params):
    """Deny a port through the firewall"""
    port = params["port"]
    protocol = params["protocol"]

    result = run_command_full(
        ["sudo", "-n", "ufw", "deny", f"{port}/{protocol}"], timeout=30
    )
    if result["returncode"] != 0:
        return {
            "success": False,
            "error": result["stderr"] or f"Failed to deny {port}/{protocol}",
        }

    return {"success": True, "message": f"Denied {port}/{protocol} through firewall"}


def handle_firewall_delete(params):
    """Delete a firewall rule by number (DANGEROUS)"""
    rule_number = params["rule_number"]

    result = run_command_full(
        ["sudo", "-n", "ufw", "--force", "delete", str(rule_number)], timeout=30
    )
    if result["returncode"] != 0:
        return {
            "success": False,
            "error": result["stderr"] or f"Failed to delete rule {rule_number}",
        }

    return {"success": True, "message": f"Deleted firewall rule #{rule_number}"}


def handle_firewall_update(params):
    """Update a firewall rule (delete old + add new)"""
    old_port = params["old_port"]
    old_protocol = params["old_protocol"]
    new_port = params["new_port"]
    new_protocol = params["new_protocol"]
    action = params["action"]

    # Delete old rule
    delete_result = run_command_full(
        [
            "sudo",
            "-n",
            "ufw",
            "--force",
            "delete",
            action,
            f"{old_port}/{old_protocol}",
        ],
        timeout=30,
    )
    # Note: delete might fail if rule doesn't exist exactly, that's ok

    # Add new rule
    add_result = run_command_full(
        ["sudo", "-n", "ufw", action, f"{new_port}/{new_protocol}"], timeout=30
    )
    if add_result["returncode"] != 0:
        return {
            "success": False,
            "error": add_result["stderr"]
            or f"Failed to add {action} {new_port}/{new_protocol}",
        }

    return {
        "success": True,
        "message": f"Updated rule: {action} {new_port}/{new_protocol}",
    }


# =========================================================================
# NGINX HANDLERS
# =========================================================================


NGINX_SITES_DIRS = [
    "/etc/nginx/sites-enabled",
    "/etc/nginx/sites-available",
    "/etc/nginx/conf.d",
]


def handle_nginx_list_configs(params):
    """List available Nginx configuration files."""
    configs = []
    for sites_dir in NGINX_SITES_DIRS:
        if not os.path.isdir(sites_dir):
            continue
        for fname in sorted(os.listdir(sites_dir)):
            fpath = os.path.join(sites_dir, fname)
            if os.path.isfile(fpath) or os.path.islink(fpath):
                is_link = os.path.islink(fpath)
                link_target = os.readlink(fpath) if is_link else None
                try:
                    size = os.path.getsize(fpath)
                except OSError:
                    size = 0
                configs.append({
                    "name": fname,
                    "directory": sites_dir,
                    "path": fpath,
                    "is_symlink": is_link,
                    "link_target": link_target,
                    "size_bytes": size,
                })
    return {"success": True, "configs": configs}


def handle_nginx_get_config(params):
    """Read content of a specific Nginx config file."""
    name = params["name"]
    # Search in known directories
    for sites_dir in NGINX_SITES_DIRS:
        fpath = os.path.join(sites_dir, name)
        if os.path.isfile(fpath):
            try:
                with open(fpath, "r") as f:
                    content = f.read()
                return {"success": True, "name": name, "path": fpath, "content": content}
            except OSError as e:
                return {"success": False, "error": f"Failed to read {fpath}: {e}"}
    return {"success": False, "error": f"Config '{name}' not found in nginx directories"}


def handle_nginx_test(params):
    """Run nginx -t to test configuration validity."""
    result = run_command_full(["sudo", "-n", "nginx", "-t"], timeout=15)
    # nginx -t writes to stderr even on success
    output = result["stderr"] or result["stdout"]
    success = result["returncode"] == 0
    return {"success": success, "output": output}


def handle_nginx_reload(params):
    """Reload Nginx to apply configuration changes."""
    # Test first
    test_result = run_command_full(["sudo", "-n", "nginx", "-t"], timeout=15)
    if test_result["returncode"] != 0:
        return {
            "success": False,
            "error": "Config test failed — not reloading",
            "test_output": test_result["stderr"] or test_result["stdout"],
        }
    # Reload
    result = run_command_full(["sudo", "-n", "systemctl", "reload", "nginx"], timeout=15)
    if result["returncode"] != 0:
        return {
            "success": False,
            "error": result["stderr"] or "Failed to reload nginx",
        }
    return {"success": True, "message": "Nginx reloaded successfully"}


# ============================================================================
# COMPLIANCE CHECK HANDLERS (read-only)
# ============================================================================

# Safe paths for compliance file reads — no /proc, /dev, or sensitive dirs
_COMPLIANCE_SAFE_PREFIXES = (
    "/etc/", "/var/log/", "/usr/", "/lib/", "/opt/",
)


def handle_compliance_read_file(params):
    """Read a file for compliance checking (read-only, path-restricted)."""
    path = params["path"]
    if not any(path.startswith(p) for p in _COMPLIANCE_SAFE_PREFIXES):
        return {"success": False, "error": f"Path not allowed: {path}"}
    if ".." in path:
        return {"success": False, "error": "Path traversal not allowed"}
    if not os.path.exists(path):
        return {"success": True, "exists": False, "content": ""}
    try:
        with open(path, "r") as f:
            content = f.read(64 * 1024)  # Max 64KB
        return {"success": True, "exists": True, "content": content}
    except PermissionError:
        return {"success": False, "error": f"Permission denied: {path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_compliance_check_sysctl(params):
    """Read a kernel sysctl parameter."""
    param = params["param"]
    result = run_command_full(["sysctl", "-n", param], timeout=10)
    if result["returncode"] != 0:
        return {"success": False, "error": result["stderr"] or f"sysctl {param} not found"}
    return {"success": True, "param": param, "value": result["stdout"].strip()}


def handle_compliance_run_check(params):
    """Run a specific compliance check type."""
    check_type = params["check_type"]
    target = params["target"]

    if check_type == "file_permission":
        result = run_command_full(["stat", "-c", "%a %U %G", target], timeout=10)
        if result["returncode"] != 0:
            return {"success": False, "error": result["stderr"] or f"Cannot stat {target}"}
        parts = result["stdout"].strip().split()
        return {
            "success": True,
            "mode": parts[0] if len(parts) > 0 else "",
            "owner": parts[1] if len(parts) > 1 else "",
            "group": parts[2] if len(parts) > 2 else "",
        }

    elif check_type == "service_status":
        enabled = run_command_full(["systemctl", "is-enabled", target], timeout=10)
        active = run_command_full(["systemctl", "is-active", target], timeout=10)
        return {
            "success": True,
            "enabled": enabled["stdout"].strip(),
            "active": active["stdout"].strip(),
        }

    elif check_type == "package_installed":
        result = run_command_full(
            ["dpkg-query", "-W", "-f=${Status}", target], timeout=10
        )
        installed = "install ok installed" in result["stdout"]
        return {"success": True, "installed": installed, "status": result["stdout"].strip()}

    elif check_type == "port_listening":
        result = run_command_full(["ss", "-tlnp"], timeout=10)
        listening = f":{target} " in result["stdout"] or f":{target}\t" in result["stdout"]
        return {"success": True, "listening": listening, "output": result["stdout"][:4096]}

    elif check_type == "command_safe":
        # Only allow a small set of safe read-only commands
        safe_commands = {
            "auditctl -l": ["auditctl", "-l"],
            "docker info": ["docker", "info", "--format", "json"],
            "ufw status": ["sudo", "-n", "ufw", "status", "verbose"],
        }
        if target not in safe_commands:
            return {"success": False, "error": f"Command not in safe list: {target}"}
        result = run_command_full(safe_commands[target], timeout=30)
        return {"success": True, "output": result["stdout"][:8192], "returncode": result["returncode"]}

    return {"success": False, "error": f"Unknown check type: {check_type}"}


# ============================================================================
# COMPLIANCE REMEDIATION HANDLERS (added for compliance fix pipeline)
# ============================================================================

# Whitelist of sysctl params that can be safely modified via remediation.
# Keep this list conservative — only STIG/CIS compliance items.
_COMPLIANCE_SYSCTL_ALLOW = {
    "kernel.randomize_va_space",
    "kernel.kptr_restrict",
    "kernel.dmesg_restrict",
    "kernel.panic",
    "kernel.panic_on_oops",
    "net.ipv4.conf.all.accept_redirects",
    "net.ipv4.conf.all.accept_source_route",
    "net.ipv4.conf.all.log_martians",
    "net.ipv4.conf.all.rp_filter",
    "net.ipv4.conf.all.send_redirects",
    "net.ipv4.conf.default.accept_redirects",
    "net.ipv4.conf.default.accept_source_route",
    "net.ipv4.conf.default.log_martians",
    "net.ipv4.conf.default.rp_filter",
    "net.ipv4.conf.default.send_redirects",
    "net.ipv4.icmp_echo_ignore_broadcasts",
    "net.ipv4.icmp_ignore_bogus_error_responses",
    "net.ipv4.ip_forward",
    "net.ipv4.tcp_syncookies",
    "net.ipv6.conf.all.accept_ra",
    "net.ipv6.conf.all.accept_redirects",
    "net.ipv6.conf.all.accept_source_route",
    "net.ipv6.conf.default.accept_ra",
    "net.ipv6.conf.default.accept_redirects",
    "net.ipv6.conf.default.accept_source_route",
    "fs.suid_dumpable",
    "fs.protected_hardlinks",
    "fs.protected_symlinks",
}

# Whitelist of config file paths that can be edited via file_line fix.
_COMPLIANCE_FILE_ALLOW = {
    "/etc/sysctl.conf",
    "/etc/sysctl.d/99-compliance.conf",
    "/etc/ssh/sshd_config",
    "/etc/login.defs",
    "/etc/security/limits.conf",
    "/etc/docker/daemon.json",
    "/etc/default/grub",
    "/etc/audit/auditd.conf",
    "/etc/modprobe.d/blacklist.conf",
}

# Whitelist of services that can be controlled via remediation.
_COMPLIANCE_SERVICE_ALLOW = {
    "sshd", "ssh", "docker", "auditd", "ufw", "fail2ban",
    "chronyd", "systemd-journald", "cron",
}


def handle_compliance_fix_sysctl(params):
    """Apply or dry-run a sysctl parameter change.

    Validates against _COMPLIANCE_SYSCTL_ALLOW whitelist.
    Supports dry_run to preview without applying.
    """
    param = params["param"]
    value = str(params["value"]).strip()
    dry_run = bool(params.get("dry_run", False))

    if param not in _COMPLIANCE_SYSCTL_ALLOW:
        return {
            "success": False,
            "error": f"sysctl param '{param}' not in compliance whitelist",
        }

    # Get current value for preview
    current = run_command_full(["sysctl", "-n", param], timeout=5)
    current_value = current["stdout"].strip() if current["returncode"] == 0 else "unknown"

    preview = f"sysctl {param}: {current_value} -> {value}"

    if dry_run:
        return {
            "success": True,
            "preview": preview,
            "applied": False,
            "current": current_value,
            "target": value,
        }

    # Apply runtime change
    result = run_command_full(
        ["sudo", "-n", "sysctl", "-w", f"{param}={value}"],
        timeout=10,
    )
    if result["returncode"] != 0:
        return {"success": False, "error": result["stderr"] or "sysctl failed"}

    # Persist to sysctl.d to survive reboots (idempotent append)
    persist_file = "/etc/sysctl.d/99-compliance.conf"
    entry = f"{param} = {value}"
    persist_cmd = [
        "sudo", "-n", "bash", "-c",
        f"touch {persist_file} && "
        f"sed -i '/^{param}[[:space:]]*=/d' {persist_file} && "
        f"echo '{entry}' >> {persist_file}",
    ]
    persist_result = run_command_full(persist_cmd, timeout=10)

    return {
        "success": True,
        "applied": True,
        "preview": preview,
        "output": result["stdout"].strip(),
        "persisted": persist_result["returncode"] == 0,
    }


def handle_compliance_fix_file_line(params):
    """Add or replace a line in a whitelisted config file.

    Uses sed for atomic line replacement. The first word of the line is
    treated as the 'key' — any existing line starting with that key is
    removed before adding the new line.
    """
    path = params["path"]
    line = str(params["line"]).strip()
    restart_service = str(params.get("restart_service", "")).strip()
    dry_run = bool(params.get("dry_run", False))

    if path not in _COMPLIANCE_FILE_ALLOW:
        return {"success": False, "error": f"Path '{path}' not in compliance whitelist"}

    if not line:
        return {"success": False, "error": "Empty line not allowed"}

    # Sanitize — reject lines with shell metacharacters that could break sed
    if any(c in line for c in ("'", '"', "\n", "\\", "`", "$(")):
        return {"success": False, "error": "Line contains unsafe characters"}

    key = line.split()[0] if line.split() else ""
    if not key:
        return {"success": False, "error": "Cannot determine key from line"}

    # Preview current state
    preview_lines = []
    current = run_command_full(
        ["grep", f"^{key}", path], timeout=5,
    )
    if current["returncode"] == 0:
        preview_lines.append(f"Current: {current['stdout'].strip()}")
    preview_lines.append(f"New:     {line}")
    if restart_service:
        preview_lines.append(f"Then restart: {restart_service}")
    preview = "\n".join(preview_lines)

    if dry_run:
        return {
            "success": True,
            "preview": preview,
            "applied": False,
        }

    # Ensure file exists
    touch_cmd = run_command_full(["sudo", "-n", "touch", path], timeout=5)
    if touch_cmd["returncode"] != 0:
        return {"success": False, "error": f"Cannot touch {path}: {touch_cmd['stderr']}"}

    # Remove any existing line with this key, then append the new line
    fix_cmd = [
        "sudo", "-n", "bash", "-c",
        f"sed -i '/^{key}[[:space:]]*/d' '{path}' && "
        f"echo '{line}' | sudo tee -a '{path}' > /dev/null",
    ]
    result = run_command_full(fix_cmd, timeout=15)
    if result["returncode"] != 0:
        return {"success": False, "error": result["stderr"] or "File edit failed"}

    output = "File updated"
    if restart_service and restart_service in _COMPLIANCE_SERVICE_ALLOW:
        restart_result = run_command_full(
            ["sudo", "-n", "systemctl", "restart", f"{restart_service}.service"],
            timeout=30,
        )
        if restart_result["returncode"] == 0:
            output += f"; {restart_service} restarted"
        else:
            output += f"; {restart_service} restart FAILED: {restart_result['stderr'][:100]}"

    return {
        "success": True,
        "applied": True,
        "preview": preview,
        "output": output,
    }


def handle_compliance_fix_command(params):
    """Execute an arbitrary shell command for a compliance fix.

    SECURITY: This handler requires Tier-2 human approval via
    autonomy_approval_queue — the command is NOT whitelisted here.
    It should ONLY be called from apply_fix_approved() after
    a human has explicitly approved the command via Telegram or web.

    Rejects patterns that would be destructive regardless of approval.
    """
    command = params.get("command", "")
    dry_run = bool(params.get("dry_run", False))

    if not command or not isinstance(command, str):
        return {"success": False, "error": "Empty or invalid command"}

    # Hard-blocked patterns — always reject, even if human-approved
    blocked_patterns = [
        "rm -rf /",
        "mkfs.",
        "dd if=/dev/zero of=/dev/",
        ":(){ :|:& };:",  # fork bomb
        "> /dev/sda",
        "chmod -R 000 /",
        "chown -R nobody /",
    ]
    for pat in blocked_patterns:
        if pat in command:
            return {"success": False, "error": f"Command contains blocked pattern: {pat}"}

    if dry_run:
        return {
            "success": True,
            "preview": f"Would execute: {command[:200]}",
            "applied": False,
        }

    # Execute via bash -c with sudo
    result = run_command_full(
        ["sudo", "-n", "bash", "-c", command],
        timeout=60,
    )
    if result["returncode"] != 0:
        return {
            "success": False,
            "error": result["stderr"] or result["stdout"] or "Command failed",
            "returncode": result["returncode"],
        }
    return {
        "success": True,
        "applied": True,
        "output": (result["stdout"] or "")[:2000],
    }


def handle_compliance_check_command(params):
    """Execute a read-only shell command for compliance scanning.

    Runs the command as the current user (no sudo) to check system state.
    Blocked patterns prevent any writes/modifications.
    Output capped at 8KB.
    """
    command = params.get("command", "")
    if not command or not isinstance(command, str):
        return {"success": False, "error": "Empty or invalid command"}

    # Block any write/modify patterns — this is read-only
    # Use regex word boundaries to avoid false positives (e.g. "tcp" matching "cp")
    import re as _re
    blocked_re = _re.compile(
        r'(?:^|[;&|]\s*)(?:rm|mv|cp|dd|mkfs|chmod|chown|tee|truncate|wget|apt|pip|pkill|reboot|shutdown)\s',
        _re.IGNORECASE
    )
    blocked_exact = [">> /", "> /", "systemctl start", "systemctl stop", "systemctl restart", "kill -"]
    if blocked_re.search(command):
        return {"success": False, "error": "Write command blocked in read-only check"}
    for pat in blocked_exact:
        if pat in command:
            return {"success": False, "error": f"Write command blocked in check: {pat}"}

    result = run_command_full(
        ["bash", "-c", command],
        timeout=15,
    )
    output = ((result["stdout"] or "") + (result["stderr"] or "")).strip()
    return {
        "success": True,
        "output": output[:8192],
        "returncode": result["returncode"],
    }


def handle_compliance_restore_file(params):
    """Restore a config file to previous content (for git-style rollback).

    Writes the given content to the file atomically via temp file + mv.
    Only allowed on whitelisted paths, max 64KB.
    """
    import tempfile
    import os

    path = params["path"]
    content = params["content"]

    if path not in _COMPLIANCE_FILE_ALLOW:
        return {"success": False, "error": f"Path '{path}' not in compliance whitelist"}

    if len(content) > 64 * 1024:
        return {"success": False, "error": "Content too large (>64KB)"}

    # Write to temp file in /tmp, then sudo mv into place
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, dir="/tmp", prefix="compliance_restore_"
        ) as tf:
            tf.write(content)
            tmp_path = tf.name
        os.chmod(tmp_path, 0o644)

        result = run_command_full(
            ["sudo", "-n", "mv", tmp_path, path],
            timeout=10,
        )
        if result["returncode"] != 0:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return {"success": False, "error": result["stderr"] or "mv failed"}

        return {"success": True, "restored": path, "size": len(content)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def handle_compliance_fix_service(params):
    """Apply a service state change (start/stop/restart/enable/disable).

    Only whitelisted services are allowed.
    """
    service = params["service"]
    action = params["action"]
    dry_run = bool(params.get("dry_run", False))

    if service not in _COMPLIANCE_SERVICE_ALLOW:
        return {
            "success": False,
            "error": f"Service '{service}' not in compliance whitelist",
        }

    # Get current state
    active = run_command_full(["systemctl", "is-active", service], timeout=5)
    enabled = run_command_full(["systemctl", "is-enabled", service], timeout=5)
    current_state = f"active={active['stdout'].strip()}, enabled={enabled['stdout'].strip()}"
    preview = f"systemctl {action} {service} (current: {current_state})"

    if dry_run:
        return {
            "success": True,
            "preview": preview,
            "applied": False,
            "current_state": current_state,
        }

    result = run_command_full(
        ["sudo", "-n", "systemctl", action, f"{service}.service"],
        timeout=30,
    )
    if result["returncode"] != 0:
        return {"success": False, "error": result["stderr"] or f"systemctl {action} failed"}

    return {
        "success": True,
        "applied": True,
        "preview": preview,
        "output": f"{service} {action} successful",
    }


# Handler dispatch table
_HANDLER_MAP = {
    "handle_service_control": handle_service_control,
    "handle_discover_media_services": handle_discover_media_services,
    "handle_filesystem_resize": handle_filesystem_resize,
    "handle_lvm_extend": handle_lvm_extend,
    "handle_lvm_shrink": handle_lvm_shrink,
    "handle_lvm_rename": handle_lvm_rename,
    "handle_lvm_create": handle_lvm_create,
    "handle_vg_create": handle_vg_create,
    "handle_lvm_snapshot": handle_lvm_snapshot,
    "handle_disk_mount": handle_disk_mount,
    "handle_disk_unmount": handle_disk_unmount,
    "handle_fix_stale_mount": handle_fix_stale_mount,
    "handle_disk_format": handle_disk_format,
    "handle_partition_create": handle_partition_create,
    "handle_pv_create_vg_extend": handle_pv_create_vg_extend,
    "handle_disk_prepare": handle_disk_prepare,
    "handle_disk_label": handle_disk_label,
    "handle_convert_to_lvm": handle_convert_to_lvm,
    "handle_disk_prepare_lvm": handle_disk_prepare_lvm,
    "handle_disk_wipe": handle_disk_wipe,
    # Firewall handlers
    "handle_firewall_status": handle_firewall_status,
    "handle_firewall_rules": handle_firewall_rules,
    "handle_firewall_enable": handle_firewall_enable,
    "handle_firewall_disable": handle_firewall_disable,
    "handle_firewall_allow": handle_firewall_allow,
    "handle_firewall_deny": handle_firewall_deny,
    "handle_firewall_delete": handle_firewall_delete,
    "handle_firewall_update": handle_firewall_update,
    # fail2ban handlers
    "handle_fail2ban_status": handle_fail2ban_status,
    "handle_fail2ban_jail_status": handle_fail2ban_jail_status,
    "handle_fail2ban_ban": handle_fail2ban_ban,
    "handle_fail2ban_unban": handle_fail2ban_unban,
    # Network discovery
    "handle_arp_scan": handle_arp_scan,
    # Nginx handlers
    "handle_nginx_list_configs": handle_nginx_list_configs,
    "handle_nginx_get_config": handle_nginx_get_config,
    "handle_nginx_test": handle_nginx_test,
    "handle_nginx_reload": handle_nginx_reload,
    # Compliance check handlers (read-only)
    "handle_compliance_read_file": handle_compliance_read_file,
    "handle_compliance_check_sysctl": handle_compliance_check_sysctl,
    "handle_compliance_run_check": handle_compliance_run_check,
    "handle_compliance_fix_sysctl": handle_compliance_fix_sysctl,
    "handle_compliance_fix_file_line": handle_compliance_fix_file_line,
    "handle_compliance_fix_service": handle_compliance_fix_service,
    "handle_compliance_restore_file": handle_compliance_restore_file,
    "handle_compliance_fix_command": handle_compliance_fix_command,
    "handle_compliance_check_command": handle_compliance_check_command,
}


# ============================================================================
# COMMAND QUEUE PROCESSING
# ============================================================================


def ensure_command_dirs():
    """Create command queue directories if they don't exist"""
    for d in (COMMANDS_DIR, PENDING_DIR, COMPLETED_DIR):
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o777)


def log_command(cmd_id, cmd_data, result, duration):
    """Append command execution to JSONL audit log"""
    try:
        entry = {
            "command_id": cmd_id,
            "command_type": cmd_data.get("command_type"),
            "params": cmd_data.get("params"),
            "submitted_by": cmd_data.get("submitted_by"),
            "user_id": cmd_data.get("user_id"),
            "result": result,
            "duration_seconds": round(duration, 2),
            "executed_at": datetime.now().isoformat(),
        }
        with open(COMMAND_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"  Warning: failed to write command log: {e}")


def _cleanup_completed():
    """Remove completed command files older than TTL"""
    try:
        now = time.time()
        for filepath in glob.glob(os.path.join(COMPLETED_DIR, "*.json")):
            try:
                if now - os.path.getmtime(filepath) > COMPLETED_TTL_SECONDS:
                    os.remove(filepath)
            except OSError:
                pass
    except Exception:
        pass


def process_command_queue():
    """Scan pending directory, validate, execute, write results.
    Returns the number of commands processed."""
    processed = 0

    try:
        pending_files = sorted(glob.glob(os.path.join(PENDING_DIR, "*.json")))
    except Exception:
        return 0

    for filepath in pending_files:
        cmd_id = os.path.basename(filepath).replace(".json", "")
        print(f"  Processing command: {cmd_id}")

        try:
            with open(filepath, "r") as f:
                cmd_data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  Error reading {filepath}: {e}")
            # Write error result
            _write_result(
                cmd_id,
                {"success": False, "error": f"Failed to read command file: {e}"},
                cmd_data={},
                duration=0,
            )
            _safe_remove(filepath)
            processed += 1
            continue

        # Validate
        valid, error = validate_command(cmd_data)
        if not valid:
            print(f"  Validation failed: {error}")
            _write_result(
                cmd_id,
                {"success": False, "error": error},
                cmd_data=cmd_data,
                duration=0,
            )
            _safe_remove(filepath)
            processed += 1
            continue

        # Execute handler
        cmd_type = cmd_data["command_type"]
        handler_name = COMMAND_REGISTRY[cmd_type]["handler"]
        handler_fn = _HANDLER_MAP.get(handler_name)

        if not handler_fn:
            _write_result(
                cmd_id,
                {"success": False, "error": f"No handler for {cmd_type}"},
                cmd_data=cmd_data,
                duration=0,
            )
            _safe_remove(filepath)
            processed += 1
            continue

        start_time = time.time()
        try:
            result = handler_fn(cmd_data.get("params", {}))
        except Exception as e:
            result = {"success": False, "error": f"Handler exception: {str(e)}"}
        duration = time.time() - start_time

        print(
            f"  Result: {'OK' if result.get('success') else 'FAIL'} ({duration:.1f}s)"
        )

        # Write result
        _write_result(cmd_id, result, cmd_data=cmd_data, duration=duration)
        log_command(cmd_id, cmd_data, result, duration)
        _safe_remove(filepath)
        processed += 1

    # Periodic cleanup
    _cleanup_completed()

    return processed


def _write_result(cmd_id, result, cmd_data, duration):
    """Write command result to completed directory"""
    try:
        result_data = {
            "command_id": cmd_id,
            "command_type": cmd_data.get("command_type", "unknown"),
            "result": result,
            "duration_seconds": round(duration, 2),
            "completed_at": datetime.now().isoformat(),
        }
        result_path = os.path.join(COMPLETED_DIR, f"{cmd_id}.json")
        with open(result_path, "w") as f:
            json.dump(result_data, f, indent=2)
        os.chmod(result_path, 0o644)
    except Exception as e:
        print(f"  Error writing result for {cmd_id}: {e}")


def _safe_remove(filepath):
    """Remove a file, ignoring errors"""
    try:
        os.remove(filepath)
    except OSError:
        pass


def get_docker_mounts():
    """Get host paths mounted by Docker containers"""
    docker_mounts = {}  # host_path -> [container_names]

    try:
        # Get list of running containers
        result = run_command(["docker", "ps", "--format", "{{.Names}}"], timeout=10)
        if not result:
            return docker_mounts

        containers = result.strip().split("\n")

        for container in containers:
            if not container:
                continue
            # Get mounts for this container
            mount_result = run_command(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{range .Mounts}}{{.Source}}|{{end}}",
                    container,
                ],
                timeout=10,
            )
            if mount_result:
                paths = [p.strip() for p in mount_result.split("|") if p.strip()]
                for path in paths:
                    if path not in docker_mounts:
                        docker_mounts[path] = []
                    if container not in docker_mounts[path]:
                        docker_mounts[path].append(container)
    except Exception as e:
        print(f"Error getting docker mounts: {e}")

    return docker_mounts


def get_port_usage():
    """Get comprehensive port usage info for conflict detection"""
    ports = {}

    # Get TCP listening ports from ss
    ss_tcp = run_command(["ss", "-tlnp"], timeout=10)
    if ss_tcp:
        for line in ss_tcp.split("\n")[1:]:
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                local_addr = parts[3]
                # Parse address:port
                if "]:" in local_addr:
                    ip, port = local_addr.rsplit(":", 1)
                    ip = ip.strip("[]")
                elif local_addr.startswith("*:"):
                    ip, port = "0.0.0.0", local_addr[2:]
                else:
                    ip, port = local_addr.rsplit(":", 1)
                port = int(port)

                # Extract process name
                process = None
                pid = None
                if len(parts) >= 6:
                    proc_info = parts[-1]
                    proc_match = re.search(r'users:\(\("([^"]+)",pid=(\d+)', proc_info)
                    if proc_match:
                        process = proc_match.group(1)
                        pid = int(proc_match.group(2))

                if port not in ports:
                    ports[port] = {"tcp": [], "udp": []}
                ports[port]["tcp"].append({"bind": ip, "process": process, "pid": pid})
            except (ValueError, IndexError):
                continue

    # Get UDP listening ports from ss
    ss_udp = run_command(["ss", "-ulnp"], timeout=10)
    if ss_udp:
        for line in ss_udp.split("\n")[1:]:
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                local_addr = parts[4] if len(parts) > 4 else parts[3]
                if "]:" in local_addr:
                    ip, port = local_addr.rsplit(":", 1)
                    ip = ip.strip("[]")
                elif local_addr.startswith("*:"):
                    ip, port = "0.0.0.0", local_addr[2:]
                else:
                    ip, port = local_addr.rsplit(":", 1)
                port = int(port)

                process = None
                pid = None
                if len(parts) >= 6:
                    proc_info = parts[-1]
                    proc_match = re.search(r'users:\(\("([^"]+)",pid=(\d+)', proc_info)
                    if proc_match:
                        process = proc_match.group(1)
                        pid = int(proc_match.group(2))

                if port not in ports:
                    ports[port] = {"tcp": [], "udp": []}
                ports[port]["udp"].append({"bind": ip, "process": process, "pid": pid})
            except (ValueError, IndexError):
                continue

    # Get Docker container port mappings
    docker_ports = run_command(
        ["docker", "ps", "--format", "{{.Names}}|{{.Ports}}"], timeout=10
    )
    if docker_ports:
        for line in docker_ports.strip().split("\n"):
            if not line or "|" not in line:
                continue
            name, port_str = line.split("|", 1)
            if not port_str:
                continue
            # Parse port mappings like "0.0.0.0:8080->80/tcp, :::8080->80/tcp"
            for mapping in port_str.split(", "):
                match = re.search(
                    r"(\d+\.\d+\.\d+\.\d+)?:?(\d+)->(\d+)/(tcp|udp)", mapping
                )
                if match:
                    host_ip = match.group(1) or "0.0.0.0"
                    host_port = int(match.group(2))
                    container_port = int(match.group(3))
                    proto = match.group(4)

                    if host_port not in ports:
                        ports[host_port] = {"tcp": [], "udp": []}

                    ports[host_port][proto].append(
                        {
                            "bind": host_ip,
                            "process": f"docker:{name}",
                            "container": name,
                            "container_port": container_port,
                            "pid": None,
                        }
                    )

    return ports


def get_docker_health():
    """Get Docker container health status and resource usage"""
    containers = []

    # Get container info with health status
    fmt = "{{.Names}}|{{.Status}}|{{.State}}|{{.Image}}"
    output = run_command(["docker", "ps", "-a", "--format", fmt], timeout=10)
    if not output:
        return containers

    for line in output.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue

        name, status, state, image = parts[0], parts[1], parts[2], parts[3]

        # Parse health from status (e.g., "Up 2 hours (healthy)")
        health = "none"
        if "(healthy)" in status:
            health = "healthy"
        elif "(unhealthy)" in status:
            health = "unhealthy"
        elif "(health: starting)" in status:
            health = "starting"

        # Get restart count and compose project label
        restart_count = 0
        compose_project = ""
        inspect_output = run_command(
            [
                "docker",
                "inspect",
                "--format",
                '{{.RestartCount}}|{{index .Config.Labels "com.docker.compose.project"}}',
                name,
            ],
            timeout=5,
        )
        if inspect_output:
            inspect_parts = inspect_output.strip().split("|")
            try:
                restart_count = int(inspect_parts[0]) if inspect_parts[0] else 0
            except ValueError:
                pass
            if len(inspect_parts) > 1 and inspect_parts[1]:
                compose_project = inspect_parts[1]

        # Get resource usage from docker stats (no-stream for single snapshot)
        cpu_percent = 0.0
        mem_percent = 0.0
        mem_usage = "0B"

        if state == "running":
            stats_output = run_command(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{.CPUPerc}}|{{.MemPerc}}|{{.MemUsage}}",
                    name,
                ],
                timeout=10,
            )
            if stats_output:
                stats_parts = stats_output.strip().split("|")
                if len(stats_parts) >= 3:
                    try:
                        cpu_percent = float(stats_parts[0].replace("%", ""))
                        mem_percent = float(stats_parts[1].replace("%", ""))
                        mem_usage = stats_parts[2].split("/")[0].strip()
                    except (ValueError, IndexError):
                        pass

        containers.append(
            {
                "name": name,
                "state": state,
                "health": health,
                "image": image,
                "restart_count": restart_count,
                "cpu_percent": cpu_percent,
                "mem_percent": mem_percent,
                "mem_usage": mem_usage,
                "compose_project": compose_project,
            }
        )

    return containers


# ============================================================================
# HEALTH ALERTING SYSTEM
# ============================================================================

# In-memory state for tracking changes
_last_restart_counts = {}
_last_alert_times = {}  # (container_name, alert_type) -> timestamp


def get_db_connection():
    """Get database connection for alert storage"""
    import psycopg2

    # Try to load from .env file if env vars not set
    db_password = os.environ.get("DB_PASSWORD", "")
    if not db_password:
        env_file = Path("/mnt/archie_brain/.env")
        if env_file.exists():
            try:
                with open(env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("DB_PASSWORD="):
                            db_password = line.split("=", 1)[1].strip("\"'")
                            break
            except Exception:
                pass

    return psycopg2.connect(
        host=os.environ.get("DB_HOST", ""),
        port=os.environ.get("DB_PORT", "5432"),
        database=os.environ.get("DB_NAME", "archie"),
        user=os.environ.get("DB_USER", "archie"),
        password=db_password,
    )


def get_health_config():
    """Load health alert configuration from database"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT stack_name, metric_type, threshold_warning, threshold_critical,
                   enabled, cooldown_minutes
            FROM stack_health_config
            WHERE enabled = TRUE
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        config = {}
        for row in rows:
            stack_name, metric_type, warn, crit, enabled, cooldown = row
            key = (stack_name, metric_type)  # stack_name can be None for global
            config[key] = {
                "threshold_warning": float(warn) if warn else None,
                "threshold_critical": float(crit) if crit else None,
                "cooldown_minutes": cooldown or 15,
            }
        return config
    except Exception as e:
        print(f"Error loading health config: {e}")
        return {}


def get_threshold(config, stack_name, metric_type, level):
    """Get threshold value, checking stack-specific first then global"""
    # Try stack-specific
    stack_config = config.get((stack_name, metric_type))
    if stack_config:
        return stack_config.get(f"threshold_{level}")
    # Fall back to global (stack_name = None)
    global_config = config.get((None, metric_type))
    if global_config:
        return global_config.get(f"threshold_{level}")
    return None


def get_cooldown(config, stack_name, metric_type):
    """Get cooldown minutes, checking stack-specific first then global"""
    stack_config = config.get((stack_name, metric_type))
    if stack_config:
        return stack_config.get("cooldown_minutes", 15)
    global_config = config.get((None, metric_type))
    if global_config:
        return global_config.get("cooldown_minutes", 15)
    return 15


def should_alert(container_name, alert_type, cooldown_minutes):
    """Check if enough time has passed since last alert"""
    key = (container_name, alert_type)
    last_time = _last_alert_times.get(key, 0)
    now = time.time()
    if now - last_time < cooldown_minutes * 60:
        return False
    return True


def create_alert(
    stack_name, container_name, alert_type, severity, message, details=None
):
    """Create a health alert in the database"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO stack_health_alerts
            (stack_name, container_name, alert_type, severity, message, details)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """,
            (
                stack_name,
                container_name,
                alert_type,
                severity,
                message,
                json.dumps(details) if details else None,
            ),
        )
        alert_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        # Update last alert time
        key = (container_name, alert_type)
        _last_alert_times[key] = time.time()

        print(f"[ALERT] {severity.upper()}: {message}")

        # Fire webhooks
        fire_health_webhooks(
            alert_type,
            severity,
            {
                "id": alert_id,
                "stack_name": stack_name,
                "container_name": container_name,
                "alert_type": alert_type,
                "severity": severity,
                "message": message,
                "details": details,
            },
        )

        return alert_id
    except Exception as e:
        print(f"Error creating alert: {e}")
        return None


def fire_health_webhooks(event_type, severity, alert_data):
    """Fire webhooks for health alerts"""
    import requests

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Map alert types to webhook event types
        event_map = {
            "restart_loop": "container_crash",
            "crash": "container_crash",
            "unhealthy": (
                "health_warning" if severity == "warning" else "health_critical"
            ),
            "resource_threshold": (
                "health_warning" if severity == "warning" else "health_critical"
            ),
        }
        webhook_event = event_map.get(event_type, "health_warning")

        cur.execute(
            """
            SELECT id, url, secret_key, stack_filter
            FROM health_alert_webhooks
            WHERE active = TRUE
              AND %s = ANY(event_types)
        """,
            (webhook_event,),
        )
        webhooks = cur.fetchall()

        for webhook_id, url, secret_key, stack_filter in webhooks:
            # Check stack filter
            if stack_filter and alert_data.get("stack_name") not in stack_filter:
                continue

            try:
                headers = {"Content-Type": "application/json"}
                if secret_key:
                    headers["X-Webhook-Secret"] = secret_key

                payload = {
                    "event_type": webhook_event,
                    "timestamp": datetime.now().isoformat(),
                    "alert": alert_data,
                }

                response = requests.post(url, json=payload, headers=headers, timeout=10)

                # Update webhook status
                cur.execute(
                    """
                    UPDATE health_alert_webhooks
                    SET last_triggered = CURRENT_TIMESTAMP,
                        last_status = %s,
                        last_error = %s
                    WHERE id = %s
                """,
                    (
                        response.status_code,
                        None if response.ok else response.text[:500],
                        webhook_id,
                    ),
                )
                conn.commit()

            except Exception as e:
                cur.execute(
                    """
                    UPDATE health_alert_webhooks
                    SET last_triggered = CURRENT_TIMESTAMP,
                        last_status = 0,
                        last_error = %s
                    WHERE id = %s
                """,
                    (str(e)[:500], webhook_id),
                )
                conn.commit()

        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error firing webhooks: {e}")


def record_container_metrics(containers):
    """Record container metrics to history table"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        for container in containers:
            if container.get("state") != "running":
                continue

            # Parse memory usage to MB
            mem_usage_mb = 0
            mem_str = container.get("mem_usage", "0B")
            try:
                if "GiB" in mem_str:
                    mem_usage_mb = int(float(mem_str.replace("GiB", "").strip()) * 1024)
                elif "MiB" in mem_str:
                    mem_usage_mb = int(float(mem_str.replace("MiB", "").strip()))
                elif "KiB" in mem_str:
                    mem_usage_mb = int(float(mem_str.replace("KiB", "").strip()) / 1024)
            except:
                pass

            # Infer stack name from container name (e.g., archie_platform -> archie)
            name_parts = container["name"].split("_")
            stack_name = name_parts[0] if len(name_parts) > 1 else "default"

            cur.execute(
                """
                INSERT INTO container_metrics_history
                (container_name, stack_name, cpu_percent, mem_percent, mem_usage_mb, restart_count, health_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
                (
                    container["name"],
                    stack_name,
                    container.get("cpu_percent", 0),
                    container.get("mem_percent", 0),
                    mem_usage_mb,
                    container.get("restart_count", 0),
                    container.get("health", "none"),
                ),
            )

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error recording metrics: {e}")


def check_container_health(containers):
    """Check containers against health thresholds and create alerts"""
    global _last_restart_counts

    try:
        config = get_health_config()
        if not config:
            return  # No config loaded, skip checks

        for container in containers:
            name = container["name"]

            # Infer stack name from container name
            name_parts = name.split("_")
            stack_name = name_parts[0] if len(name_parts) > 1 else "default"

            # Check restart count changes (restart loop detection)
            current_restart = container.get("restart_count", 0)
            last_restart = _last_restart_counts.get(name, 0)

            if current_restart > last_restart:
                restart_diff = current_restart - last_restart
                cooldown = get_cooldown(config, stack_name, "restart_count")

                if should_alert(name, "restart_loop", cooldown):
                    # Check thresholds
                    crit_threshold = get_threshold(
                        config, stack_name, "restart_count", "critical"
                    )
                    warn_threshold = get_threshold(
                        config, stack_name, "restart_count", "warning"
                    )

                    if crit_threshold and current_restart >= crit_threshold:
                        create_alert(
                            stack_name,
                            name,
                            "restart_loop",
                            "critical",
                            f"Container {name} has restarted {current_restart} times (threshold: {crit_threshold})",
                            {
                                "restart_count": current_restart,
                                "previous": last_restart,
                            },
                        )
                    elif warn_threshold and current_restart >= warn_threshold:
                        create_alert(
                            stack_name,
                            name,
                            "restart_loop",
                            "warning",
                            f"Container {name} has restarted {current_restart} times (threshold: {warn_threshold})",
                            {
                                "restart_count": current_restart,
                                "previous": last_restart,
                            },
                        )

            _last_restart_counts[name] = current_restart

            # Skip remaining checks for non-running containers
            if container.get("state") != "running":
                continue

            # Check unhealthy status
            health = container.get("health", "none")
            if health == "unhealthy":
                cooldown = get_cooldown(config, stack_name, "unhealthy")
                if should_alert(name, "unhealthy", cooldown):
                    create_alert(
                        stack_name,
                        name,
                        "unhealthy",
                        "warning",
                        f"Container {name} is reporting unhealthy status",
                        {"health_status": health},
                    )

            # Check CPU threshold
            cpu_percent = container.get("cpu_percent", 0)
            cooldown = get_cooldown(config, stack_name, "cpu")
            if should_alert(name, "cpu_threshold", cooldown):
                crit_threshold = get_threshold(config, stack_name, "cpu", "critical")
                warn_threshold = get_threshold(config, stack_name, "cpu", "warning")

                if crit_threshold and cpu_percent >= crit_threshold:
                    create_alert(
                        stack_name,
                        name,
                        "resource_threshold",
                        "critical",
                        f"Container {name} CPU at {cpu_percent:.1f}% (threshold: {crit_threshold}%)",
                        {
                            "metric": "cpu",
                            "value": cpu_percent,
                            "threshold": crit_threshold,
                        },
                    )
                elif warn_threshold and cpu_percent >= warn_threshold:
                    create_alert(
                        stack_name,
                        name,
                        "resource_threshold",
                        "warning",
                        f"Container {name} CPU at {cpu_percent:.1f}% (threshold: {warn_threshold}%)",
                        {
                            "metric": "cpu",
                            "value": cpu_percent,
                            "threshold": warn_threshold,
                        },
                    )

            # Check memory threshold
            mem_percent = container.get("mem_percent", 0)
            cooldown = get_cooldown(config, stack_name, "memory")
            if should_alert(name, "memory_threshold", cooldown):
                crit_threshold = get_threshold(config, stack_name, "memory", "critical")
                warn_threshold = get_threshold(config, stack_name, "memory", "warning")

                if crit_threshold and mem_percent >= crit_threshold:
                    create_alert(
                        stack_name,
                        name,
                        "resource_threshold",
                        "critical",
                        f"Container {name} memory at {mem_percent:.1f}% (threshold: {crit_threshold}%)",
                        {
                            "metric": "memory",
                            "value": mem_percent,
                            "threshold": crit_threshold,
                        },
                    )
                elif warn_threshold and mem_percent >= warn_threshold:
                    create_alert(
                        stack_name,
                        name,
                        "resource_threshold",
                        "warning",
                        f"Container {name} memory at {mem_percent:.1f}% (threshold: {warn_threshold}%)",
                        {
                            "metric": "memory",
                            "value": mem_percent,
                            "threshold": warn_threshold,
                        },
                    )

    except Exception as e:
        print(f"Error checking container health: {e}")


def cleanup_old_metrics():
    """Remove metrics older than 7 days"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM container_metrics_history
            WHERE recorded_at < NOW() - INTERVAL '7 days'
        """)
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if deleted > 0:
            print(f"[Cleanup] Removed {deleted} old metric records")
    except Exception as e:
        print(f"Error cleaning up old metrics: {e}")


def collect_all_data():
    """Collect all host system data"""
    network = get_network_info()
    container_bw = get_container_bandwidth()

    # Update rolling bandwidth history
    update_bandwidth_history(network, container_bw)

    # Check command queue availability
    queue_available = os.path.isdir(PENDING_DIR) and os.path.isdir(COMPLETED_DIR)

    # Get disk layout first so we can check capacity alerts
    disks = get_disk_layout()

    return {
        "system": get_system_info(),
        "cpu": get_cpu_info(),
        "cpu_hardware": get_cpu_hardware(),
        "gpu": get_gpu_info(),
        "memory_hardware": get_memory_hardware(),
        "motherboard": get_motherboard_info(),
        "pci_slots": get_pci_slots(),
        "sata_ports": get_sata_ports(),
        "lvm": get_lvm_info(),
        "raid": get_raid_status(),
        "disks": disks,
        "smart_details": get_smart_details(),
        "capacity_alerts": check_capacity_alerts(disks),
        "services": get_systemd_services(),
        "processes": get_process_list(),
        "network": network,
        "container_bandwidth": container_bw,
        "docker_mounts": get_docker_mounts(),
        "port_usage": get_port_usage(),
        "docker_health": get_docker_health(),
        "command_queue": {"available": queue_available},
        "collected_at": datetime.now().isoformat(),
        "host": os.uname().nodename,
    }


def write_data(data):
    """Write data to JSON file"""
    try:
        # Ensure directory exists
        Path(OUTPUT_FILE).parent.mkdir(parents=True, exist_ok=True)

        # Write directly to file (don't use rename - breaks Docker bind mounts)
        # Docker bind mounts track by inode, rename creates new inode
        with open(OUTPUT_FILE, "w") as f:
            json.dump(data, f, indent=2)

        # Make readable by container
        os.chmod(OUTPUT_FILE, 0o644)

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Updated {OUTPUT_FILE}")
        return True
    except Exception as e:
        print(f"Error writing data: {e}")
        return False


def main():
    global OUTPUT_FILE

    parser = argparse.ArgumentParser(description="A.R.C.H.I.E. Host System Monitor")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Update interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--output", type=str, default=OUTPUT_FILE, help="Output file path"
    )
    args = parser.parse_args()

    OUTPUT_FILE = args.output

    # Ensure command queue directories exist
    ensure_command_dirs()

    print("A.R.C.H.I.E. Host System Monitor")
    print(f"Output file: {OUTPUT_FILE}")
    print(f"Command queue: {COMMANDS_DIR}")
    print(
        f"Mode: {'Daemon (interval: {}s)'.format(args.interval) if args.daemon else 'Single run'}"
    )
    print("-" * 50)

    if args.daemon:
        print("Starting daemon mode... (Ctrl+C to stop)")
        print("Health alerting: ENABLED")

        # Restore managed mounts from database on startup
        try:
            restore_managed_mounts()
        except Exception as e:
            print(f"[Mount Restore] Startup error: {e}")

        QUEUE_CHECK_INTERVAL = 3  # Check command queue every 3 seconds
        HEALTH_CHECK_INTERVAL = 60  # Check health every 60 seconds
        CLEANUP_INTERVAL = 3600  # Cleanup old metrics every hour
        last_data_collect = 0  # Force immediate first collection
        last_health_check = 0
        last_cleanup = 0

        # Dedicated queue-processing thread — never blocked by data collection
        import threading
        _queue_stop = threading.Event()

        def _queue_worker():
            while not _queue_stop.is_set():
                try:
                    processed = process_command_queue()
                    if processed > 0:
                        print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] Queue worker processed {processed} command(s)"
                        )
                except Exception as e:
                    print(f"Queue worker error: {e}")
                _queue_stop.wait(3)  # Check every 3 seconds

        queue_thread = threading.Thread(target=_queue_worker, daemon=True, name="cmd-queue")
        queue_thread.start()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Command queue worker thread started")

        while True:
            try:
                now = time.time()

                # Collect system data at the configured interval
                if now - last_data_collect >= args.interval:
                    data = collect_all_data()
                    write_data(data)
                    last_data_collect = now

                    # Check container health after data collection
                    if now - last_health_check >= HEALTH_CHECK_INTERVAL:
                        try:
                            docker_health = data.get("docker_health", [])
                            if docker_health:
                                check_container_health(docker_health)
                                record_container_metrics(docker_health)
                            last_health_check = now
                        except Exception as e:
                            print(f"Health check error: {e}")

                # Periodic cleanup of old metrics
                if now - last_cleanup >= CLEANUP_INTERVAL:
                    try:
                        cleanup_old_metrics()
                        last_cleanup = now
                    except Exception as e:
                        print(f"Cleanup error: {e}")

                time.sleep(QUEUE_CHECK_INTERVAL)
            except KeyboardInterrupt:
                print("\nStopping...")
                break
            except Exception as e:
                print(f"Error: {e}")
                time.sleep(5)
    else:
        data = collect_all_data()
        write_data(data)
        print("\nCollected data:")
        sys_info = data.get("system") or {}
        print(
            f"  Host: {sys_info.get('hostname', '?')} ({sys_info.get('distribution', '?')})"
        )
        print(f"  Kernel: {sys_info.get('kernel', '?')}")
        print(f"  Uptime: {sys_info.get('uptime_formatted', '?')}")
        print(
            f"  Processes: {sys_info.get('process_count', '?')}, Users: {sys_info.get('users_logged_in', '?')}"
        )
        cpu_info = data.get("cpu") or {}
        print(
            f"  CPU: {cpu_info.get('model', '?')} ({cpu_info.get('physical_cores', '?')}c/{cpu_info.get('logical_cores', '?')}t), Temp: {cpu_info.get('temperature', '?')}°C"
        )
        gpu_info = data.get("gpu")
        if gpu_info:
            for g in gpu_info:
                print(
                    f"  GPU: {g.get('model', '?')} ({g.get('vendor', '?')}), Temp: {g.get('temperature', '?')}°C, Driver: {g.get('driver', '?')}"
                )
        else:
            print("  GPU: None detected")
        print(f"  Disks: {len(data['disks'])}")
        for d in data["disks"]:
            print(f"    {d['device']}: {d['size_gb']} GB ({d['model']})")
        print(f"  LVM VGs: {len(data['lvm']['vgs']) if data['lvm'] else 0}")
        print(f"  LVM LVs: {len(data['lvm']['lvs']) if data['lvm'] else 0}")
        print(f"  RAID Arrays: {len(data['raid']['arrays']) if data['raid'] else 0}")
        print(f"  Processes: {len(data['processes'])} (top by CPU)")
        print(f"  Services: {len(data['services'])}")
        print(
            f"  Network: {data['network'].get('primary_ip', '?')} ({len(data['network']['interfaces'])} interfaces)"
        )


if __name__ == "__main__":
    main()
