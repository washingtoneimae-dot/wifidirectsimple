"""
network.py — Network adapter discovery, classification, and IP enumeration helpers.

Identifies physical Wi-Fi, Hotspot, Ethernet, and Virtual (WSL, Hyper-V, VMware) adapters
to ensure users always select the reachable physical IP address.
"""

import subprocess
import socket
import re
import ipaddress
import logging
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)


def get_all_adapters() -> List[Dict[str, str]]:
    """
    Parse ipconfig output and classify adapters by type and priority:
      - 'hotspot'   : Windows Mobile Hotspot / Wi-Fi Direct virtual adapter (192.168.137.x)
      - 'wifi'      : Physical Wireless Wi-Fi adapter
      - 'ethernet'  : Physical Ethernet adapter
      - 'virtual'   : WSL, Hyper-V, VirtualBox, VMware, Docker (unreachable from other physical PCs)
    """
    adapters = []
    try:
        result = subprocess.run(
            ["ipconfig", "/all"],
            capture_output=True, text=True, timeout=8
        )
        output = result.stdout

        blocks = re.split(r'\n(?=\S)', output)
        for block in blocks:
            lines = block.strip().split('\n')
            if not lines:
                continue
            name_line = lines[0].strip().rstrip(':')

            ip = None
            mask = "255.255.255.0"
            for line in lines[1:]:
                m = re.search(r'IPv4 Address.*?:\s*([\d.]+)', line)
                if m:
                    ip_candidate = m.group(1).strip()
                    if ip_candidate.startswith('127.') or ip_candidate.startswith('169.254.'):
                        continue
                    ip = ip_candidate

                m_mask = re.search(r'Subnet Mask.*?:\s*([\d.]+)', line)
                if m_mask:
                    mask = m_mask.group(1).strip()

            if ip:
                name_lower = name_line.lower()

                # Detect Virtual / WSL / Docker / VM adapters
                if any(v in name_lower for v in [
                    'vethernet', 'wsl', 'hyper-v', 'virtualbox', 'vmware',
                    'docker', 'bluetooth', 'loopback', 'npcap', 'tap-'
                ]):
                    atype = 'virtual'
                # Detect Hotspot / Wi-Fi Direct adapter
                elif ip.startswith('192.168.137.') or 'wi-fi direct' in name_lower or 'local area connection*' in name_lower:
                    atype = 'hotspot'
                # Detect Physical Wi-Fi
                elif any(w in name_lower for w in ['wi-fi', 'wireless', 'wlan', '802.11']):
                    atype = 'wifi'
                # Detect Physical Ethernet
                elif any(e in name_lower for e in ['ethernet', 'gigabit', 'realtek', 'intel', 'lan']):
                    atype = 'ethernet'
                else:
                    atype = 'other'

                # Calculate broadcast address for this subnet
                try:
                    net = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
                    broadcast = str(net.broadcast_address)
                except Exception:
                    broadcast = "255.255.255.255"

                adapters.append({
                    "name": name_line,
                    "ip": ip,
                    "mask": mask,
                    "broadcast": broadcast,
                    "type": atype
                })
    except Exception as e:
        logger.warning(f"get_all_adapters error: {e}")

    # Sort adapters by priority: hotspot > wifi > ethernet > other > virtual
    priority_order = {"hotspot": 0, "wifi": 1, "ethernet": 2, "other": 3, "virtual": 4}
    adapters.sort(key=lambda a: priority_order.get(a["type"], 5))
    return adapters


def get_physical_ips() -> List[Dict[str, str]]:
    """Returns only reachable physical adapters (Hotspot, Wi-Fi, Ethernet)."""
    return [a for a in get_all_adapters() if a["type"] != "virtual"]


def get_best_transfer_ip() -> Optional[str]:
    """Return the most suitable primary IP for peer connections."""
    adapters = get_all_adapters()
    for atype in ("hotspot", "wifi", "ethernet", "other"):
        for adapter in adapters:
            if adapter["type"] == atype:
                return adapter["ip"]
    return adapters[0]["ip"] if adapters else None


def get_all_local_ips() -> List[str]:
    """Return physical local IPv4 addresses (excluding virtual/WSL by default)."""
    physical = [a["ip"] for a in get_physical_adapters()]
    if physical:
        return physical
    return [a["ip"] for a in get_all_adapters()]


def get_physical_adapters() -> List[Dict[str, str]]:
    adapters = get_all_adapters()
    phys = [a for a in adapters if a["type"] in ("hotspot", "wifi", "ethernet")]
    return phys if phys else adapters


def format_size(n_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n_bytes < 1024 or unit == "TB":
            if unit == "B":
                return f"{n_bytes} {unit}"
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"


def format_speed(mb_per_sec: float) -> str:
    if mb_per_sec >= 1000:
        return f"{mb_per_sec/1000:.2f} GB/s"
    if mb_per_sec >= 1:
        return f"{mb_per_sec:.1f} MB/s"
    return f"{mb_per_sec*1024:.0f} KB/s"


def format_eta(seconds: float) -> str:
    if seconds <= 0 or seconds > 86400:
        return "--"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"
