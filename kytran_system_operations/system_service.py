"""
System Service - Business Logic for System Operations Module
Provides methods for collecting and managing system metrics.
"""

import os
import subprocess
import platform
import socket
import time
from typing import Dict, Any, List, Optional
from datetime import datetime

# Try to import psutil with fallback
try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None


class SystemService:
    """Service class for system monitoring and management"""

    def __init__(self):
        self._psutil = psutil if PSUTIL_AVAILABLE else None

    # =========================================================================
    # CPU Methods
    # =========================================================================

    def get_cpu_info(self) -> Dict[str, Any]:
        """Get CPU information including per-core usage and temperature"""
        info = {
            "model": "Unknown",
            "cores_physical": 0,
            "cores_logical": 0,
            "usage_percent": 0.0,
            "per_core": [],
            "frequency_mhz": 0,
            "load_avg": [0, 0, 0],
            "temperature": None,
            "temp_high": None,
            "temp_critical": None,
        }

        # Get CPU model
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if line.startswith("model name"):
                        info["model"] = line.split(":")[1].strip()
                        break
        except Exception:
            info["model"] = platform.processor() or "Unknown"

        if self._psutil:
            info["cores_physical"] = self._psutil.cpu_count(logical=False) or 0
            info["cores_logical"] = self._psutil.cpu_count(logical=True) or 0
            info["usage_percent"] = self._psutil.cpu_percent(interval=0.5)
            info["per_core"] = self._psutil.cpu_percent(interval=0.1, percpu=True)

            freq = self._psutil.cpu_freq()
            if freq:
                info["frequency_mhz"] = int(freq.current)

            # Get CPU temperature
            try:
                if hasattr(self._psutil, "sensors_temperatures"):
                    temps = self._psutil.sensors_temperatures()
                    if temps:
                        # Try common CPU sensor names
                        for sensor_name in [
                            "coretemp",
                            "k10temp",
                            "cpu_thermal",
                            "acpitz",
                            "zenpower",
                        ]:
                            if sensor_name in temps and temps[sensor_name]:
                                sensor = temps[sensor_name][0]
                                info["temperature"] = round(sensor.current, 1)
                                info["temp_high"] = sensor.high if sensor.high else 70
                                info["temp_critical"] = sensor.critical if sensor.critical else 90
                                break
            except Exception:
                pass

        # Get load average (Linux)
        try:
            load = os.getloadavg()
            info["load_avg"] = [round(val, 2) for val in load]
        except Exception:
            pass

        return info

    # =========================================================================
    # Memory Methods
    # =========================================================================

    def get_memory_info(self) -> Dict[str, Any]:
        """Get memory information including swap"""
        info = {
            "total_gb": 0,
            "used_gb": 0,
            "available_gb": 0,
            "usage_percent": 0.0,
            "swap_total_gb": 0,
            "swap_used_gb": 0,
            "swap_percent": 0.0,
            "buffers_gb": 0,
            "cached_gb": 0,
        }

        if self._psutil:
            mem = self._psutil.virtual_memory()
            info["total_gb"] = round(mem.total / (1024**3), 2)
            info["used_gb"] = round(mem.used / (1024**3), 2)
            info["available_gb"] = round(mem.available / (1024**3), 2)
            info["usage_percent"] = mem.percent
            info["buffers_gb"] = round(getattr(mem, "buffers", 0) / (1024**3), 2)
            info["cached_gb"] = round(getattr(mem, "cached", 0) / (1024**3), 2)

            swap = self._psutil.swap_memory()
            info["swap_total_gb"] = round(swap.total / (1024**3), 2)
            info["swap_used_gb"] = round(swap.used / (1024**3), 2)
            info["swap_percent"] = swap.percent
        else:
            # Fallback: parse /proc/meminfo
            try:
                with open("/proc/meminfo", "r") as f:
                    meminfo = {}
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 2:
                            meminfo[parts[0].rstrip(":")] = int(parts[1])

                total_kb = meminfo.get("MemTotal", 0)
                free_kb = meminfo.get("MemFree", 0)
                buffers_kb = meminfo.get("Buffers", 0)
                cached_kb = meminfo.get("Cached", 0)
                available_kb = free_kb + buffers_kb + cached_kb
                used_kb = total_kb - available_kb

                info["total_gb"] = round(total_kb / (1024**2), 2)
                info["used_gb"] = round(used_kb / (1024**2), 2)
                info["available_gb"] = round(available_kb / (1024**2), 2)
                info["buffers_gb"] = round(buffers_kb / (1024**2), 2)
                info["cached_gb"] = round(cached_kb / (1024**2), 2)
                if total_kb > 0:
                    info["usage_percent"] = round((used_kb / total_kb) * 100, 1)

                # Swap
                swap_total = meminfo.get("SwapTotal", 0)
                swap_free = meminfo.get("SwapFree", 0)
                info["swap_total_gb"] = round(swap_total / (1024**2), 2)
                info["swap_used_gb"] = round((swap_total - swap_free) / (1024**2), 2)
                if swap_total > 0:
                    info["swap_percent"] = round(((swap_total - swap_free) / swap_total) * 100, 1)
            except Exception:
                pass

        return info

    # =========================================================================
    # Disk Methods
    # =========================================================================

    def get_disk_info(self) -> Dict[str, Any]:
        """Get disk information including partitions, LVM, and unassigned disks"""
        info = {
            "partitions": [],
            "unassigned": [],
            "lvm": None,
            "raid": None,
            "total_gb": 0,
            "used_gb": 0,
            "free_gb": 0,
            "root_fs": None,  # Primary filesystem info
        }

        # Get LVM information first for enrichment
        lvm_info = self._get_lvm_info()
        info["lvm"] = lvm_info

        # Build device mapping from lsblk
        device_mapping = self._get_device_mapping()

        # Track seen devices to avoid counting bind mounts multiple times
        seen_devices = set()

        if self._psutil:
            # Get mounted partitions
            for part in self._psutil.disk_partitions(all=False):
                try:
                    usage = self._psutil.disk_usage(part.mountpoint)

                    # Determine parent device and LVM status
                    parent_device = None
                    lvm_volume = None
                    is_lvm = False
                    can_extend = False
                    vg_free_gb = 0

                    # Check if this is an LVM device
                    if "/dev/mapper/" in part.device or "-" in part.device:
                        is_lvm = True
                        # Find matching LVM info
                        if lvm_info and lvm_info.get("lvs"):
                            for lv in lvm_info["lvs"]:
                                lv_path = f"/dev/mapper/{lv['vg_name']}-{lv['lv_name'].replace('-', '--')}"
                                lv_path_alt = f"/dev/{lv['vg_name']}/{lv['lv_name']}"
                                if part.device == lv_path or part.device == lv_path_alt or lv["lv_name"] in part.device:
                                    lvm_volume = lv
                                    # Find VG free space
                                    for vg in lvm_info.get("vgs", []):
                                        if vg["vg_name"] == lv["vg_name"]:
                                            vg_free_gb = vg["vg_free_gb"]
                                            can_extend = vg_free_gb > 0.1  # At least 100MB free
                                            break
                                    break

                        # Get parent device from mapping
                        parent_device = device_mapping.get(part.device, {}).get("parent_disk")
                    else:
                        # Regular partition - find parent disk
                        parent_device = device_mapping.get(part.device, {}).get("parent_disk")

                    # Skip bind mounts (same device seen multiple times)
                    # Use device + total size as key to identify unique filesystems
                    device_key = f"{part.device}:{usage.total}"
                    if device_key in seen_devices:
                        continue
                    seen_devices.add(device_key)

                    partition_info = {
                        "device": part.device,
                        "mountpoint": part.mountpoint,
                        "fstype": part.fstype,
                        "total_gb": round(usage.total / (1024**3), 2),
                        "used_gb": round(usage.used / (1024**3), 2),
                        "free_gb": round(usage.free / (1024**3), 2),
                        "usage_percent": usage.percent,
                        "parent_disk": parent_device,
                        "is_lvm": is_lvm,
                        "lvm_info": (
                            {
                                "vg_name": (lvm_volume["vg_name"] if lvm_volume else None),
                                "lv_name": (lvm_volume["lv_name"] if lvm_volume else None),
                                "can_extend": can_extend,
                                "vg_free_gb": round(vg_free_gb, 2),
                            }
                            if is_lvm
                            else None
                        ),
                    }
                    info["partitions"].append(partition_info)
                    info["total_gb"] += partition_info["total_gb"]
                    info["used_gb"] += partition_info["used_gb"]
                    info["free_gb"] += partition_info["free_gb"]

                    # Set root_fs to the primary/largest filesystem
                    # Prefer mountpoint '/' or '/app' (Docker), otherwise use largest
                    if part.mountpoint in ["/", "/app"] or info["root_fs"] is None:
                        if info["root_fs"] is None or partition_info["total_gb"] > info["root_fs"]["total_gb"]:
                            info["root_fs"] = partition_info
                except Exception:
                    pass

        # Check for unassigned disks using lsblk
        try:
            result = subprocess.run(
                ["lsblk", "-J", "-o", "NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,PKNAME"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                import json

                data = json.loads(result.stdout)
                for device in data.get("blockdevices", []):
                    if device.get("type") == "disk":
                        disk_name = device.get("name")
                        disk_size = device.get("size", "Unknown")

                        # Track unmounted partitions
                        for child in device.get("children", []):
                            child_type = child.get("type")
                            child_mount = child.get("mountpoint")
                            child_name = child.get("name")

                            # Skip LVM PVs that are in use
                            if child.get("fstype") == "LVM2_member":
                                continue

                            # Check for unmounted partitions
                            if not child_mount and child_type == "part":
                                # Check children (might be LVM)
                                has_lvm_children = any(gc.get("type") == "lvm" for gc in child.get("children", []))
                                if not has_lvm_children:
                                    info["unassigned"].append(
                                        {
                                            "device": f"/dev/{child_name}",
                                            "size": child.get("size", "Unknown"),
                                            "fstype": child.get("fstype") or "Unformatted",
                                            "status": "Unmounted",
                                            "parent_disk": f"/dev/{disk_name}",
                                        }
                                    )

                        # Check if entire disk is unassigned
                        if not device.get("children"):
                            info["unassigned"].append(
                                {
                                    "device": f"/dev/{disk_name}",
                                    "size": disk_size,
                                    "fstype": "Unpartitioned",
                                    "status": "Available",
                                    "parent_disk": None,
                                }
                            )
        except Exception:
            pass

        # Check RAID status
        info["raid"] = self._get_raid_status()

        return info

    def _get_device_mapping(self) -> Dict[str, Dict]:
        """Build a mapping of devices to their parent disks"""
        mapping = {}
        try:
            result = subprocess.run(
                ["lsblk", "-J", "-o", "NAME,TYPE,PKNAME,SIZE"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                import json

                data = json.loads(result.stdout)

                def process_device(device, parent_disk=None):
                    name = device.get("name")
                    dev_type = device.get("type")

                    if dev_type == "disk":
                        parent_disk = f"/dev/{name}"

                    # Map this device
                    if name:
                        dev_path = f"/dev/{name}"
                        if dev_type == "lvm":
                            # For LVM, use mapper path
                            dev_path = f"/dev/mapper/{name}"
                        mapping[dev_path] = {
                            "parent_disk": parent_disk,
                            "type": dev_type,
                            "size": device.get("size"),
                        }

                    # Process children
                    for child in device.get("children", []):
                        process_device(child, parent_disk)

                for device in data.get("blockdevices", []):
                    process_device(device)
        except Exception:
            pass
        return mapping

    def _get_lvm_info(self) -> Optional[Dict[str, Any]]:
        """Get LVM volume group and logical volume information"""
        info = {"vgs": [], "lvs": [], "pvs": []}

        # Get Physical Volumes
        try:
            result = subprocess.run(
                [
                    "pvs",
                    "--noheadings",
                    "--units",
                    "g",
                    "-o",
                    "pv_name,vg_name,pv_size,pv_free",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
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
        except Exception:
            pass

        # Get Volume Groups
        try:
            result = subprocess.run(
                [
                    "vgs",
                    "--noheadings",
                    "--units",
                    "g",
                    "-o",
                    "vg_name,vg_size,vg_free",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    parts = line.split()
                    if len(parts) >= 3:
                        # Parse size (remove 'g' suffix)
                        vg_size = float(parts[1].rstrip("gG")) if parts[1] else 0
                        vg_free = float(parts[2].rstrip("gG")) if parts[2] else 0
                        info["vgs"].append(
                            {
                                "vg_name": parts[0],
                                "vg_size_gb": round(vg_size, 2),
                                "vg_free_gb": round(vg_free, 2),
                            }
                        )
        except Exception:
            pass

        # Get Logical Volumes
        try:
            result = subprocess.run(
                [
                    "lvs",
                    "--noheadings",
                    "--units",
                    "g",
                    "-o",
                    "lv_name,vg_name,lv_size",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
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
        except Exception:
            pass

        return info if (info["vgs"] or info["lvs"] or info["pvs"]) else None

    def extend_lvm(
        self,
        vg_name: str,
        lv_name: str,
        size_gb: float = None,
        extend_all: bool = False,
    ) -> Dict[str, Any]:
        """
        Extend an LVM logical volume.

        Delegates to the host command queue when available (preferred path).
        Falls back to direct subprocess execution (legacy, only works with
        proper permissions inside the container).

        Args:
            vg_name: Volume group name
            lv_name: Logical volume name
            size_gb: Size to add in GB (optional if extend_all=True)
            extend_all: Use all available free space in VG

        Returns:
            Success status and message
        """
        # Try host command queue first
        try:
            from .host_command_client import (
                submit_and_wait,
                is_queue_available,
                HostCommandTimeout,
                HostCommandQueueUnavailable,
            )

            if is_queue_available():
                params = {"vg_name": vg_name, "lv_name": lv_name}
                if extend_all:
                    params["extend_all"] = True
                elif size_gb:
                    params["size_gb"] = size_gb
                else:
                    return {
                        "success": False,
                        "error": "Must specify size_gb or extend_all=True",
                    }

                result_data = submit_and_wait("lvm_extend", params, timeout=120)
                return result_data.get("result", {"success": False, "error": "No result from host"})
        except HostCommandTimeout:
            # Don't fall back — command may still be running on host
            return {
                "success": False,
                "error": "LVM extend timed out waiting for host. Check host_monitor.py logs.",
            }
        except (ImportError, HostCommandQueueUnavailable):
            pass  # Fall through to legacy path
        except Exception:
            pass  # Fall through to legacy path for unexpected errors

        # Legacy direct execution (container-side fallback)
        try:
            lv_path = f"/dev/{vg_name}/{lv_name}"

            lvm_info = self._get_lvm_info()
            vg_free = 0
            for vg in lvm_info.get("vgs", []):
                if vg["vg_name"] == vg_name:
                    vg_free = vg["vg_free_gb"]
                    break

            if vg_free < 0.1:
                return {
                    "success": False,
                    "error": f"No free space in volume group {vg_name}",
                }

            if extend_all:
                cmd = ["lvextend", "-l", "+100%FREE", lv_path]
            elif size_gb:
                if size_gb > vg_free:
                    return {
                        "success": False,
                        "error": f"Requested {size_gb}GB but only {vg_free}GB available",
                    }
                cmd = ["lvextend", "-L", f"+{size_gb}G", lv_path]
            else:
                return {
                    "success": False,
                    "error": "Must specify size_gb or extend_all=True",
                }

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                return {"success": False, "error": result.stderr or "lvextend failed"}

            fs_result = subprocess.run(
                ["blkid", "-o", "value", "-s", "TYPE", lv_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            fs_type = fs_result.stdout.strip()

            if fs_type in ["ext4", "ext3", "ext2"]:
                resize_cmd = ["resize2fs", lv_path]
            elif fs_type == "xfs":
                resize_cmd = ["xfs_growfs", lv_path]
            else:
                return {
                    "success": True,
                    "message": f"LV extended but filesystem resize not supported for {fs_type}",
                    "filesystem": fs_type,
                }

            resize_result = subprocess.run(resize_cmd, capture_output=True, text=True, timeout=120)
            if resize_result.returncode != 0:
                return {
                    "success": True,
                    "message": "LV extended but filesystem resize failed",
                    "error": resize_result.stderr,
                }

            return {
                "success": True,
                "message": f"Successfully extended {lv_path}",
                "filesystem": fs_type,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _get_raid_status(self) -> Optional[Dict[str, Any]]:
        """Check for RAID arrays"""
        try:
            if os.path.exists("/proc/mdstat"):
                with open("/proc/mdstat", "r") as f:
                    content = f.read()
                    if "md" in content and "inactive" not in content.lower():
                        # Parse mdstat for RAID info
                        arrays = []
                        for line in content.split("\n"):
                            if line.startswith("md"):
                                parts = line.split()
                                if len(parts) >= 4:
                                    arrays.append(
                                        {
                                            "name": parts[0],
                                            "status": ("active" if "active" in line else "inactive"),
                                            "level": (parts[3] if len(parts) > 3 else "unknown"),
                                            "devices": [p for p in parts[4:] if "[" in p],
                                        }
                                    )
                        if arrays:
                            return {"arrays": arrays, "healthy": True}
        except Exception:
            pass
        return None

    # =========================================================================
    # GPU Methods
    # =========================================================================

    def get_gpu_info(self) -> Dict[str, Any]:
        """Get GPU information"""
        info = {
            "available": False,
            "model": None,
            "vram_mb": 0,
            "vram_used_mb": 0,
            "usage_percent": 0.0,
            "temperature": None,
            "driver": None,
        }

        # Try NVIDIA
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu,driver_version",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split(",")
                info["available"] = True
                info["model"] = parts[0].strip()
                info["vram_mb"] = int(float(parts[1].strip())) if len(parts) > 1 else 0
                info["vram_used_mb"] = int(float(parts[2].strip())) if len(parts) > 2 else 0
                info["usage_percent"] = float(parts[3].strip()) if len(parts) > 3 else 0.0
                info["temperature"] = int(float(parts[4].strip())) if len(parts) > 4 else None
                info["driver"] = parts[5].strip() if len(parts) > 5 else None
                return info
        except Exception:
            pass

        # Try AMD ROCm
        try:
            result = subprocess.run(
                ["rocm-smi", "--showproductname"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                info["available"] = True
                info["model"] = "AMD GPU (ROCm)"
                return info
        except Exception:
            pass

        return info

    # =========================================================================
    # Process Methods
    # =========================================================================

    def get_process_list(self, sort_by: str = "cpu", limit: int = 50) -> List[Dict[str, Any]]:
        """Get list of running processes"""
        processes = []

        if self._psutil:
            for proc in self._psutil.process_iter(
                [
                    "pid",
                    "name",
                    "username",
                    "cpu_percent",
                    "memory_percent",
                    "status",
                    "create_time",
                ]
            ):
                try:
                    pinfo = proc.info
                    processes.append(
                        {
                            "pid": pinfo["pid"],
                            "name": pinfo["name"] or "Unknown",
                            "user": pinfo["username"] or "Unknown",
                            "cpu_percent": round(pinfo["cpu_percent"] or 0, 1),
                            "memory_percent": round(pinfo["memory_percent"] or 0, 1),
                            "status": pinfo["status"],
                            "started": (
                                datetime.fromtimestamp(pinfo["create_time"]).isoformat()
                                if pinfo["create_time"]
                                else None
                            ),
                        }
                    )
                except (self._psutil.NoSuchProcess, self._psutil.AccessDenied):
                    pass

            # Sort by requested field
            if sort_by == "cpu":
                processes.sort(key=lambda x: x["cpu_percent"], reverse=True)
            elif sort_by == "memory":
                processes.sort(key=lambda x: x["memory_percent"], reverse=True)
            elif sort_by == "name":
                processes.sort(key=lambda x: x["name"].lower())

        return processes[:limit]

    def kill_process(self, pid: int, signal: str = "SIGTERM") -> Dict[str, Any]:
        """Kill a process by PID"""
        # Protected PIDs (init, kernel threads)
        if pid <= 1:
            return {
                "success": False,
                "error": "Cannot kill system processes (PID <= 1)",
            }

        try:
            if self._psutil:
                proc = self._psutil.Process(pid)
                proc_name = proc.name()

                # Protect critical processes
                critical = ["init", "systemd", "kernel", "kthreadd"]
                if proc_name.lower() in critical:
                    return {
                        "success": False,
                        "error": f"Cannot kill critical system process: {proc_name}",
                    }

                if signal == "SIGKILL":
                    proc.kill()
                else:
                    proc.terminate()

                return {
                    "success": True,
                    "message": f"Process {pid} ({proc_name}) terminated",
                }
            else:
                import signal as sig

                os.kill(pid, sig.SIGTERM if signal == "SIGTERM" else sig.SIGKILL)
                return {"success": True, "message": f"Process {pid} terminated"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Service Methods (Systemd)
    # =========================================================================

    def get_services_list(self, filter_type: str = "all") -> List[Dict[str, Any]]:
        """Get systemd services"""
        services = []

        try:
            # Get all services
            result = subprocess.run(
                [
                    "systemctl",
                    "list-units",
                    "--type=service",
                    "--all",
                    "--no-pager",
                    "--plain",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                for line in lines[1:]:  # Skip header
                    if ".service" in line:
                        parts = line.split()
                        if len(parts) >= 4:
                            name = parts[0].replace(".service", "")
                            _load = parts[1]  # noqa: F841 - kept for clarity
                            active = parts[2]
                            sub = parts[3]

                            # Filter
                            if filter_type == "active" and active != "active":
                                continue
                            if filter_type == "failed" and sub != "failed":
                                continue

                            services.append(
                                {
                                    "name": name,
                                    "status": active,
                                    "sub_status": sub,
                                    "enabled": self._is_service_enabled(name),
                                }
                            )
        except Exception:
            pass

        return services

    def _is_service_enabled(self, service_name: str) -> bool:
        """Check if a service is enabled"""
        try:
            result = subprocess.run(
                ["systemctl", "is-enabled", f"{service_name}.service"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip() == "enabled"
        except Exception:
            return False

    def service_action(self, service_name: str, action: str) -> Dict[str, Any]:
        """Perform action on a systemd service"""
        valid_actions = ["start", "stop", "restart", "enable", "disable"]
        if action not in valid_actions:
            return {
                "success": False,
                "error": f"Invalid action. Must be one of: {valid_actions}",
            }

        # Protect critical services
        critical = ["docker", "postgresql", "sshd", "systemd-journald"]
        if service_name in critical and action == "stop":
            return {
                "success": False,
                "error": f"Cannot stop critical service: {service_name}",
                "warning": True,
            }

        try:
            result = subprocess.run(
                ["sudo", "systemctl", action, f"{service_name}.service"],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                new_status = self._get_service_status(service_name)
                return {
                    "success": True,
                    "message": f"Service {service_name} {action}ed",
                    "new_status": new_status,
                }
            else:
                return {"success": False, "error": result.stderr or "Action failed"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _get_service_status(self, service_name: str) -> str:
        """Get current service status"""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", f"{service_name}.service"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip()
        except Exception:
            return "unknown"

    # =========================================================================
    # Docker Methods
    # =========================================================================

    def get_docker_containers(self) -> List[Dict[str, Any]]:
        """Get Docker containers"""
        containers = []

        try:
            result = subprocess.run(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--format",
                    "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if line:
                        parts = line.split("\t")
                        if len(parts) >= 4:
                            containers.append(
                                {
                                    "id": parts[0][:12],
                                    "name": parts[1],
                                    "image": parts[2],
                                    "status": parts[3],
                                    "ports": parts[4] if len(parts) > 4 else "",
                                    "running": "Up" in parts[3],
                                }
                            )
        except Exception:
            pass

        return containers

    def docker_action(self, container_id: str, action: str) -> Dict[str, Any]:
        """Perform action on a Docker container"""
        valid_actions = ["start", "stop", "restart", "remove"]
        if action not in valid_actions:
            return {
                "success": False,
                "error": f"Invalid action. Must be one of: {valid_actions}",
            }

        try:
            cmd = ["docker", action, container_id]
            if action == "remove":
                cmd = ["docker", "rm", "-f", container_id]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode == 0:
                return {
                    "success": True,
                    "message": f"Container {container_id} {action}ed",
                }
            else:
                return {"success": False, "error": result.stderr or "Action failed"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Network Methods
    # =========================================================================

    def get_network_info(self) -> Dict[str, Any]:
        """Get network interface information including host network if available"""
        info = {
            "hostname": socket.gethostname(),
            "primary_ip": None,
            "host_ip": None,  # Host machine IP (for Docker scenarios)
            "interfaces": [],
            "host_interfaces": [],  # Host interfaces if detectable
            "connections": [],
            "listening_ports": [],  # Open/listening ports
            "gateway": None,
            "dns_servers": [],
            "is_containerized": os.path.exists("/.dockerenv"),
            "total_bytes_sent": 0,
            "total_bytes_recv": 0,
        }

        # Get primary IP (container IP in Docker)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            info["primary_ip"] = s.getsockname()[0]
            s.close()
        except Exception:
            info["primary_ip"] = "127.0.0.1"

        # Try to detect host IP from Docker host gateway
        try:
            # Docker typically uses 172.17.0.1 or 172.18.0.1 as gateway
            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                parts = result.stdout.split()
                if "via" in parts:
                    idx = parts.index("via")
                    if idx + 1 < len(parts):
                        info["gateway"] = parts[idx + 1]
        except Exception:
            pass

        # Try to get host network info via nsenter or docker host
        host_interfaces = self._get_host_network_info()
        if host_interfaces:
            info["host_interfaces"] = host_interfaces
            # Set host_ip - prioritize environment variable, then host.docker.internal
            for iface in host_interfaces:
                ip = iface.get("ip")
                if ip and not ip.startswith("127.") and not ip.startswith("172."):
                    # Prefer real external IPs (environment variable)
                    info["host_ip"] = ip
                    break
            # Fallback to any non-loopback if no external IP found
            if not info["host_ip"]:
                for iface in host_interfaces:
                    if iface.get("ip") and not iface["ip"].startswith("127."):
                        info["host_ip"] = iface["ip"]
                        break

        # Get DNS servers
        try:
            with open("/etc/resolv.conf", "r") as f:
                for line in f:
                    if line.startswith("nameserver"):
                        dns = line.split()[1]
                        if dns not in info["dns_servers"]:
                            info["dns_servers"].append(dns)
        except Exception:
            pass

        if self._psutil:
            # Get interface addresses
            addrs = self._psutil.net_if_addrs()
            stats = self._psutil.net_if_stats()
            io_counters = self._psutil.net_io_counters(pernic=True)

            for iface, addr_list in addrs.items():
                iface_info = {
                    "name": iface,
                    "ip": None,
                    "ipv6": None,
                    "netmask": None,
                    "mac": None,
                    "status": "down",
                    "speed_mbps": 0,
                    "mtu": 0,
                    "bytes_sent": 0,
                    "bytes_recv": 0,
                    "packets_sent": 0,
                    "packets_recv": 0,
                    "errors_in": 0,
                    "errors_out": 0,
                }

                for addr in addr_list:
                    if addr.family == socket.AF_INET:
                        iface_info["ip"] = addr.address
                        iface_info["netmask"] = addr.netmask
                    elif addr.family == socket.AF_INET6:
                        # Skip link-local IPv6
                        if not addr.address.startswith("fe80"):
                            iface_info["ipv6"] = addr.address
                    elif hasattr(socket, "AF_PACKET") and addr.family == socket.AF_PACKET:
                        iface_info["mac"] = addr.address

                if iface in stats:
                    iface_info["status"] = "up" if stats[iface].isup else "down"
                    iface_info["speed_mbps"] = stats[iface].speed
                    iface_info["mtu"] = stats[iface].mtu

                if iface in io_counters:
                    io = io_counters[iface]
                    iface_info["bytes_sent"] = io.bytes_sent
                    iface_info["bytes_recv"] = io.bytes_recv
                    iface_info["packets_sent"] = io.packets_sent
                    iface_info["packets_recv"] = io.packets_recv
                    iface_info["errors_in"] = io.errin
                    iface_info["errors_out"] = io.errout

                info["interfaces"].append(iface_info)

            # Calculate total bandwidth
            total_io = self._psutil.net_io_counters()
            info["total_bytes_sent"] = total_io.bytes_sent
            info["total_bytes_recv"] = total_io.bytes_recv

            # Get active connections (top 30)
            try:
                connections = self._psutil.net_connections(kind="inet")
                established = [c for c in connections if c.status == "ESTABLISHED"]
                for conn in established[:30]:
                    try:
                        # Try to get process name
                        proc_name = None
                        if conn.pid:
                            try:
                                proc_name = self._psutil.Process(conn.pid).name()
                            except Exception:
                                pass

                        info["connections"].append(
                            {
                                "local_addr": (f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else None),
                                "local_port": conn.laddr.port if conn.laddr else None,
                                "remote_addr": (f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else None),
                                "remote_ip": conn.raddr.ip if conn.raddr else None,
                                "remote_port": conn.raddr.port if conn.raddr else None,
                                "status": conn.status,
                                "pid": conn.pid,
                                "process": proc_name,
                            }
                        )
                    except Exception:
                        pass
            except Exception:
                pass

            # Get listening ports
            try:
                listening = [c for c in connections if c.status == "LISTEN"]
                seen_ports = set()
                for conn in listening:
                    try:
                        port = conn.laddr.port if conn.laddr else None
                        if port and port not in seen_ports:
                            seen_ports.add(port)
                            proc_name = None
                            if conn.pid:
                                try:
                                    proc_name = self._psutil.Process(conn.pid).name()
                                except Exception:
                                    pass

                            info["listening_ports"].append(
                                {
                                    "port": port,
                                    "ip": conn.laddr.ip if conn.laddr else "0.0.0.0",
                                    "pid": conn.pid,
                                    "process": proc_name,
                                    "protocol": "tcp",
                                }
                            )
                    except Exception:
                        pass

                # Sort by port number
                info["listening_ports"].sort(key=lambda x: x["port"])
            except Exception:
                pass

        return info

    def _get_host_network_info(self) -> List[Dict[str, Any]]:
        """
        Try to get host network information when running in Docker.
        This requires either:
        1. Docker socket access
        2. nsenter capability (privileged container)
        3. Host network mode
        """
        interfaces = []

        # Method 1: Try to read from proc if we have host access
        try:
            # This works if /host/proc is mounted
            if os.path.exists("/host/proc/net/route"):
                pass  # Could parse routing table
        except Exception:
            pass

        # Method 2: Try docker host.docker.internal
        try:
            host_ip = socket.gethostbyname("host.docker.internal")
            if host_ip:
                interfaces.append(
                    {
                        "name": "host",
                        "ip": host_ip,
                        "type": "docker-host",
                        "note": "Docker host gateway",
                    }
                )
        except Exception:
            pass

        # Method 3: Try to get from environment or Docker API
        host_ip_env = os.environ.get("HOST_IP")
        if host_ip_env:
            interfaces.append(
                {
                    "name": "host-env",
                    "ip": host_ip_env,
                    "type": "environment",
                    "note": "From HOST_IP environment variable",
                }
            )

        # Method 4: Try nsenter to host namespace (requires privileged)
        try:
            result = subprocess.run(
                ["nsenter", "-t", "1", "-n", "ip", "-4", "addr", "show"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                current_iface = None
                for line in result.stdout.split("\n"):
                    # Parse interface name
                    if line and not line.startswith(" "):
                        parts = line.split(":")
                        if len(parts) >= 2:
                            current_iface = parts[1].strip().split("@")[0]

                    # Parse IP address
                    elif "inet " in line and current_iface:
                        parts = line.strip().split()
                        if len(parts) >= 2:
                            ip_with_mask = parts[1]
                            ip = ip_with_mask.split("/")[0]
                            if not ip.startswith("127."):
                                interfaces.append(
                                    {
                                        "name": current_iface,
                                        "ip": ip,
                                        "cidr": ip_with_mask,
                                        "type": "host-nsenter",
                                        "note": "Host network (via nsenter)",
                                    }
                                )
        except Exception:
            pass

        return interfaces

    # =========================================================================
    # System Info Methods
    # =========================================================================

    def get_system_info(self) -> Dict[str, Any]:
        """Get general system information"""
        info = {
            "hostname": socket.gethostname(),
            "platform": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "uptime_seconds": 0,
            "boot_time": None,
            "distribution": None,
            "process_count": 0,
            "users_logged_in": 0,
        }

        if self._psutil:
            boot_time = self._psutil.boot_time()
            info["boot_time"] = datetime.fromtimestamp(boot_time).isoformat()
            info["uptime_seconds"] = int(time.time() - boot_time)
            info["process_count"] = len(self._psutil.pids())
            info["users_logged_in"] = len(self._psutil.users())

        # Get distribution info (Linux)
        try:
            if os.path.exists("/etc/os-release"):
                with open("/etc/os-release", "r") as f:
                    os_info = {}
                    for line in f:
                        if "=" in line:
                            key, value = line.strip().split("=", 1)
                            os_info[key] = value.strip('"')
                    info["distribution"] = os_info.get("PRETTY_NAME", os_info.get("NAME", "Unknown"))
        except Exception:
            pass

        return info

    # =========================================================================
    # Combined Overview
    # =========================================================================

    def get_overview(self) -> Dict[str, Any]:
        """Get complete system overview"""
        return {
            "cpu": self.get_cpu_info(),
            "memory": self.get_memory_info(),
            "disk": self.get_disk_info(),
            "gpu": self.get_gpu_info(),
            "network": self.get_network_info(),
            "system": self.get_system_info(),
            "timestamp": datetime.now().isoformat(),
        }


# Singleton instance
_service = None


def get_system_service() -> SystemService:
    """Get singleton instance of SystemService"""
    global _service
    if _service is None:
        _service = SystemService()
    return _service
