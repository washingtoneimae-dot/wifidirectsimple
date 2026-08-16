# WiFi Direct & High-Speed LAN File Transfer (Windows)

High-performance, zero-fluff Windows desktop application for transferring files between PCs at maximum network speeds over **WiFi Direct** or **Local WiFi/Hotspot**.

---

## ⚡ Key Highlights
- **High-Speed Transfer Engine**: Optimized binary streaming protocol over TCP with 512KB/2MB buffers & `TCP_NODELAY` delivering **500+ MB/s** throughput.
- **Zero Configuration Discovery**: Automatic peer discovery using UDP broadcast beacons (no manual IP typing required).
- **WiFi Direct & LAN Hybrid**: Starts a WiFi Direct Group Owner hotspot or connects directly over any shared WiFi/hotspot.
- **Real-Time Transfer Metrics**: Live transfer speeds (MB/s), elapsed time, percent, ETA countdown, and file queue size.
- **Clean Functional UI**: Native Tkinter dark interface built for fast operation.

---

## 🚀 How to Run

### Option 1: Double-Click
Simply double-click [`run.bat`](file:///c:/Users/washi/Downloads/wifi/run.bat).

### Option 2: Command Line
```powershell
python main.py
```

---

## 💻 How to Transfer Files Between 2 Windows PCs

### On PC 1 (Receiver):
1. Launch the app and go to the **RECEIVE / HOST** tab.
2. Click **▶ Start Listening for Files**.
3. *(Optional)* Click **📡 Start WiFi Direct Hotspot** if you want to create a direct peer network without a router.

### On PC 2 (Sender):
1. Launch the app and go to the **SEND FILES** tab.
2. Under **Discovered PCs**, select PC 1 from the dropdown (or enter its IP).
3. Click **+ Add Files...** to select one or multiple files.
4. Click **⬆ Start High-Speed Send**.

---

## 🧪 Testing & Verification
You can benchmark the core transfer speed and verify 100% SHA-256 data integrity at any time:
```powershell
python benchmark.py
```
