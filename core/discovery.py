"""
discovery.py — Peer Auto-Discovery for WiFi Direct / LAN

Uses multi-interface UDP broadcast beacons so that sending and receiving PCs
automatically discover each other regardless of which subnet or hotspot they are on.
"""

import socket
import json
import time
import logging
import threading
from typing import Callable, Dict, List, Optional
from core.network import get_all_adapters

logger = logging.getLogger(__name__)

DISCOVERY_PORT = 5002
BEACON_MAGIC = "WIFI_DIRECT_TRANSFER_V1"

class PeerBeacon:
    """Broadcasts presence across all local network subnets periodically."""
    def __init__(self, device_name: str, transfer_port: int = 5001, broadcast_interval: float = 1.2):
        self.device_name = device_name
        self.transfer_port = transfer_port
        self.interval = broadcast_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self._thread.start()
        logger.info("Peer beacon broadcaster started.")

    def stop(self):
        self._running = False
        logger.info("Peer beacon broadcaster stopped.")

    def _broadcast_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.5)

        payload = json.dumps({
            "magic": BEACON_MAGIC,
            "name": self.device_name,
            "port": self.transfer_port,
        }).encode("utf-8")

        while self._running:
            try:
                # 1. Global broadcast
                sock.sendto(payload, ("255.255.255.255", DISCOVERY_PORT))

                # 2. Targeted subnet broadcasts for all active adapters (e.g. 192.168.137.255)
                adapters = get_all_adapters()
                for a in adapters:
                    if a.get("broadcast") and a["type"] != "virtual":
                        try:
                            sock.sendto(payload, (a["broadcast"], DISCOVERY_PORT))
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"Broadcast error: {e}")

            time.sleep(self.interval)

        sock.close()


class PeerListener:
    """Listens for peer beacons and notifies callback of discovered peers."""
    def __init__(self, on_peer_found: Optional[Callable[[Dict[str, str]], None]] = None):
        self.on_peer_found = on_peer_found
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.discovered_peers: Dict[str, Dict[str, str]] = {}  # ip -> {name, ip, port, last_seen}

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info(f"Peer discovery listener started on port {DISCOVERY_PORT}")

    def stop(self):
        self._running = False

    def _listen_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", DISCOVERY_PORT))
        except OSError as e:
            logger.warning(f"Could not bind discovery port {DISCOVERY_PORT}: {e}")
            return

        sock.settimeout(1.0)

        while self._running:
            try:
                data, addr = sock.recvfrom(2048)
                ip = addr[0]
                info = json.loads(data.decode("utf-8"))
                if info.get("magic") == BEACON_MAGIC:
                    peer_info = {
                        "name": info.get("name", "Unknown PC"),
                        "ip": ip,
                        "port": str(info.get("port", 5001)),
                        "last_seen": time.time(),
                    }
                    self.discovered_peers[ip] = peer_info
                    if self.on_peer_found:
                        self.on_peer_found(peer_info)
            except (socket.timeout, json.JSONDecodeError, UnicodeDecodeError):
                continue
            except OSError:
                break

        sock.close()
