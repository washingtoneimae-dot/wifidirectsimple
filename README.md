# PeerDrop LAN

A small, working desktop program for sending files directly between PCs connected to the same Wi-Fi network, Ethernet LAN, or one PC's mobile hotspot. It does not upload files to the internet or require an account.

## Run on Windows

1. Double-click `setup.bat` on each Windows PC. It checks Python, installs Python 3.13 through Windows Package Manager if needed, verifies the desktop UI support, and runs the local checks. Python 3.13 works. Some Microsoft Office installations include an internal Python component that cannot run normal desktop apps; setup installs the regular Python app when needed.
2. Copy this folder to each PC.
3. Double-click `run.bat`, or open the folder in a terminal and run `py -3 app.py`.
4. If Windows Firewall asks, allow access on **Private networks**.
5. Each PC should appear in the other PC's nearby-device list. Select a device, choose **Send file**, then accept the request on the receiving PC.

## Run on Linux / macOS

1. Make sure Python 3.10+ and Tk are installed:
   - Ubuntu/Debian: `sudo apt-get install -y python3 python3-tk`
   - Fedora: `sudo dnf install -y python3 python3-tkinter`
   - macOS: `brew install python-tk` (or use the python.org installer, which bundles Tk)
2. Copy this folder to each machine.
3. Launch it — either double-click `run.sh`, or from a terminal:
   ```bash
   ./run.sh
   # or directly:
   python3 app.py
   ```
4. A desktop (X11/Wayland) session is required — the app is a Tk GUI. On a headless server use `xvfb-run python3 app.py` if you only need to test it.
5. Allow the UDP (45871) and TCP (45872) ports in your firewall if discovery or transfers are blocked (e.g. `sudo ufw allow 45871/udp` and `sudo ufw allow 45872/tcp`).
6. Each PC should appear in the other PC's nearby-device list. Select a device, choose **Send file**, then accept the request on the receiving PC.

The same `app.py` runs on both platforms; only the launcher differs (`run.bat` on Windows, `run.sh` elsewhere).

## Mesh-style sending

Use the **Network** tab to set the nickname other PCs see. On the **Send** tab, select one PC and use **Save selected peer name** to give it your own persistent label; both that label and the PC's shared nickname are shown. Labels are linked to a stable opaque device fingerprint, not an IP address, so they remain correct after DHCP, hotspot, or adapter changes. Hold `Ctrl` while selecting devices (or use `Shift` for a range), choose one file, and it is sent directly to every selected PC at the same time. Each receiving PC approves or declines independently.

## Folders

Select one or more PCs, then choose **Send folder**. The selected folder, its subfolders, and files retain their arrangement on the receiving PC. Each recipient receives and unpacks its own copy; the same safe chunk tuning is used for the transfer.

## Transfer tuning

The Network tab defaults to **Automatic (recommended)**. It starts at 256 KB and safely adjusts between 64 KB and 1 MB as a transfer runs. You can instead select a fixed size: try 128 KB or 64 KB for unstable hotspot links, or 512 KB/1 MB on strong, fast Wi-Fi. A selected mode applies to newly started transfers and is saved for later.

The app automatically shows Wi-Fi connection state and the local IPv4 address(es) below the PC settings. On Windows, select **Open Mobile Hotspot** to open Windows' Mobile Hotspot setup. On Linux/macOS the same button points you to your desktop's network settings. Turn on a hotspot there, have the other PC join it, then both PCs can discover and exchange files in either direction.

## Notes

- This uses local Wi-Fi/LAN connectivity. Native Windows Wi-Fi Direct pairing is hardware/driver-specific, so using the same Wi-Fi or a hotspot is the most reliable PC-to-PC path.
- Received files go to `Downloads\PeerDrop` on Windows, or `~/Downloads/PeerDrop` on Linux/macOS, unless changed in the app.
- Settings are stored in `%APPDATA%\PeerDropLAN\settings.json` on Windows, or `~/.config/PeerDropLAN/settings.json` on Linux/macOS.
- Transfers require acceptance by default. The receiver can enable automatic acceptance for trusted networks.
- Automatic acceptance is limited to files up to 20 GB. Larger files always show an explicit confirmation prompt.
- A file is marked **Sent** only after the receiving PC confirms it fully saved the file. Failed or declined transfers appear in the activity list, and incomplete received files are removed.
- Devices are discovered with local UDP broadcast (port 45871); files are transferred over TCP (port 45872). Both PCs must be on the same local network, and firewall rules must allow those ports on private networks.
- If a router hides devices from each other, enter the other PC's private IP address in **Add PC by IP** (on Windows run `ipconfig`, on Linux run `ip -4 addr` or `hostname -I`, and use the IPv4 Address).

## Test

Run `py -3 -m unittest -v` on Windows, or `python3 -m unittest -v` on Linux/macOS, in this folder.
