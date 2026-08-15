# PeerDrop LAN

A small, working desktop program for sending files directly between PCs connected to the same Wi-Fi network, Ethernet LAN, or one PC's mobile hotspot. It does not upload files to the internet or require an account.

## Run

1. Install Python 3.10 or newer on each Windows PC (from [python.org](https://www.python.org/downloads/)). During installation, select **Add Python to PATH**.
2. Copy this folder to each PC.
3. Double-click `run.bat`, or open the folder in a terminal and run `py -3 app.py`.
4. If Windows Firewall asks, allow access on **Private networks**.
5. Each PC should appear in the other PC's nearby-device list. Select a device, choose **Send file**, then accept the request on the receiving PC.

The app automatically shows Wi-Fi connection state and the local IPv4 address(es) below the PC settings. Select **Open Mobile Hotspot** to open Windows' Mobile Hotspot setup. Turn it on there, have the other PC join that hotspot, then both PCs can discover and exchange files in either direction.

## Notes

- This uses local Wi-Fi/LAN connectivity. Native Windows Wi-Fi Direct pairing is hardware/driver-specific, so using the same Wi-Fi or a hotspot is the most reliable PC-to-PC path.
- Received files go to `Downloads\PeerDrop` unless changed in the app.
- Transfers require acceptance by default. The receiver can enable automatic acceptance for trusted networks.
- A file is marked **Sent** only after the receiving PC confirms it fully saved the file. Failed or declined transfers appear in the activity list, and incomplete received files are removed.
- Devices are discovered with local UDP broadcast (port 45871); files are transferred over TCP (port 45872). Both PCs must be on the same local network, and firewall rules must allow those ports on private networks.
- If a router hides devices from each other, enter the other PC's private IP address in **Add PC by IP** (on that PC, run `ipconfig` and use its IPv4 Address).

## Test

Run `py -3 -m unittest -v` in this folder.
