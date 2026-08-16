# WiFi Direct & High-Speed LAN File Transfer (Windows)

High-performance, clean Windows desktop application for transferring files between PCs at maximum network speeds over **WiFi Direct**, **Mobile Hotspot**, or **Local Wi-Fi/LAN**.

---

## ⚡ Key Features
- **High-Speed Transfer Engine**: Binary streaming protocol over TCP with 512 KB chunks, 2 MB socket buffers, and `TCP_NODELAY`.
- **Zero-Configuration Discovery**: Automatic peer discovery across all network subnets using UDP broadcast beacons.
- **Asymmetric Dual Transfer**: Send and receive back-and-forth simultaneously without stopping or reversing roles.
- **2-Way Handshake & Integrity**: Pre-flight disk space verification, path sanitization, and on-the-fly CRC-32 checksums.
- **Obsidian & Cobalt UI**: Clean, high-contrast dark design with no distracting animations.

---

## 🚀 How to Run

### Option 1: Double-Click
Double-click [`run.bat`](file:///c:/Users/washi/Downloads/wifi/run.bat).

### Option 2: Command Line
```powershell
python main.py
```

---

## 💻 How to Transfer Files Between 2 Windows PCs

1. Launch the application on both PCs via [`run.bat`](file:///c:/Users/washi/Downloads/wifi/run.bat).
2. To send files:
   - Go to the **SEND FILES** tab.
   - Select the target PC from the **Discovered Devices** dropdown (or enter its IP).
   - Click **+ Add Files...** and click **START TRANSFER**.
3. Files will stream directly PC-to-PC, verified with CRC-32 checksums, and appear in the **RECEIVE / HOST** save directory.

---

## 🛡️ Windows Firewall Setup
If transferring across PCs for the first time, click **Fix Firewall** in the app or run [`allow_firewall.bat`](file:///c:/Users/washi/Downloads/wifi/allow_firewall.bat) as Administrator on both machines.
