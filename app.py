"""PeerDrop LAN - simple peer-to-peer file transfer for PCs on the same Wi-Fi.

Run on every PC with:  python app.py
No cloud account or external server is required.
"""
from __future__ import annotations

import json
import hashlib
import os
import queue
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from tkinter import Tk, StringVar, BooleanVar, filedialog, messagebox
from tkinter import ttk

APP_NAME = "PeerDrop LAN"
DISCOVERY_PORT = 45871
TRANSFER_PORT = 45872
MAGIC = "PEERDROP1"
PROTOCOL_VERSION = 1
MAX_META = 64 * 1024
MAX_FILE_SIZE = 1024 * 1024 * 1024 * 1024  # 1 TB safety bound for malformed offers.
MAX_EXTRACTED_SIZE = 1024 * 1024 * 1024 * 1024
AUTO_ACCEPT_MAX_SIZE = 20 * 1024 * 1024 * 1024
MAX_PENDING_REQUESTS = 10
CHUNK_SIZE = 256 * 1024
CHUNK_SIZES = (64 * 1024, 128 * 1024, 256 * 1024, 512 * 1024, 1024 * 1024)
SETTINGS_FILE = Path(os.environ.get("APPDATA", str(Path.home()))) / "PeerDropLAN" / "settings.json"


def local_name() -> str:
    return socket.gethostname() or "This PC"


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def stable_fingerprint(identity: str) -> str:
    """A short, opaque device identifier safe to advertise on a local network."""
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def next_automatic_chunk(current: int, send_seconds: float) -> int:
    """Small, bounded adjustment for automatic mode; never exceeds safe presets."""
    index = CHUNK_SIZES.index(current)
    if send_seconds < 0.008 and index < len(CHUNK_SIZES) - 1:
        return CHUNK_SIZES[index + 1]
    if send_seconds > 0.15 and index > 0:
        return CHUNK_SIZES[index - 1]
    return current


def should_auto_accept(enabled: bool, size: int) -> bool:
    return enabled and size <= AUTO_ACCEPT_MAX_SIZE


def safe_filename(name: str) -> str:
    """Keep a received filename inside the Downloads destination."""
    name = Path(name).name.strip()
    return name or "received-file"


def unique_destination(folder: Path, filename: str) -> Path:
    target = folder / safe_filename(filename)
    stem, suffix = target.stem, target.suffix
    n = 1
    while target.exists():
        target = folder / f"{stem} ({n}){suffix}"
        n += 1
    return target


def create_folder_archive(folder: Path) -> Path:
    """Create a temporary archive with paths relative to the selected folder."""
    handle, temporary_name = tempfile.mkstemp(prefix="peerdrop-", suffix=".zip")
    os.close(handle)
    archive_path = Path(temporary_name)
    try:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for current, directories, filenames in os.walk(folder, followlinks=False):
                current_path = Path(current)
                relative = current_path.relative_to(folder)
                if not directories and not filenames:
                    archive.writestr(f"{relative.as_posix()}/", b"")
                for filename in filenames:
                    file_path = current_path / filename
                    if not file_path.is_symlink():
                        archive.write(file_path, file_path.relative_to(folder).as_posix())
        return archive_path
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise


def safe_extract_archive(archive_path: Path, destination: Path) -> None:
    """Extract with traversal, item-count, and expanded-size checks."""
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > 10000:
            raise ValueError("Folder archive contains too many items")
        expanded = 0
        for member in members:
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError("Folder archive contains an unsafe path")
            expanded += member.file_size
            if expanded > MAX_EXTRACTED_SIZE:
                raise ValueError("Expanded folder is too large")
        destination.mkdir(parents=True, exist_ok=False)
        archive.extractall(destination)


def pack_header(data: dict) -> bytes:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return struct.pack("!I", len(raw)) + raw


