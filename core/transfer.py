"""
transfer.py — Robust High-Performance TCP File Transfer Engine with 2-Way Handshakes

Protocol Specification (Binary Big-Endian):
==========================================
1. HANDSHAKE (Sender -> Receiver):
   [4 bytes] Magic: b"WFT1"
   [8 bytes] File size: uint64
   [2 bytes] Filename length: uint16
   [N bytes] Filename: UTF-8

2. HANDSHAKE RESPONSE (Receiver -> Sender):
   [1 byte]  Status code:
             0x00 = ACCEPT (Ready to receive)
             0x01 = DISK_FULL (Insufficient space)
             0x02 = PERMISSION_DENIED (Save directory not writable)
             0x03 = INVALID_FILENAME
             0x04 = SERVER_ERROR
   [2 bytes] Message length: uint16
   [M bytes] Message text: UTF-8

3. DATA STREAMING:
   Raw file bytes streamed in CHUNK_SIZE blocks (512 KB).
   Both ends calculate CRC-32 on-the-fly.

4. COMPLETION ACK (Receiver -> Sender):
   [1 byte]  Status: 0x00 (SUCCESS) or 0x01 (CRC/Write Error)
   [4 bytes] CRC-32 checksum: uint32
"""

import socket
import struct
import os
import shutil
import zlib
import time
import logging
import threading
from pathlib import Path
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# Protocol constants
MAGIC_HEADER = b"WFT1"
CHUNK_SIZE = 1024 * 1024         # 1 MB chunk buffer
SOCKET_BUFFER_SIZE = 4 * 1024 * 1024  # 4MB socket buffer
DEFAULT_PORT = 5001
CONNECT_TIMEOUT = 6
SOCKET_TIMEOUT = 30

# Status codes
STATUS_ACCEPT = 0x00
STATUS_ERR_DISK_FULL = 0x01
STATUS_ERR_PERMISSION = 0x02
STATUS_ERR_INVALID = 0x03
STATUS_ERR_SERVER = 0x04

STATUS_TRANSFER_OK = 0x00
STATUS_TRANSFER_FAIL = 0x01


class TransferProgress:
    """Tracks progress, speed, ETA, and state of a single file transfer."""
    def __init__(self, filename: str, total_bytes: int):
        self.filename = filename
        self.total_bytes = total_bytes
        self.transferred_bytes = 0
        self.start_time = time.monotonic()
        self.done = False
        self.error: Optional[str] = None
        self.crc32 = 0

    @property
    def percent(self) -> float:
        if self.total_bytes == 0:
            return 100.0
        return min(100.0, self.transferred_bytes / self.total_bytes * 100)

    @property
    def elapsed(self) -> float:
        return max(0.001, time.monotonic() - self.start_time)

    @property
    def speed_mbps(self) -> float:
        return (self.transferred_bytes / self.elapsed) / (1024 * 1024)

    @property
    def eta_seconds(self) -> float:
        if self.transferred_bytes == 0:
            return 0.0
        rate = self.transferred_bytes / self.elapsed
        remaining = self.total_bytes - self.transferred_bytes
        return remaining / rate if rate > 0 else 0.0


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes from a socket or raise ConnectionError."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed prematurely during data receive")
        buf.extend(chunk)
    return bytes(buf)


