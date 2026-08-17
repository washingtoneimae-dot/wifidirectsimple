# PeerDrop LAN — Go edition

A from-scratch **Go** implementation of [PeerDrop LAN](https://github.com/washingtoneimae-dot/wifidirectsimple):
send files and folders directly between PCs on the same Wi-Fi, Ethernet, or
mobile hotspot — no cloud, no account, no internet upload. Transfers happen
over your local network only.

This is one of three wire-compatible editions:

| Edition | Branch | Language |
| --- | --- | --- |
| Python (Linux) | `chatgpt-linux` | Python + Tkinter |
| Windows | `chatgpt` | Python + Tkinter |
| **Go (Linux)** | **`go-linux`** | **Go + Fyne** |

All three speak the same protocol, so the Go app can transfer to the Python
and Windows apps on the same network.

## Features

- Discover nearby PCs automatically over UDP (no setup)
- Send a file or a whole folder to a selected PC
- Cancel an in-progress transfer from either the sender or the receiver
- Auto-accept transfers up to 20 GB (toggleable)
- Pause/resume listening
- Wire-compatible with the Python and Windows editions
- Single static binary, no runtime dependencies

## Install (end users)

Download `peerdrop-go_1.0.0_amd64.deb` from the
[releases page](https://github.com/washingtoneimae-dot/wifidirectsimple/releases),
then:

```bash
sudo dpkg -i peerdrop-go_1.0.0_amd64.deb
```

Launch **PeerDrop LAN** from your applications menu, or run `peerdrop-go`.
Files you receive land in `~/Downloads/PeerDrop`.

> To open the mobile-hotspot panel, the app calls `gnome-control-center
> wifi`; install it with `sudo apt-get install gnome-control-center` if your
> desktop doesn't have it.

## Build from source (developers)

Requires Go 1.26+ and the Fyne build prerequisites (C libraries for the
windowing backend):

```bash
sudo apt-get install pkg-config libgl1-mesa-dev xorg-dev libwayland-dev \
  libxkbcommon-dev libxkbcommon-x11-dev libxcursor-dev \
  libxrandr-dev libxinerama-dev libxi-dev
```

```bash
go build ./...                 # vet + compile everything
go test ./...                  # run the unit tests
./build.sh 1.0.0              # produce dist/peerdrop-go_1.0.0_amd64.deb
```

The `peerdrop-send` helper is a small CLI for headless testing and for
verifying cross-edition interop:

```bash
peerdrop-send <host> <port> <path>   # e.g. peerdrop-send 192.168.1.24 45872 photo.jpg
```

## Protocol (for the curious)

- **Discovery:** UDP broadcast to `255.255.255.255:45871` every 3 s with a
  JSON announcement (`magic`, `version`, `fingerprint`, `name`, `port`,
  `capabilities`). Peers prune after 10 s of silence.
- **Transfer:** TCP connect to `host:45872`, send a 4-byte big-endian length
  followed by a compact JSON header (`magic`, `type`, `name`, `size`,
  `sender`, `sender_fingerprint`). The receiver replies `{"accepted":true}`
  (or `false`), the sender streams the raw bytes, then the receiver replies
  `{"completed":true}`. Folders are zipped (forward-slash members) and
  extracted with path-traversal protection.
- **Magic:** `PEERDROP1`, ports `45871` (discovery) / `45872` (transfer).

## License

Same as the parent project.
