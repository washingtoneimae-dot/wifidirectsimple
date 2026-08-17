# PeerDrop LAN

A small, working desktop program for sending files directly between PCs connected to the same Wi-Fi network, Ethernet LAN, or one PC's mobile hotspot. It does not upload files to the internet or require an account.

This is the **Linux** build. (The Windows build lives on the `chatgpt` branch.)

## Requirements

- Python 3.10 or newer
- Tk (the `tkinter` GUI toolkit)
  - Ubuntu/Debian: `sudo apt-get install -y python3 python3-tk`
  - Fedora: `sudo dnf install -y python3 python3-tkinter`
  - Other distros: install the `python3-tk` / `tk` package for your Python
- A desktop session (X11 or Wayland). On a headless machine, run with `xvfb-run python3 app.py` to test.

## Run

1. Copy this folder to each Linux PC that will take part.
2. Launch it — double-click `run.sh`, or from a terminal:
   ```bash
   ./run.sh
   # or directly:
   python3 app.py
   ```
3. Allow the discovery/listening ports in your firewall if they are blocked, e.g.:
   ```bash
   sudo ufw allow 45871/udp
   sudo ufw allow 45872/tcp
   ```
4. Each PC should appear in the other PC's nearby-device list. Select a device, choose **Send file**, then accept the request on the receiving PC.

## Mesh-style sending

Use the **Network** tab to set the nickname other PCs see. On the **Send** tab, select one PC and use **Save selected peer name** to give it your own persistent label; both that label and the PC's shared nickname are shown. Labels are linked to a stable opaque device fingerprint, not an IP address, so they remain correct after DHCP, hotspot, or adapter changes. Hold `Ctrl` while selecting devices (or use `Shift` for a range), choose one file, and it is sent directly to every selected PC at the same time. Each receiving PC approves or declines independently.

## Folders

Select one or more PCs, then choose **Send folder**. The selected folder, its subfolders, and files retain their arrangement on the receiving PC. Each recipient receives and unpacks its own copy; the same safe chunk tuning is used for the transfer.

## Transfer tuning

The Network tab defaults to **Automatic (recommended)**. It starts at 256 KB and safely adjusts between 64 KB and 1 MB as a transfer runs. You can instead select a fixed size: try 128 KB or 64 KB for unstable hotspot links, or 512 KB/1 MB on strong, fast Wi-Fi. A selected mode applies to newly started transfers and is saved for later.

The app automatically shows Wi-Fi connection state and the local IPv4 address(es) below the PC settings. Select **Open Mobile Hotspot** to jump to your desktop's network settings. Turn on a hotspot there, have the other PC join it, then both PCs can discover and exchange files in either direction.

## Notes

- This uses local Wi-Fi/LAN connectivity. Using the same Wi-Fi or a hotspot is the most reliable PC-to-PC path.
- Received files go to `~/Downloads/PeerDrop` unless changed in the app.
- Settings are stored in `~/.config/PeerDropLAN/settings.json`.
- Transfers require acceptance by default. The receiver can enable automatic acceptance for trusted networks.
- Automatic acceptance is limited to files up to 20 GB. Larger files always show an explicit confirmation prompt.
- A file is marked **Sent** only after the receiving PC confirms it fully saved the file. Failed or declined transfers appear in the activity list, and incomplete received files are removed.
- Devices are discovered with local UDP broadcast (port 45871); files are transferred over TCP (port 45872). Both PCs must be on the same local network, and firewall rules must allow those ports.
- If a router hides devices from each other, enter the other PC's private IP address in **Add PC by IP** (on that PC, run `ip -4 addr` or `hostname -I`, and use its IPv4 address).

## Test

Run `python3 -m unittest -v` in this folder.
