# PeerDrop LAN

A small, working desktop program for sending files directly between PCs connected to the same Wi-Fi network, Ethernet LAN, or one PC's mobile hotspot. It does not upload files to the internet or require an account.

## Run

1. Double-click `setup.bat` on each Windows PC. It checks Python, installs Python 3.13 through Windows Package Manager if needed, verifies the desktop UI support, and runs the local checks. Python 3.13 works. Some Microsoft Office installations include an internal Python component that cannot run normal desktop apps; setup installs the regular Python app when needed.
2. Copy this folder to each PC.
3. Double-click `run.bat`, or open the folder in a terminal and run `py -3 app.py`.
4. If Windows Firewall asks, allow access on **Private networks**.
5. Each PC should appear in the other PC's nearby-device list. Select a device, choose **Send file**, then accept the request on the receiving PC.

While a transfer is in progress you can stop it from either side:
- On the **Send** tab, **Cancel send** aborts an outgoing transfer.
- On the **Receive** tab, **Cancel current transfer** aborts an incoming one.

Cancelling cleans up any partially received file and notifies the other PC.

## Mesh-style sending

Use the **Network** tab to set the nickname other PCs see. On the **Send** tab, select one PC and use **Save selected peer name** to give it your own persistent label; both that label and the PC's shared nickname are shown. Labels are linked to a stable opaque device fingerprint, not an IP address, so they remain correct after DHCP, hotspot, or adapter changes. Hold `Ctrl` while selecting devices (or use `Shift` for a range), choose one file, and it is sent directly to every selected PC at the same time. Each receiving PC approves or declines independently.

## Folders

Select one or more PCs, then choose **Send folder**. The selected folder, its subfolders, and files retain their arrangement on the receiving PC. Each recipient receives and unpacks its own copy; the same safe chunk tuning is used for the transfer.

## Transfer tuning

The Network tab defaults to **Automatic (recommended)**. It starts at 256 KB and safely adjusts between 64 KB and 1 MB as a transfer runs. You can instead select a fixed size: try 128 KB or 64 KB for unstable hotspot links, or 512 KB/1 MB on strong, fast Wi-Fi. A selected mode applies to newly started transfers and is saved for later.

The app automatically shows Wi-Fi connection state and the local IPv4 address(es) below the PC settings. Select **Open Mobile Hotspot** to open Windows' Mobile Hotspot setup. Turn it on there, have the other PC join that hotspot, then both PCs can discover and exchange files in either direction.

## Notes

- This uses local Wi-Fi/LAN connectivity. Native Windows Wi-Fi Direct pairing is hardware/driver-specific, so using the same Wi-Fi or a hotspot is the most reliable PC-to-PC path.
- Received files go to `Downloads\PeerDrop` unless changed in the app.
- Transfers require acceptance by default. The receiver can enable automatic acceptance for trusted networks.
- Automatic acceptance is limited to files up to 20 GB. Larger files always show an explicit confirmation prompt.
- A file is marked **Sent** only after the receiving PC confirms it fully saved the file. Failed or declined transfers appear in the activity list, and incomplete received files are removed.
- Devices are discovered with local UDP broadcast (port 45871); files are transferred over TCP (port 45872). Both PCs must be on the same local network, and firewall rules must allow those ports on private networks.
- If a router hides devices from each other, enter the other PC's private IP address in **Add PC by IP** (on that PC, run `ipconfig` and use its IPv4 Address).

## Test

Run `py -3 -m unittest -v` in this folder.