def recv_exact(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        block = sock.recv(count - len(data))
        if not block:
            raise ConnectionError("Connection closed unexpectedly")
        data.extend(block)
    return bytes(data)


def recv_header(sock: socket.socket) -> dict:
    size = struct.unpack("!I", recv_exact(sock, 4))[0]
    if not 0 < size <= MAX_META:
        raise ValueError("Invalid transfer metadata")
    return json.loads(recv_exact(sock, size).decode("utf-8"))


def format_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def parse_wifi_status(netsh_output: str) -> tuple[str, str]:
    """Return Windows netsh Wi-Fi connection state and SSID without shell parsing."""
    state = "Unavailable"
    ssid = ""
    for line in netsh_output.splitlines():
        match = re.match(r"^\s*State\s*:\s*(.+)$", line, re.IGNORECASE)
        if match:
            state = match.group(1).strip().title()
        match = re.match(r"^\s*SSID\s*:\s*(.+)$", line, re.IGNORECASE)
        if match and not line.lstrip().lower().startswith("bssid"):
            ssid = match.group(1).strip()
    return state, ssid


def preferred_address(addresses: list[str]) -> tuple[str | None, int]:
    """Prefer a normal home/office LAN address over VPN and virtual adapters."""
    usable = [address for address in addresses if not address.startswith(("127.", "169.254."))]
    private = []
    for address in usable:
        first, second, *_ = (int(part) for part in address.split("."))
        if first == 10 or (first == 172 and 16 <= second <= 31) or (first == 192 and second == 168):
            private.append(address)
    candidates = private or usable
    return (candidates[0], len(usable) - 1) if candidates else (None, 0)


def parse_ipconfig_interfaces(ipconfig_output: str) -> list[dict]:
    """Extract adapter names, IPv4 addresses and masks from Windows ipconfig output."""
    interfaces: list[dict] = []
    current: dict | None = None
    for line in ipconfig_output.splitlines():
        heading = re.match(r"^\s*(?:Wireless LAN|Ethernet|Unknown|PPP) adapter (.+):\s*$", line, re.IGNORECASE)
        if heading:
            current = {"name": heading.group(1).strip(), "address": None, "mask": None}
            interfaces.append(current)
            continue
        if not current:
            continue
        address = re.search(r"IPv4[^:]*:\s*([0-9]{1,3}(?:\.[0-9]{1,3}){3})", line, re.IGNORECASE)
        if address:
            current["address"] = address.group(1)
        mask = re.search(r"Subnet Mask[^:]*:\s*([0-9]{1,3}(?:\.[0-9]{1,3}){3})", line, re.IGNORECASE)
        if mask:
            current["mask"] = mask.group(1)
    return [item for item in interfaces if item["address"]]


def is_private_address(address: str) -> bool:
    first, second, *_ = (int(part) for part in address.split("."))
    return first == 10 or (first == 172 and 16 <= second <= 31) or (first == 192 and second == 168)


def directed_broadcast(address: str, mask: str) -> str | None:
    try:
        ip_number = struct.unpack("!I", socket.inet_aton(address))[0]
        mask_number = struct.unpack("!I", socket.inet_aton(mask))[0]
        return socket.inet_ntoa(struct.pack("!I", ip_number | (~mask_number & 0xFFFFFFFF)))
    except OSError:
        return None


def wifi_first_address(interfaces: list[dict]) -> tuple[str | None, int]:
    usable = [item for item in interfaces if not item["address"].startswith(("127.", "169.254."))]
    wifi = [item for item in usable if any(word in item["name"].lower() for word in ("wi-fi", "wifi", "wireless", "wlan"))]
    hotspot = [item for item in usable if "local area connection*" in item["name"].lower()]
    private = [item for item in usable if is_private_address(item["address"])]
    choice = (wifi or hotspot or private or usable)
    return (choice[0]["address"], len(usable) - 1) if choice else (None, 0)


def network_summary() -> str:
    """Collect a useful local-network status while still allowing wired connections."""
    try:
        result = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=4, check=False)
        wifi_state, ssid = parse_wifi_status(result.stdout)
    except (OSError, subprocess.SubprocessError):
        wifi_state, ssid = "Unavailable", ""
    interfaces: list[dict] = []
    try:
        result = subprocess.run(["ipconfig"], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=4, check=False)
        interfaces = parse_ipconfig_interfaces(result.stdout)
    except (OSError, subprocess.SubprocessError):
        pass
    primary, additional_count = wifi_first_address(interfaces)
    wifi_text = f"Wi-Fi: {wifi_state}" + (f" ({ssid})" if ssid and wifi_state.lower() == "connected" else "")
    address_text = primary or "No local IPv4 address detected"
    if primary and additional_count:
        address_text += f"  (+{additional_count} other adapter address{'es' if additional_count != 1 else ''})"
    return f"{wifi_text}  |  This PC IP: {address_text}"


def local_broadcast_targets() -> list[tuple[str, str]]:
    """Give each private adapter a directed broadcast, avoiding a wrong default route."""
    try:
        result = subprocess.run(["ipconfig"], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=4, check=False)
        interfaces = parse_ipconfig_interfaces(result.stdout)
    except (OSError, subprocess.SubprocessError):
        return []
    targets = []
    for interface in interfaces:
        address, mask = interface["address"], interface["mask"]
        broadcast = directed_broadcast(address, mask) if mask and is_private_address(address) else None
        if broadcast:
            targets.append((address, broadcast))
    return list(dict.fromkeys(targets))