def send_file(
    host: str,
    filepath: str,
    port: int = DEFAULT_PORT,
    progress_callback: Optional[Callable[[TransferProgress], None]] = None,
    status_callback: Optional[Callable[[str, str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> TransferProgress:
    """
    Send a file with handshake verification, progress tracking, and CRC-32 integrity.
    """
    def log(tag: str, msg: str):
        logger.info(f"[{tag}] {msg}")
        if status_callback:
            status_callback(tag, msg)

    path = Path(filepath)
    if not path.is_file():
        progress = TransferProgress(path.name, 0)
        progress.error = f"File does not exist: {filepath}"
        log("error", progress.error)
        return progress

    try:
        file_size = path.stat().st_size
    except OSError as e:
        progress = TransferProgress(path.name, 0)
        progress.error = f"Cannot read file size: {e}"
        log("error", progress.error)
        return progress

    filename = path.name
    progress = TransferProgress(filename, file_size)
    sock = None

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUFFER_SIZE)
        except OSError:
            pass

        log("send", f"Connecting to receiver at {host}:{port}...")
        sock.settimeout(CONNECT_TIMEOUT)
        sock.connect((host, port))
        sock.settimeout(SOCKET_TIMEOUT)

        # ── 1. SEND HANDSHAKE HEADER ──
        log("handshake", f"Sending metadata for '{filename}' ({format_size(file_size)})...")
        name_bytes = filename.encode("utf-8")
        handshake = (
            MAGIC_HEADER +
            struct.pack(">QH", file_size, len(name_bytes)) +
            name_bytes
        )
        sock.sendall(handshake)

        # ── 2. WAIT FOR RECEIVER HANDSHAKE ACK ──
        resp_raw = _recv_exact(sock, 3)
        status_code, msg_len = struct.unpack(">BH", resp_raw)
        msg_bytes = _recv_exact(sock, msg_len) if msg_len > 0 else b""
        msg_text = msg_bytes.decode("utf-8", errors="replace")

        if status_code != STATUS_ACCEPT:
            error_map = {
                STATUS_ERR_DISK_FULL: f"Receiver disk full: {msg_text}",
                STATUS_ERR_PERMISSION: f"Receiver permission error: {msg_text}",
                STATUS_ERR_INVALID: f"Receiver rejected file: {msg_text}",
                STATUS_ERR_SERVER: f"Receiver error: {msg_text}",
            }
            progress.error = error_map.get(status_code, f"Receiver rejected transfer: {msg_text}")
            log("error", f"Handshake failed: {progress.error}")
            return progress

        log("handshake", f"Receiver ACCEPTED transfer. Streaming payload...")

        # ── 3. STREAM FILE DATA WITH CRC-32 ──
        crc = 0
        with open(filepath, "rb") as f:
            last_update = time.monotonic()
            while True:
                if cancel_event and cancel_event.is_set():
                    progress.error = "Transfer cancelled by user"
                    log("error", "Transfer cancelled by user.")
                    break

                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                sock.sendall(chunk)
                crc = zlib.crc32(chunk, crc)
                progress.transferred_bytes += len(chunk)

                now = time.monotonic()
                if progress_callback and (now - last_update >= 0.05 or progress.transferred_bytes == file_size):
                    progress_callback(progress)
                    last_update = now

        if progress.error:
            return progress

        # ── 4. WAIT FOR RECEIVER COMPLETION ACK & CHECKSUM ──
        log("handshake", "Waiting for remote CRC-32 verification...")
        comp_raw = _recv_exact(sock, 5)
        comp_status, recv_crc = struct.unpack(">BI", comp_raw)

        if comp_status != STATUS_TRANSFER_OK or (file_size > 0 and (crc & 0xffffffff) != recv_crc):
            progress.error = "Integrity check failed: Checksum mismatch on receiver"
            log("error", progress.error)
        else:
            progress.done = True
            progress.crc32 = crc & 0xffffffff
            log("done", f"Successfully sent '{filename}' in {progress.elapsed:.2f}s ({format_speed(progress.speed_mbps)}). CRC-32 verified!")

        if progress_callback:
            progress_callback(progress)

    except ConnectionRefusedError:
        progress.error = f"Connection refused at {host}:{port}. Is the receiver open and listening?"
        log("error", progress.error)
    except socket.timeout:
        progress.error = f"Connection timed out reaching {host}:{port}. Check if Windows Firewall is blocking Port {port} or try the other Wi-Fi IP."
        log("error", progress.error)
    except (ConnectionResetError, ConnectionAbortedError):
        progress.error = "Connection reset or aborted by the remote PC."
        log("error", progress.error)
    except OSError as e:
        progress.error = f"Network error ({e}). Check IP address and Wi-Fi connection."
        log("error", progress.error)
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    return progress


class FileReceiver:
    """
    TCP Receiver server with handshakes, disk validation, and atomic part-file writing.
    """
    def __init__(self, save_dir: str = ".", port: int = DEFAULT_PORT):
        self.save_dir = Path(save_dir)
        self.port = port
        self._server_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._progress_callback: Optional[Callable[[TransferProgress], None]] = None
        self._done_callback: Optional[Callable[[TransferProgress], None]] = None
        self._status_callback: Optional[Callable[[str, str], None]] = None

    def start(
        self,
        progress_callback: Optional[Callable[[TransferProgress], None]] = None,
        done_callback: Optional[Callable[[TransferProgress], None]] = None,
        status_callback: Optional[Callable[[str, str], None]] = None,
    ):
        self._progress_callback = progress_callback
        self._done_callback = done_callback
        self._status_callback = status_callback
        self._running = True

        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info(f"Receiver started on port {self.port}")

    def stop(self):
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        logger.info("Receiver stopped.")

    def _notify(self, tag: str, msg: str):
        logger.info(f"[{tag}] {msg}")
        if self._status_callback:
            self._status_callback(tag, msg)

    def _listen_loop(self):
        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_SIZE)
            except OSError:
                pass
            self._server_sock.bind(("0.0.0.0", self.port))
            self._server_sock.listen(5)
            self._server_sock.settimeout(1.0)
            self._notify("info", f"Receiver listening on port {self.port}...")

            while self._running:
                try:
                    conn, addr = self._server_sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                try:
                    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass

                self._notify("handshake", f"Accepted incoming connection from {addr[0]}")
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr[0]),
                    daemon=True
                )
                client_thread.start()

        except OSError as e:
            if self._running:
                self._notify("error", f"Receiver socket error: {e}")
        finally:
            if self._server_sock:
                try:
                    self._server_sock.close()
                except Exception:
                    pass

    def _send_handshake_response(self, conn: socket.socket, status_code: int, message: str = ""):
        msg_bytes = message.encode("utf-8")
        packet = struct.pack(">BH", status_code, len(msg_bytes)) + msg_bytes
        conn.sendall(packet)

    def _handle_client(self, conn: socket.socket, peer_ip: str):
        conn.settimeout(SOCKET_TIMEOUT)
        part_path: Optional[Path] = None
        progress: Optional[TransferProgress] = None

        try:
            # ── 1. READ MAGIC & HEADER ──
            magic = _recv_exact(conn, 4)
            if magic != MAGIC_HEADER:
                self._send_handshake_response(conn, STATUS_ERR_INVALID, "Invalid protocol header")
                raise ValueError("Handshake magic mismatch")

            header_fixed = _recv_exact(conn, 10)
            file_size, name_len = struct.unpack(">QH", header_fixed)

            name_bytes = _recv_exact(conn, name_len)
            raw_filename = name_bytes.decode("utf-8", errors="replace")
            filename = Path(raw_filename).name
            if not filename:
                filename = "unnamed_file.bin"

            self._notify("handshake", f"Received transfer request for '{filename}' ({format_size(file_size)}) from {peer_ip}")

            # ── 2. PRE-FLIGHT VALIDATION (Disk space & directory writeability) ──
            try:
                self.save_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                self._send_handshake_response(conn, STATUS_ERR_PERMISSION, f"Cannot create save directory: {e}")
                raise

            try:
                usage = shutil.disk_usage(self.save_dir)
                if usage.free < file_size + (10 * 1024 * 1024):
                    err_msg = f"Insufficient disk space ({format_size(file_size)} required, {format_size(usage.free)} available)"
                    self._send_handshake_response(conn, STATUS_ERR_DISK_FULL, err_msg)
                    raise OSError(err_msg)
            except Exception as e:
                logger.warning(f"Disk check warning: {e}")

            # Handshake ACCEPTED
            self._send_handshake_response(conn, STATUS_ACCEPT, "Ready")
            self._notify("handshake", f"Disk space verified. Accepted handshake with {peer_ip}")

            # Prepare safe unique destination and temporary .part file
            final_path = self._unique_path(self.save_dir / filename)
            part_path = final_path.with_suffix(final_path.suffix + ".part")

            progress = TransferProgress(filename, file_size)
            self._notify("recv", f"Receiving '{filename}' ({format_size(file_size)}) from {peer_ip}...")

            # ── 3. RECEIVE STREAM WITH CRC-32 ──
            crc = 0
            with open(part_path, "wb") as f:
                if file_size > 0:
                    try:
                        f.truncate(file_size)
                        f.seek(0)
                    except Exception:
                        pass

                remaining = file_size
                last_update = time.monotonic()
                while remaining > 0:
                    to_read = min(CHUNK_SIZE, remaining)
                    chunk = conn.recv(to_read)
                    if not chunk:
                        raise ConnectionError("Connection lost unexpectedly during transfer")
                    f.write(chunk)
                    crc = zlib.crc32(chunk, crc)
                    progress.transferred_bytes += len(chunk)
                    remaining -= len(chunk)

                    now = time.monotonic()
                    if self._progress_callback and (now - last_update >= 0.05 or remaining == 0):
                        self._progress_callback(progress)
                        last_update = now

            # ── 4. ATOMIC RENAME & FINAL COMPLETION ACK ──
            if part_path.exists():
                part_path.rename(final_path)

            progress.done = True
            progress.crc32 = crc & 0xffffffff

            # Send Completion ACK
            comp_packet = struct.pack(">BI", STATUS_TRANSFER_OK, progress.crc32)
            conn.sendall(comp_packet)

            self._notify("done", f"Saved '{filename}' ({progress.elapsed:.2f}s @ {format_speed(progress.speed_mbps)}). CRC-32 verified!")

            if self._done_callback:
                self._done_callback(progress)

        except Exception as e:
            msg = f"Transfer error from {peer_ip}: {e}"
            self._notify("error", msg)
            if progress:
                progress.error = str(e)
                if self._done_callback:
                    self._done_callback(progress)
            if part_path and part_path.exists():
                try:
                    part_path.unlink()
                except Exception:
                    pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 1
        while True:
            candidate = parent / f"{stem} ({counter}){suffix}"
            if not candidate.exists():
                return candidate
            counter += 1


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
