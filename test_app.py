import json
import socket
import queue
import tempfile
import time
import unittest
from pathlib import Path

from app import (AUTO_ACCEPT_MAX_SIZE, CHUNK_SIZES, MAX_FILE_SIZE, NetworkService, TRANSFER_PORT, directed_broadcast,
                 next_automatic_chunk, pack_header, parse_ipconfig_interfaces, parse_wifi_status, preferred_address,
                 recv_header, safe_extract_archive, safe_filename, should_auto_accept, stable_fingerprint,
                 unique_destination, wifi_first_address)


class ProtocolTests(unittest.TestCase):
    def test_round_trip_header(self):
        sender, receiver = socket.socketpair()
        try:
            sender.sendall(pack_header({"magic": "PEERDROP1", "name": "hello.txt", "size": 7}))
            self.assertEqual(recv_header(receiver)["name"], "hello.txt")
        finally:
            sender.close(); receiver.close()

    def test_filename_cannot_escape_folder(self):
        self.assertEqual(safe_filename("../../private.txt"), "private.txt")

    def test_destination_gets_suffix(self):
        folder = Path(self._testMethodName)
        self.assertEqual(unique_destination(folder, "same.txt").name, "same.txt")

    def test_wifi_status_parser(self):
        state, ssid = parse_wifi_status("    State                  : connected\n    SSID                   : Office WiFi\n")
        self.assertEqual((state, ssid), ("Connected", "Office WiFi"))

    def test_prefers_private_lan_ip(self):
        self.assertEqual(preferred_address(["100.64.1.4", "192.168.0.33", "127.0.0.1"]), ("192.168.0.33", 1))

    def test_fingerprint_is_stable_and_not_an_ip(self):
        fingerprint = stable_fingerprint("persistent-installation-id")
        self.assertEqual(fingerprint, stable_fingerprint("persistent-installation-id"))
        self.assertEqual(len(fingerprint), 20)
        self.assertNotIn(".", fingerprint)

    def test_discovery_payload_has_identity_and_capabilities(self):
        service = NetworkService(queue.Queue(), "Test PC", Path("."), device_id="abc123fingerprint")
        packet = json.loads(service._announcement())
        self.assertEqual(packet["fingerprint"], "abc123fingerprint")
        self.assertIn("direct-file", packet["capabilities"])
        self.assertIn("approval", packet["capabilities"])

    def test_manual_address_reads_stable_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = queue.Queue()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            service = NetworkService(events, "Lookup PC", root, transfer_port=port, device_id="stable-test-id")
            service.broadcast_enabled = False
            service.start()
            try:
                deadline = time.time() + 3
                while time.time() < deadline:
                    try:
                        if events.get(timeout=.1)[0] == "status":
                            break
                    except queue.Empty:
                        pass
                service.add_manual_peer(f"127.0.0.1:{port}")
                deadline = time.time() + 3
                while time.time() < deadline:
                    try:
                        event = events.get(timeout=.1)
                        if event[0] == "peer":
                            self.assertEqual(event[1]["id"], "stable-test-id")
                            return
                        if event[0] == "error":
                            self.fail(event[1])
                    except queue.Empty:
                        pass
                self.fail("Manual lookup did not return a device identity")
            finally:
                service.stop()

    def test_tcp_presence_registers_a_peer(self):
        with tempfile.TemporaryDirectory() as temp:
            events = queue.Queue()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            service = NetworkService(events, "Host", Path(temp), transfer_port=port, device_id="host-fingerprint-0001")
            service.broadcast_enabled = False
            service.start()
            try:
                deadline = time.time() + 3
                while time.time() < deadline:
                    try:
                        if events.get(timeout=.1)[0] == "status":
                            break
                    except queue.Empty:
                        pass
                with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
                    sock.sendall(pack_header({"magic": "PEERDROP1", "type": "presence",
                                              "fingerprint": "client-fingerprint-0001", "name": "Client",
                                              "port": port, "capabilities": ["direct-file"]}))
                deadline = time.time() + 3
                while time.time() < deadline:
                    try:
                        event = events.get(timeout=.1)
                        if event[0] == "peer":
                            self.assertEqual(event[1]["id"], "client-fingerprint-0001")
                            self.assertEqual(event[1]["host"], "127.0.0.1")
                            return
                    except queue.Empty:
                        pass
                self.fail("TCP presence did not register a peer")
            finally:
                service.stop()

    def test_rejects_oversized_offer(self):
        self.assertGreater(MAX_FILE_SIZE, 30 * 1024 * 1024 * 1024)

    def test_transfer_chunk_size_is_bounded(self):
        service = NetworkService(queue.Queue(), "Test", Path("."), chunk_size=CHUNK_SIZES[0])
        service.set_chunk_size(CHUNK_SIZES[-1])
        self.assertEqual(service.chunk_size, CHUNK_SIZES[-1])
        with self.assertRaises(ValueError):
            service.set_chunk_size(12345)

    def test_automatic_chunk_tuning_stays_in_safe_range(self):
        self.assertEqual(next_automatic_chunk(256 * 1024, .001), 512 * 1024)
        self.assertEqual(next_automatic_chunk(256 * 1024, .2), 128 * 1024)
        self.assertEqual(next_automatic_chunk(CHUNK_SIZES[-1], .001), CHUNK_SIZES[-1])
        service = NetworkService(queue.Queue(), "Test", Path("."), chunk_size=None)
        self.assertIsNone(service.chunk_size)

    def test_auto_accept_requires_confirmation_for_large_files(self):
        self.assertTrue(should_auto_accept(True, AUTO_ACCEPT_MAX_SIZE))
        self.assertFalse(should_auto_accept(True, AUTO_ACCEPT_MAX_SIZE + 1))
        self.assertFalse(should_auto_accept(False, 1))

    def test_wifi_adapter_beats_virtual_adapter(self):
        interfaces = parse_ipconfig_interfaces("""
Ethernet adapter vEthernet (WSL):
   IPv4 Address. . . . . . . . . . . : 172.22.16.1
   Subnet Mask . . . . . . . . . . . : 255.255.240.0
Wireless LAN adapter Wi-Fi:
   IPv4 Address. . . . . . . . . . . : 192.168.1.44
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
""")
        self.assertEqual(wifi_first_address(interfaces), ("192.168.1.44", 1))
        self.assertEqual(directed_broadcast("192.168.1.44", "255.255.255.0"), "192.168.1.255")

    def test_approved_loopback_transfer(self):
        """Exercise the real TCP offer/approve/send/save path without the GUI."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.bin"
            payload = b"PeerDrop transfer test\x00" * 12000
            source.write_bytes(payload)
            events = queue.Queue()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                test_port = probe.getsockname()[1]
            service = NetworkService(events, "Test PC", root / "received", transfer_port=test_port)
            service.broadcast_enabled = False
            service.start()
            try:
                deadline = time.time() + 3
                while time.time() < deadline:
                    try:
                        if events.get(timeout=.1)[0] == "status":
                            break
                    except queue.Empty:
                        pass
                else:
                    self.fail("Transfer listener did not start")
                service.peers["self"] = {"id": "self", "name": "Test PC", "host": "127.0.0.1",
                                         "port": test_port, "seen": time.time()}
                service.send_file("self", source)
                deadline = time.time() + 5
                received = sent = False
                while time.time() < deadline and not (received and sent):
                    try:
                        event = events.get(timeout=.1)
                    except queue.Empty:
                        continue
                    if event[0] == "incoming":
                        service.respond_to_incoming(event[1]["token"], True)
                    elif event[0] == "received":
                        received = event[1].read_bytes() == payload
                    elif event[0] == "sent":
                        sent = True
                    elif event[0] == "error":
                        self.fail(event[1])
                self.assertTrue(received, "Receiver did not save the expected bytes")
                self.assertTrue(sent, "Sender did not finish")
            finally:
                service.stop()

    def test_automatic_accept_loopback_transfer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "automatic.txt"
            payload = b"automatic approval" * 1000
            source.write_bytes(payload)
            events = queue.Queue()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                test_port = probe.getsockname()[1]
            service = NetworkService(events, "Test PC", root / "received", transfer_port=test_port)
            service.broadcast_enabled = False
            service.set_auto_accept(True)
            service.start()
            try:
                deadline = time.time() + 3
                while time.time() < deadline:
                    try:
                        if events.get(timeout=.1)[0] == "status":
                            break
                    except queue.Empty:
                        pass
                service.peers["self"] = {"id": "self", "name": "Test PC", "host": "127.0.0.1",
                                         "port": test_port, "seen": time.time()}
                service.send_file("self", source)
                received = sent = auto_notice = False
                deadline = time.time() + 5
                while time.time() < deadline and not (received and sent and auto_notice):
                    try:
                        event = events.get(timeout=.1)
                    except queue.Empty:
                        continue
                    if event[0] == "incoming_auto":
                        auto_notice = True
                    elif event[0] == "received":
                        received = event[1].read_bytes() == payload
                    elif event[0] == "sent":
                        sent = True
                    elif event[0] == "error":
                        self.fail(event[1])
                self.assertTrue(auto_notice, "Receiver did not auto-accept")
                self.assertTrue(received, "Receiver did not save automatic transfer")
                self.assertTrue(sent, "Sender did not finish")
            finally:
                service.stop()

    def test_folder_transfer_preserves_structure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "project"
            (source / "nested").mkdir(parents=True)
            (source / "readme.txt").write_text("top level", encoding="utf-8")
            (source / "nested" / "data.txt").write_text("nested content", encoding="utf-8")
            events = queue.Queue()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                test_port = probe.getsockname()[1]
            service = NetworkService(events, "Test PC", root / "received", transfer_port=test_port)
            service.broadcast_enabled = False
            service.set_auto_accept(True)
            service.start()
            try:
                deadline = time.time() + 3
                while time.time() < deadline:
                    try:
                        if events.get(timeout=.1)[0] == "status":
                            break
                    except queue.Empty:
                        pass
                service.peers["self"] = {"id": "self", "name": "Test PC", "host": "127.0.0.1",
                                         "port": test_port, "seen": time.time()}
                service.send_folder("self", source)
                received = sent = False
                deadline = time.time() + 8
                while time.time() < deadline and not (received and sent):
                    try:
                        event = events.get(timeout=.1)
                    except queue.Empty:
                        continue
                    if event[0] == "received":
                        received = (event[1] / "nested" / "data.txt").read_text(encoding="utf-8") == "nested content"
                    elif event[0] == "sent":
                        sent = True
                    elif event[0] == "error":
                        self.fail(event[1])
                self.assertTrue(received, "Folder contents were not restored")
                self.assertTrue(sent, "Folder sender did not finish")
            finally:
                service.stop()


    def test_sender_can_cancel_active_transfer(self):
        """A sender-triggered cancel stops the transfer and cleans up."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "big.bin"
            payload = b"x" * (5 * 1024 * 1024)  # 5 MB so it spans many chunks
            source.write_bytes(payload)
            events = queue.Queue()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                test_port = probe.getsockname()[1]
            service = NetworkService(events, "Test PC", root / "received", transfer_port=test_port)
            service.broadcast_enabled = False
            service.set_auto_accept(True)
            service.start()
            try:
                deadline = time.time() + 3
                while time.time() < deadline:
                    try:
                        if events.get(timeout=.1)[0] == "status":
                            break
                    except queue.Empty:
                        pass
                else:
                    self.fail("Transfer listener did not start")
                service.peers["self"] = {"id": "self", "name": "Test PC", "host": "127.0.0.1",
                                         "port": test_port, "seen": time.time()}
                service.send_file("self", source)
                saw_progress = False
                deadline = time.time() + 5
                while time.time() < deadline:
                    try:
                        event = events.get(timeout=.1)
                    except queue.Empty:
                        continue
                    if event[0] == "receive_progress":
                        saw_progress = True
                        self.assertTrue(service.cancel_transfer(source.name), "cancel_transfer found the transfer")
                    elif event[0] == "cancelled":
                        break
                    elif event[0] == "error":
                        self.fail(event[1])
                else:
                    self.fail("Transfer was neither cancelled nor errored")
                self.assertTrue(saw_progress, "Receiver began saving before cancel")
                time.sleep(0.5)
                received = sorted((root / "received").glob("*"))
                self.assertEqual(received, [], f"partial file left after cancel: {received}")
            finally:
                service.stop()


if __name__ == "__main__":
    unittest.main()