class NetworkService:
    """UDP discovery plus a small TCP file-transfer protocol."""

    def __init__(self, event_queue: queue.Queue, device_name: str, receive_folder: Path,
                 transfer_port: int = TRANSFER_PORT, device_id: str | None = None,
                 chunk_size: int | None = CHUNK_SIZE):
        self.events = event_queue
        self.device_name = device_name
        self.receive_folder = receive_folder
        self.transfer_port = transfer_port
        self.chunk_size = chunk_size if chunk_size in CHUNK_SIZES or chunk_size is None else CHUNK_SIZE
        self.id = device_id or str(uuid.uuid4())
        self.running = threading.Event()
        self.running.set()
        self.peers: dict[str, dict] = {}
        self.pending: dict[str, dict] = {}
        self.pending_lock = threading.Lock()
        self.accept_lock = threading.Lock()
        self.auto_accept = False
        self.server_lock = threading.Lock()
        self.broadcast_enabled = True
        self.receiver_enabled = threading.Event()
        self.receiver_enabled.set()
        self.server: socket.socket | None = None

    def start(self) -> None:
        threading.Thread(target=self._announce_loop, daemon=True, name="discovery").start()
        threading.Thread(target=self._listen_discovery, daemon=True, name="discovery-listener").start()
        self._start_listener()

    def _start_listener(self) -> None:
        threading.Thread(target=self._listen_transfers, daemon=True, name="transfer-listener").start()

    def stop(self) -> None:
        self.running.clear()
        self.receiver_enabled.clear()
        with self.server_lock:
            server, self.server = self.server, None
        if server:
            try:
                server.close()
            except OSError:
                pass

    def set_receiving(self, enabled: bool) -> None:
        """Pause only new incoming transfers; active transfers are allowed to finish."""
        if enabled:
            self.receiver_enabled.set()
            with self.server_lock:
                should_start = self.server is None
            if should_start and self.running.is_set():
                self._start_listener()
            self.events.put(("receiver_state", True))
            return
        self.receiver_enabled.clear()
        with self.server_lock:
            server, self.server = self.server, None
        if server:
            try:
                server.close()
            except OSError:
                pass
        self.events.put(("receiver_state", False))

    def set_name(self, name: str) -> None:
        self.device_name = name.strip() or local_name()
        self._announce()

    def set_auto_accept(self, enabled: bool) -> None:
        with self.accept_lock:
            self.auto_accept = enabled

    def set_chunk_size(self, chunk_size: int | None) -> None:
        if chunk_size is not None and chunk_size not in CHUNK_SIZES:
            raise ValueError("Unsupported transfer chunk size")
        self.chunk_size = chunk_size

    def add_manual_peer(self, address: str) -> None:
        """Identify a PC by IP without treating that address as its identity."""
        host, _, supplied_port = address.strip().partition(":")
        if not host:
            self.events.put(("error", "Enter a PC IP address, for example 192.168.1.24."))
            return
        try:
            port = int(supplied_port) if supplied_port else self.transfer_port
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.events.put(("error", "Use a valid port number."))
            return
        self.events.put(("status", f"Identifying PC at {host}…"))
        threading.Thread(target=self._identify_manual_peer, args=(host, port), daemon=True).start()

    def _identify_manual_peer(self, host: str, port: int) -> None:
        try:
            with socket.create_connection((host, port), timeout=10) as sock:
                sock.settimeout(10)
                sock.sendall(pack_header({"magic": MAGIC, "type": "identity"}))
                reply = recv_header(sock)
            peer_id = reply.get("fingerprint")
            if reply.get("type") != "identity" or not isinstance(peer_id, str) or len(peer_id) < 10:
                raise ValueError("The PC did not provide a valid PeerDrop identity")
            new = peer_id not in self.peers
            self.peers[peer_id] = {"id": peer_id, "name": str(reply.get("name", "Unknown PC")),
                                   "host": host, "port": port, "seen": time.time(),
                                   "version": reply.get("version", 0),
                                   "capabilities": reply.get("capabilities", [])}
            self.events.put(("peer", self.peers[peer_id], new))
        except Exception as error:
            self.events.put(("error", f"Could not identify {host}: {error}"))

    def _announcement(self) -> bytes:
        return json.dumps({"magic": MAGIC, "version": PROTOCOL_VERSION, "fingerprint": self.id,
                           "name": self.device_name, "port": self.transfer_port,
                           "capabilities": ["direct-file", "approval", "multi-send"]}).encode("utf-8")

    def _announce(self) -> None:
        if not self.broadcast_enabled:
            return
        # Directed broadcasts make discovery work when the default route belongs
        # to WSL, Hyper-V, a VPN, or a different Wi-Fi network.
        targets: list[tuple[str | None, str]] = [(None, "255.255.255.255")]
        targets.extend(local_broadcast_targets())
        for source, destination in targets:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                try:
                    if source:
                        sock.bind((source, 0))
                    sock.sendto(self._announcement(), (destination, DISCOVERY_PORT))
                except OSError:
                    continue

    def _announce_loop(self) -> None:
        while self.running.is_set():
            self._announce()
            self._remove_stale_peers()
            time.sleep(3)

    def _remove_stale_peers(self) -> None:
        now = time.time()
        gone = [key for key, peer in self.peers.items()
                if not key.startswith("manual:") and now - peer["seen"] > 10]
        for key in gone:
            self.peers.pop(key, None)
            self.events.put(("peer_removed", key))

    def _listen_discovery(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", DISCOVERY_PORT))
            sock.settimeout(1)
            while self.running.is_set():
                try:
                    data, address = sock.recvfrom(4096)
                    packet = json.loads(data.decode("utf-8"))
                    # Accept old copies during upgrades; current copies always use
                    # the stable fingerprint field.
                    peer_id = packet.get("fingerprint") or packet.get("id")
                    if packet.get("magic") != MAGIC or not isinstance(peer_id, str) or peer_id == self.id:
                        continue
                    new = peer_id not in self.peers
                    if new and len(self.peers) >= 200:
                        continue
                    self.peers[peer_id] = {"id": peer_id, "name": packet.get("name", "Unknown PC"),
                                           "host": address[0], "port": int(packet.get("port", TRANSFER_PORT)),
                                           "seen": time.time(), "version": packet.get("version", 0),
                                           "capabilities": packet.get("capabilities", []),
                                           "legacy_identity": "fingerprint" not in packet}
                    self.events.put(("peer", self.peers[peer_id], new))
                    # Windows Mobile Hotspot can make UDP discovery one-way. A
                    # newly discovered PC therefore registers back over TCP.
                    if new:
                        threading.Thread(target=self._register_presence, args=(self.peers[peer_id],), daemon=True).start()
                except socket.timeout:
                    continue
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    continue
        finally:
            sock.close()

    def _register_presence(self, peer: dict) -> None:
        try:
            with socket.create_connection((peer["host"], peer["port"]), timeout=5) as sock:
                sock.sendall(pack_header({"magic": MAGIC, "type": "presence", "fingerprint": self.id,
                                          "name": self.device_name, "port": self.transfer_port,
                                          "version": PROTOCOL_VERSION,
                                          "capabilities": ["direct-file", "approval", "multi-send"]}))
        except OSError:
            # Broadcast discovery remains available; this is only a fallback.
            pass

    def _listen_transfers(self) -> None:
        if not self.receiver_enabled.is_set():
            return
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # On Windows, REUSEADDR can silently share a port with another listener.
        # Exclusive ownership ensures incoming files reach this app instance.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("", self.transfer_port))
            server.listen(5)
            server.settimeout(1)
            with self.server_lock:
                if not self.receiver_enabled.is_set() or not self.running.is_set():
                    return
                self.server = server
            self.events.put(("status", f"Ready on port {self.transfer_port}"))
            self.events.put(("receiver_state", True))
            while self.running.is_set() and self.receiver_enabled.is_set():
                try:
                    conn, address = server.accept()
                    threading.Thread(target=self._receive_connection, args=(conn, address), daemon=True).start()
                except socket.timeout:
                    continue
                except OSError:
                    break
        except OSError as error:
            if self.receiver_enabled.is_set() and self.running.is_set():
                self.events.put(("error", f"Could not start receiver: {error}"))
        finally:
            try:
                server.close()
            except OSError:
                pass
            with self.server_lock:
                if self.server is server:
                    self.server = None

    def _receive_connection(self, conn: socket.socket, address: tuple) -> None:
        token = str(uuid.uuid4())
        try:
            conn.settimeout(45)
            meta = recv_header(conn)
            if meta.get("magic") != MAGIC:
                raise ValueError("Not a PeerDrop transfer")
            if meta.get("type") == "identity":
                conn.sendall(pack_header({"type": "identity", "fingerprint": self.id, "name": self.device_name,
                                          "version": PROTOCOL_VERSION,
                                          "capabilities": ["direct-file", "approval", "multi-send"]}))
                conn.close()
                return
            if meta.get("type") == "presence":
                peer_id = meta.get("fingerprint")
                if not isinstance(peer_id, str) or len(peer_id) < 10 or peer_id == self.id:
                    raise ValueError("Invalid device presence")
                new = peer_id not in self.peers
                self.peers[peer_id] = {"id": peer_id, "name": str(meta.get("name", "Unknown PC")),
                                       "host": address[0], "port": int(meta.get("port", TRANSFER_PORT)),
                                       "seen": time.time(), "version": meta.get("version", 0),
                                       "capabilities": meta.get("capabilities", []), "legacy_identity": False}
                self.events.put(("peer", self.peers[peer_id], new))
                conn.close()
                return
            if meta.get("type") not in ("file", "folder"):
                raise ValueError("Not a PeerDrop transfer")
            size = int(meta["size"])
            if size < 0 or size > MAX_FILE_SIZE:
                raise ValueError("Invalid file size")
            request = {"token": token, "conn": conn, "address": address[0], "name": safe_filename(meta["name"]),
                       "size": size, "sender": str(meta.get("sender", "Unknown PC")),
                       "fingerprint": str(meta.get("sender_fingerprint", "unknown")),
                       "transfer_type": meta["type"]}
            with self.pending_lock:
                if len(self.pending) >= MAX_PENDING_REQUESTS:
                    raise ConnectionError("Receiver has too many pending transfer requests")
                self.pending[token] = request
            with self.accept_lock:
                auto_accept = self.auto_accept
            if should_auto_accept(auto_accept, size):
                self.events.put(("incoming_auto", request))
                self.respond_to_incoming(token, True)
            else:
                self.events.put(("incoming", request))
        except Exception as error:
            try:
                conn.sendall(pack_header({"accepted": False, "reason": str(error)}))
            except OSError:
                pass
            conn.close()

    def respond_to_incoming(self, token: str, accepted: bool) -> None:
        with self.pending_lock:
            request = self.pending.pop(token, None)
        if not request:
            return
        if not accepted:
            try:
                request["conn"].sendall(pack_header({"accepted": False, "reason": "Declined"}))
            except OSError:
                pass
            request["conn"].close()
            return
        try:
            self.receive_folder.mkdir(parents=True, exist_ok=True)
            free_space = shutil.disk_usage(self.receive_folder).free
            if free_space < request["size"]:
                request["conn"].sendall(pack_header({"accepted": False, "reason": "Not enough free disk space"}))
                request["conn"].close()
                self.events.put(("error", f"Not enough free disk space for {request['name']}."))
                return
        except OSError as error:
            request["conn"].close()
            self.events.put(("error", f"Could not access receive folder: {error}"))
            return
        threading.Thread(target=self._save_received_transfer, args=(request,), daemon=True).start()

    def _save_received_transfer(self, request: dict) -> None:
        conn, size = request["conn"], request["size"]
        chunk_size = self.chunk_size or CHUNK_SIZE
        destination: Path | None = None
        archive_path: Path | None = None
        try:
            self.receive_folder.mkdir(parents=True, exist_ok=True)
            if request["transfer_type"] == "folder":
                destination = unique_destination(self.receive_folder, request["name"])
                archive_path = unique_destination(self.receive_folder, f".{request['name']}.zip")
                write_path = archive_path
            else:
                destination = unique_destination(self.receive_folder, request["name"])
                write_path = destination
            conn.sendall(pack_header({"accepted": True}))
            received = 0
            with open(write_path, "wb") as stream:
                while received < size:
                    block = conn.recv(min(chunk_size, size - received))
                    if not block:
                        raise ConnectionError("Sender disconnected")
                    stream.write(block)
                    received += len(block)
                    self.events.put(("receive_progress", request["name"], received, size))
            if request["transfer_type"] == "folder":
                safe_extract_archive(archive_path, destination)
                archive_path.unlink(missing_ok=True)
            conn.sendall(pack_header({"completed": True}))
            self.events.put(("received", destination))
        except Exception as error:
            if destination and destination.exists():
                try:
                    if destination.is_dir():
                        shutil.rmtree(destination)
                    else:
                        destination.unlink()
                except OSError:
                    pass
            if archive_path:
                archive_path.unlink(missing_ok=True)
            try:
                conn.sendall(pack_header({"completed": False, "reason": str(error)}))
            except OSError:
                pass
            self.events.put(("error", f"Receiving {request['name']} failed: {error}"))
        finally:
            conn.close()

    def send_file(self, peer_id: str, path: Path) -> None:
        peer = self.peers.get(peer_id)
        if not peer:
            self.events.put(("error", "That device is no longer online."))
            return
        self.events.put(("sending", path.name, peer["name"]))
        threading.Thread(target=self._send_transfer_worker, args=(peer, path, "file", path.name), daemon=True).start()

    def send_folder(self, peer_id: str, folder: Path) -> None:
        peer = self.peers.get(peer_id)
        if not peer:
            self.events.put(("error", "That device is no longer online."))
            return
        self.events.put(("sending", f"{folder.name} (folder)", peer["name"]))
        threading.Thread(target=self._send_folder_worker, args=(peer, folder), daemon=True).start()

    def _send_folder_worker(self, peer: dict, folder: Path) -> None:
        archive_path: Path | None = None
        try:
            archive_path = create_folder_archive(folder)
            self._send_transfer_worker(peer, archive_path, "folder", folder.name)
        except Exception as error:
            self.events.put(("error", f"Preparing folder {folder.name} failed: {error}"))
        finally:
            if archive_path:
                archive_path.unlink(missing_ok=True)

    def _send_transfer_worker(self, peer: dict, path: Path, transfer_type: str, display_name: str) -> None:
        try:
            size = path.stat().st_size
            automatic_chunking = self.chunk_size is None
            chunk_size = self.chunk_size or CHUNK_SIZE
            with socket.create_connection((peer["host"], peer["port"]), timeout=15) as sock:
                # A person may reasonably take time to review an incoming-file prompt.
                sock.settimeout(300)
                sock.sendall(pack_header({"magic": MAGIC, "type": transfer_type, "name": display_name, "size": size,
                                          "sender": self.device_name, "sender_fingerprint": self.id}))
                reply = recv_header(sock)
                if not reply.get("accepted"):
                    raise PermissionError(reply.get("reason", "Transfer declined"))
                sent = 0
                with open(path, "rb") as stream:
                    while block := stream.read(chunk_size):
                        started = time.monotonic()
                        sock.sendall(block)
                        if automatic_chunking:
                            chunk_size = next_automatic_chunk(chunk_size, time.monotonic() - started)
                        sent += len(block)
                        self.events.put(("send_progress", display_name, sent, size, peer["name"]))
                sock.shutdown(socket.SHUT_WR)
                confirmation = recv_header(sock)
                if not confirmation.get("completed"):
                    raise IOError(confirmation.get("reason", "Receiver did not confirm the saved file"))
            self.events.put(("sent", display_name, peer["name"]))
        except Exception as error:
            self.events.put(("error", f"Sending {display_name} failed: {error}"))


class PeerDropApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("720x620")
        self.root.minsize(620, 500)
        self.events: queue.Queue = queue.Queue()
        self.settings = load_settings()
        device_identity = self.settings.get("device_id")
        if not isinstance(device_identity, str) or not device_identity:
            device_identity = str(uuid.uuid4())
            self.settings["device_id"] = device_identity
        device_id = stable_fingerprint(device_identity)
        stored_name = self.settings.get("nickname")
        self.device_name = StringVar(value=stored_name if isinstance(stored_name, str) and stored_name else local_name())
        stored_aliases = self.settings.get("peer_aliases", {})
        self.peer_aliases: dict[str, str] = stored_aliases if isinstance(stored_aliases, dict) else {}
        self.peer_alias = StringVar()
        stored_chunk = self.settings.get("chunk_size")
        self.chunk_size = stored_chunk if stored_chunk in CHUNK_SIZES or stored_chunk == "auto" else "auto"
        self.chunk_text = StringVar(value="Automatic (recommended)" if self.chunk_size == "auto" else f"{self.chunk_size // 1024} KB")
        try:
            save_settings(self.settings)
        except OSError:
            pass
        self.manual_address = StringVar()
        self.folder = Path.home() / "Downloads" / "PeerDrop"
        self.folder_text = StringVar(value=str(self.folder))
        self.status = StringVar(value="Starting…")
        self.network_text = StringVar(value="Checking network…")
        self.receiver_text = StringVar(value="Receiver is starting…")
        self.progress_text = StringVar(value="No active transfer")
        self.auto_accept = BooleanVar(value=False)
        self.service = NetworkService(self.events, self.device_name.get(), self.folder, device_id=device_id,
                                      chunk_size=None if self.chunk_size == "auto" else self.chunk_size)
        self._build()
        self.refresh_network()
        self.service.start()
        self.root.after(100, self._process_events)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self) -> None:
        pad = {"padx": 12, "pady": 7}
        top = ttk.Frame(self.root, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text=APP_NAME, font=("Segoe UI", 18, "bold")).pack(side="left")
        ttk.Label(top, textvariable=self.status).pack(side="right")

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.send_tab = ttk.Frame(self.tabs, padding=0)
        self.receive_tab = ttk.Frame(self.tabs, padding=0)
        self.network_tab = ttk.Frame(self.tabs, padding=0)
        self.tabs.add(self.send_tab, text="Send")
        self.tabs.add(self.receive_tab, text="Receive")
        self.tabs.add(self.network_tab, text="Network")

        settings = ttk.LabelFrame(self.network_tab, text="This PC on the mesh", padding=10)
        settings.pack(fill="x", pady=(0, 8))
        ttk.Label(settings, text="Nickname (shown to other PCs)").grid(row=0, column=0, sticky="w")
        name_entry = ttk.Entry(settings, textvariable=self.device_name, width=28)
        name_entry.grid(row=0, column=1, sticky="ew", **pad)
        name_entry.bind("<FocusOut>", lambda _e: self.save_local_name())
        ttk.Button(settings, text="Save nickname", command=self.save_local_name).grid(row=0, column=2)
        ttk.Label(settings, text="Save received files to").grid(row=1, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.folder_text).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(settings, text="Choose folder", command=self.choose_folder).grid(row=1, column=2)
        ttk.Label(settings, text="Add PC by IP").grid(row=3, column=0, sticky="w")
        manual = ttk.Entry(settings, textvariable=self.manual_address)
        manual.grid(row=3, column=1, sticky="ew", **pad)
        manual.bind("<Return>", lambda _e: self.add_manual_peer())
        ttk.Button(settings, text="Add", command=self.add_manual_peer).grid(row=3, column=2)
        network = ttk.Frame(settings)
        network.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(network, textvariable=self.network_text).pack(side="left", fill="x", expand=True)
        ttk.Button(network, text="Refresh network", command=self.refresh_network).pack(side="right")
        ttk.Button(network, text="Open Mobile Hotspot", command=self.open_hotspot_settings).pack(side="right", padx=(0, 7))
        ttk.Label(settings, text="Transfer chunk size").grid(row=5, column=0, sticky="w", pady=(8, 0))
        chunk_picker = ttk.Combobox(settings, textvariable=self.chunk_text, state="readonly",
                                    values=["Automatic (recommended)"] + [f"{size // 1024} KB" for size in CHUNK_SIZES], width=24)
        chunk_picker.grid(row=5, column=1, sticky="w", padx=12, pady=(8, 0))
        chunk_picker.bind("<<ComboboxSelected>>", self.update_chunk_size)
        ttk.Label(settings, text="Automatic adapts safely; lower can help unreliable links.").grid(row=5, column=2, sticky="w", pady=(8, 0))
        settings.columnconfigure(1, weight=1)

        devices = ttk.LabelFrame(self.send_tab, text="Mesh devices — select one or more PCs", padding=8)
        devices.pack(fill="both", expand=True, pady=(0, 8))
        self.tree = ttk.Treeview(devices, columns=("name", "shared_name", "fingerprint", "address"), show="headings", selectmode="extended", height=8)
        self.tree.heading("name", text="Your saved name")
        self.tree.heading("shared_name", text="PC nickname")
        self.tree.heading("fingerprint", text="Device ID")
        self.tree.heading("address", text="Address")
        self.tree.column("name", width=180)
        self.tree.column("shared_name", width=150)
        self.tree.column("fingerprint", width=105)
        self.tree.column("address", width=130)
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.load_selected_peer_alias)
        ttk.Scrollbar(devices, orient="vertical", command=self.tree.yview).pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=lambda first, last: None)

        actions = ttk.Frame(self.send_tab, padding=(0, 0, 0, 10))
        actions.pack(fill="x")
        ttk.Button(actions, text="Refresh devices", command=self.service._announce).pack(side="left")
        ttk.Button(actions, text="Save selected peer name", command=self.save_peer_alias).pack(side="left", padx=(8, 0))
        ttk.Entry(actions, textvariable=self.peer_alias, width=22).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Send file to selected PCs…", command=self.pick_and_send).pack(side="right")
        ttk.Button(actions, text="Send folder…", command=self.pick_and_send_folder).pack(side="right", padx=(0, 8))
        ttk.Label(actions, textvariable=self.progress_text).pack(side="right", padx=15)
        self.progress = ttk.Progressbar(self.send_tab, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 12))

        receiver = ttk.LabelFrame(self.receive_tab, text="Receiver", padding=10)
        receiver.pack(fill="x", pady=(0, 8))
        ttk.Label(receiver, textvariable=self.receiver_text).pack(side="left")
        self.receiver_button = ttk.Button(receiver, text="Pause listening", command=self.toggle_receiver)
        self.receiver_button.pack(side="right")
        receive_settings = ttk.Frame(self.receive_tab, padding=(10, 0, 10, 10))
        receive_settings.pack(fill="x")
        ttk.Label(receive_settings, text="Received files folder:").grid(row=0, column=0, sticky="w")
        ttk.Label(receive_settings, textvariable=self.folder_text).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(receive_settings, text="Choose folder", command=self.choose_folder).grid(row=0, column=2)
        ttk.Checkbutton(receive_settings, text="Accept transfers automatically (up to 20 GB)", variable=self.auto_accept,
                        command=self.update_auto_accept).grid(row=1, column=1, sticky="w", padx=8, pady=(6, 0))
        activity = ttk.LabelFrame(self.receive_tab, text="Transfer activity", padding=8)
        activity.pack(fill="both", expand=True)
        self.activity = ttk.Treeview(activity, columns=("time", "state", "details"), show="headings", height=4)
        self.activity.heading("time", text="Time")
        self.activity.heading("state", text="Status")
        self.activity.heading("details", text="Details")
        self.activity.column("time", width=80, stretch=False)
        self.activity.column("state", width=110, stretch=False)
        self.activity.column("details", width=480)
        self.activity.pack(fill="both", expand=True)

    def choose_folder(self) -> None:
        selection = filedialog.askdirectory(initialdir=self.folder)
        if selection:
            self.folder = Path(selection)
            self.folder_text.set(str(self.folder))
            self.service.receive_folder = self.folder

    def add_manual_peer(self) -> None:
        self.service.add_manual_peer(self.manual_address.get())

    def save_local_name(self) -> None:
        name = self.device_name.get().strip() or local_name()
        self.device_name.set(name)
        self.service.set_name(name)
        self.settings["nickname"] = name
        self._save_settings()

    def _save_settings(self) -> None:
        try:
            self.settings["peer_aliases"] = self.peer_aliases
            save_settings(self.settings)
        except OSError as error:
            self._add_activity("Failed", f"Could not save names: {error}")

    def _display_peer_name(self, peer: dict) -> str:
        return self.peer_aliases.get(peer["id"], "—")

    def _upsert_peer(self, peer: dict) -> None:
        values = (self._display_peer_name(peer), peer["name"], peer["id"][:10], peer["host"])
        if self.tree.exists(peer["id"]):
            self.tree.item(peer["id"], values=values)
        else:
            self.tree.insert("", "end", iid=peer["id"], values=values)

    def save_peer_alias(self) -> None:
        selected = self.tree.selection()
        if len(selected) != 1:
            messagebox.showinfo(APP_NAME, "Select exactly one PC to save its name.")
            return
        peer_id = selected[0]
        alias = self.peer_alias.get().strip()
        if alias:
            self.peer_aliases[peer_id] = alias
        else:
            self.peer_aliases.pop(peer_id, None)
        peer = self.service.peers.get(peer_id)
        if peer:
            self._upsert_peer(peer)
        self._save_settings()
        self._add_activity("Named", f"Saved name for {peer['name'] if peer else peer_id}: {alias or 'removed'}")

    def load_selected_peer_alias(self, _event=None) -> None:
        selected = self.tree.selection()
        self.peer_alias.set(self.peer_aliases.get(selected[0], "") if len(selected) == 1 else "")

    def refresh_network(self) -> None:
        self.network_text.set(network_summary())

    def toggle_receiver(self) -> None:
        self.service.set_receiving(not self.service.receiver_enabled.is_set())

    def update_auto_accept(self) -> None:
        enabled = bool(self.auto_accept.get())
        self.service.set_auto_accept(enabled)
        self._add_activity("Receiver", "Automatic acceptance enabled" if enabled else "Automatic acceptance disabled")

    def update_chunk_size(self, _event=None) -> None:
        try:
            if self.chunk_text.get().startswith("Automatic"):
                self.service.set_chunk_size(None)
                self.chunk_size = "auto"
                self.settings["chunk_size"] = "auto"
                self._save_settings()
                self._add_activity("Network", "Automatic transfer tuning enabled")
                return
            selected = int(self.chunk_text.get().split()[0]) * 1024
            self.service.set_chunk_size(selected)
            self.chunk_size = selected
            self.settings["chunk_size"] = selected
            self._save_settings()
            self._add_activity("Network", f"Transfer chunk size set to {selected // 1024} KB")
        except (ValueError, IndexError):
            self.chunk_text.set("Automatic (recommended)" if self.service.chunk_size is None else f"{self.service.chunk_size // 1024} KB")

    def open_hotspot_settings(self) -> None:
        try:
            os.startfile("ms-settings:network-mobilehotspot")
        except OSError as error:
            messagebox.showerror(APP_NAME, f"Could not open Windows Mobile Hotspot settings: {error}")

    def pick_and_send(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Select one or more nearby PCs first.")
            return
        filename = filedialog.askopenfilename(title="Choose a file to send")
        if filename:
            path = Path(filename)
            for peer_id in selected:
                self.service.send_file(peer_id, path)

    def pick_and_send_folder(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Select one or more nearby PCs first.")
            return
        folder = filedialog.askdirectory(title="Choose a folder to send")
        if folder:
            path = Path(folder)
            for peer_id in selected:
                self.service.send_folder(peer_id, path)

    def _set_progress(self, filename: str, current: int, total: int, prefix: str) -> None:
        percent = 100 if total == 0 else current * 100 / total
        self.progress["value"] = percent
        self.progress_text.set(f"{prefix} {filename}: {format_size(current)} / {format_size(total)}")

    def _add_activity(self, state: str, details: str) -> None:
        self.activity.insert("", 0, values=(time.strftime("%H:%M:%S"), state, details))
        entries = self.activity.get_children()
        for entry in entries[50:]:
            self.activity.delete(entry)

    def _process_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "peer":
                    peer, _new = event[1], event[2]
                    self._upsert_peer(peer)
                    self.status.set(f"{len(self.service.peers)} device(s) found")
                elif kind == "peer_removed":
                    if self.tree.exists(event[1]): self.tree.delete(event[1])
                    self.status.set(f"{len(self.service.peers)} device(s) found")
                elif kind == "status": self.status.set(event[1])
                elif kind == "sending":
                    self._add_activity("Sending", f"{event[1]} to {event[2]}")
                elif kind == "receiver_state":
                    if event[1]:
                        self.receiver_text.set(f"Listening for incoming files on port {TRANSFER_PORT}")
                        self.receiver_button.configure(text="Pause listening")
                    else:
                        self.receiver_text.set("Incoming transfers are paused")
                        self.receiver_button.configure(text="Start listening")
                elif kind == "send_progress": self._set_progress(event[1], event[2], event[3], f"Sending to {event[4]}")
                elif kind == "receive_progress": self._set_progress(event[1], event[2], event[3], "Receiving")
                elif kind == "sent":
                    self.progress["value"] = 100; self.progress_text.set(f"Sent {event[1]} to {event[2]}")
                    self._add_activity("Sent", f"{event[1]} to {event[2]}")
                    messagebox.showinfo(APP_NAME, f"Sent {event[1]} to {event[2]}.")
                elif kind == "received":
                    self.progress["value"] = 100; self.progress_text.set(f"Received {event[1].name}")
                    self._add_activity("Received", f"{event[1].name} saved to {event[1]}")
                    messagebox.showinfo(APP_NAME, f"Saved file to:\n{event[1]}")
                elif kind == "incoming": self._incoming(event[1])
                elif kind == "incoming_auto":
                    self._add_activity("Accepted", f"Automatically accepting {event[1]['name']} from {event[1]['sender']}")
                elif kind == "error":
                    self._add_activity("Failed", event[1])
                    messagebox.showerror(APP_NAME, event[1])
        except queue.Empty:
            pass
        self.root.after(100, self._process_events)

    def _incoming(self, request: dict) -> None:
        sender = f"{request['sender']} [{request['fingerprint'][:10]}]"
        item_type = "folder" if request["transfer_type"] == "folder" else "file"
        self._add_activity("Incoming", f"{sender} wants to send {item_type} {request['name']} ({format_size(request['size'])})")
        if self.auto_accept.get():
            self.service.respond_to_incoming(request["token"], True)
            return
        answer = messagebox.askyesno(APP_NAME, f"{sender} wants to send {item_type}:\n\n{request['name']} ({format_size(request['size'])})\n\nAccept?")
        self.service.respond_to_incoming(request["token"], answer)
        if not answer:
            self._add_activity("Declined", f"{request['name']} from {request['sender']}")

    def close(self) -> None:
        self.service.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = Tk()
    ttk.Style().theme_use("vista") if "vista" in ttk.Style().theme_names() else None
    PeerDropApp(root)
    root.mainloop()
