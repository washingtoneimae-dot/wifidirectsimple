"""
wifi_direct.py — WiFi Direct Legacy AP (Group Owner / Hotspot mode)

Uses WinRT Windows.Devices.WiFiDirect to start a WiFi Direct hotspot
that any WiFi device (not just WiFi Direct peers) can connect to.

The host PC advertises itself as a Group Owner with a custom SSID/passphrase.
Once the remote PC connects to that SSID, both machines share a private
subnet on the virtual WiFi Direct adapter, enabling TCP file transfer.
"""

import asyncio
import random
import string
import logging
import subprocess
import re
import threading
from typing import Optional, Callable, Tuple

logger = logging.getLogger(__name__)

WIFIDIRECT_AVAILABLE = False

try:
    from winrt.windows.devices.wifidirect import (
        WiFiDirectAdvertisementPublisher,
        WiFiDirectAdvertisementPublisherStatus,
        WiFiDirectConnectionListener,
        WiFiDirectAdvertisementListenStateDiscoverability,
    )
    from winrt.windows.security.credentials import PasswordCredential
    WIFIDIRECT_AVAILABLE = True
    logger.info("WinRT WiFiDirect APIs available.")
except ImportError:
    logger.warning("WinRT WiFiDirect not available. Install: pip install winrt-Windows.Devices.WiFiDirect")


def _random_passphrase(length: int = 12) -> str:
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))


def _random_ssid() -> str:
    suffix = ''.join(random.choices(string.digits, k=4))
    return f"WD-Transfer-{suffix}"


class WiFiDirectHost:
    """
    Manages a WiFi Direct Legacy AP (softAP / Group Owner).

    Usage:
        host = WiFiDirectHost()
        ssid, pw = await host.start()
        # ... wait for transfers ...
        await host.stop()
    """

    def __init__(self, status_callback: Optional[Callable[[str], None]] = None):
        self._publisher: Optional[object] = None
        self._listener: Optional[object] = None
        self._ssid: str = ""
        self._passphrase: str = ""
        self._status_callback = status_callback
        self._running = False
        self._token_status = None
        self._token_connection = None

    def _notify(self, msg: str):
        logger.info(msg)
        if self._status_callback:
            self._status_callback(msg)

    async def start(self) -> Tuple[str, str]:
        """
        Start the WiFi Direct Legacy AP.
        Returns (ssid, passphrase) on success.
        Raises RuntimeError if WiFiDirect APIs are not available.
        """
        if not WIFIDIRECT_AVAILABLE:
            raise RuntimeError(
                "WinRT WiFiDirect APIs not installed.\n"
                "Run: pip install winrt-Windows.Devices.WiFiDirect"
            )

        self._ssid = _random_ssid()
        self._passphrase = _random_passphrase()

        self._publisher = WiFiDirectAdvertisementPublisher()

        # Configure Legacy Settings so any device can connect
        adv = self._publisher.advertisement
        adv.listen_state_discoverability = (
            WiFiDirectAdvertisementListenStateDiscoverability.NORMAL
        )
        adv.is_autonomous_group_owner_enabled = True

        legacy = adv.legacy_settings
        legacy.is_enabled = True
        legacy.ssid = self._ssid

        cred = PasswordCredential()
        cred.password = self._passphrase
        legacy.passphrase = cred

        # Status change handler
        def on_status_changed(sender, args):
            status = sender.status
            if status == WiFiDirectAdvertisementPublisherStatus.STARTED:
                self._notify(f"Hotspot started: SSID={self._ssid}")
            elif status == WiFiDirectAdvertisementPublisherStatus.STOPPED:
                self._notify("Hotspot stopped.")
            elif status == WiFiDirectAdvertisementPublisherStatus.ABORTED:
                self._notify("Hotspot aborted (hardware error or conflict).")

        self._token_status = self._publisher.add_status_changed(on_status_changed)

        # Connection listener
        self._listener = WiFiDirectConnectionListener()

        def on_connection_requested(sender, args):
            self._notify(f"Incoming connection request from a peer.")

        self._token_connection = self._listener.add_connection_requested(
            on_connection_requested
        )

        self._publisher.start()
        self._running = True
        self._notify(f"WiFi Direct hotspot started. SSID: {self._ssid}")
        return self._ssid, self._passphrase

    async def stop(self):
        """Stop the WiFi Direct hotspot."""
        if self._publisher and self._running:
            try:
                if self._token_status is not None:
                    self._publisher.remove_status_changed(self._token_status)
                self._publisher.stop()
            except Exception as e:
                logger.warning(f"Error stopping publisher: {e}")
            self._running = False
            self._notify("WiFi Direct hotspot stopped.")

    @property
    def ssid(self) -> str:
        return self._ssid

    @property
    def passphrase(self) -> str:
        return self._passphrase

    @property
    def is_running(self) -> bool:
        return self._running


def check_wifi_direct_support() -> Tuple[bool, str]:
    """
    Check if the WiFi adapter supports WiFi Direct via netsh.
    Returns (supported: bool, message: str).
    """
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "wirelesscapabilities"],
            capture_output=True, text=True, timeout=5
        )
        output = result.stdout + result.stderr
        # Look for WiFi Direct support indicators
        if "Wi-Fi Direct" in output or "WiFi-Direct" in output or "Hosted network" in output:
            # Parse supported roles
            go_match = re.search(r"Group Owner.*?:\s*(\w+)", output, re.IGNORECASE)
            client_match = re.search(r"Client.*?:\s*(\w+)", output, re.IGNORECASE)
            go_supported = go_match and "supported" in go_match.group(1).lower()
            return True, output
        return False, output
    except Exception as e:
        return False, str(e)


def get_wifidirect_adapter_ip() -> Optional[str]:
    """
    Find the IP address assigned to the virtual WiFi Direct adapter.
    Returns IP string or None.
    """
    try:
        result = subprocess.run(
            ["ipconfig"],
            capture_output=True, text=True, timeout=5
        )
        output = result.stdout
        # Find the WiFi Direct adapter section
        lines = output.split('\n')
        in_wifidirect = False
        for line in lines:
            if 'Wi-Fi Direct' in line or 'WiFi-Direct' in line or 'Local Area Connection* ' in line:
                in_wifidirect = True
            elif in_wifidirect:
                ip_match = re.search(r'IPv4 Address.*?:\s*([\d.]+)', line)
                if ip_match:
                    return ip_match.group(1)
                # Stop at next adapter section
                if line.strip() and not line.startswith(' '):
                    in_wifidirect = False
    except Exception as e:
        logger.warning(f"Could not determine WiFi Direct adapter IP: {e}")
    return None


def get_all_local_ips() -> list:
    """Get all local IPv4 addresses for display to the user."""
    import socket
    ips = []
    try:
        hostname = socket.gethostname()
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for info in addr_info:
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    # Also try the primary interface
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if ip not in ips:
            ips.append(ip)
        s.close()
    except Exception:
        pass
    return ips
