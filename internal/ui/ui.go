// Package ui builds the PeerDrop LAN desktop interface using Fyne.
package ui

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	"peerdrop/internal/discovery"
	"peerdrop/internal/protocol"
	"peerdrop/internal/transfer"

	"fyne.io/fyne/v2"
	"fyne.io/fyne/v2/app"
	"fyne.io/fyne/v2/container"
	"fyne.io/fyne/v2/dialog"
	"fyne.io/fyne/v2/layout"
	"fyne.io/fyne/v2/widget"
)

// App holds the GUI and the engine wiring.
type App struct {
	fyneApp  fyne.App
	window   fyne.Window
	identity *protocol.Identity

	disc  *discovery.Service
	xfer  *transfer.Manager

	// widgets we update
	peerList   *widget.List
	peerData   []discovery.Peer
	selected   int // index into peerData of the currently selected peer (-1 = none)
	recvLog    *widget.Entry
	progress   *widget.ProgressBar
	status     *widget.Label
	autoAccept *widget.Check
	cancelSend *widget.Button
	cancelRecv *widget.Button

	activeSendName string
	activeRecvName string
}

// New builds the application window and wires the engine.
func New() *App {
	fa := app.NewWithID("com.peerdrop.lan")
	w := fa.NewWindow(protocol.ApplicationName)
	w.Resize(fyne.NewSize(720, 620))
	w.SetFixedSize(false)

	id, err := protocol.LoadIdentity("")
	if err != nil {
		id, _ = protocol.LoadIdentity(os.TempDir())
	}
	cfg := id.Config()

	disc := discovery.NewService(id.Fingerprint(), id.Name(), protocol.DiscoveryPort)
	xfer := transfer.NewManager(cfg.ReceiveDir, cfg.AutoAccept, protocol.AutoAcceptMax)

	// When automatic acceptance is off, ask the user per transfer via a
	// blocking Accept/Decline dialog instead of silently rejecting.
	xfer.SetApproveHook(func(name string, size int64, sender string) bool {
		decided := make(chan bool, 1)
		fyne.Do(func() {
			msg := fmt.Sprintf("Incoming %s from %s (%s)", name, sender, humanBytes(size))
			d := dialog.NewConfirm("Incoming transfer", msg, func(ok bool) {
				decided <- ok
			}, w)
			d.SetConfirmText("Accept")
			d.SetDismissText("Decline")
			d.Show()
		})
		return <-decided
	})

	a := &App{
		fyneApp:  fa,
		window:   w,
		identity: id,
		disc:     disc,
		xfer:     xfer,
		recvLog: func() *widget.Entry {
			e := widget.NewMultiLineEntry()
			e.SetText("No activity yet")
			e.Wrapping = fyne.TextWrapWord
			e.Disable() // read-only activity log
			return e
		}(),
		progress: widget.NewProgressBar(),
		status:   widget.NewLabel("Starting…"),
	}
	a.autoAccept = widget.NewCheck("Accept transfers automatically (up to 20 GB)", func(on bool) {
		xfer.SetAutoAccept(on)
		id.SetAutoAccept(on)
	})
	a.autoAccept.SetChecked(cfg.AutoAccept)

	a.peerList = widget.NewList(
		func() int { return len(a.peerData) },
		func() fyne.CanvasObject { return widget.NewLabel("") },
		func(i widget.ListItemID, o fyne.CanvasObject) {
			if i < 0 || i >= len(a.peerData) {
				return
			}
			p := a.peerData[i]
			o.(*widget.Label).SetText(fmt.Sprintf("%s  (%s:%d)", p.Name, p.Host, p.Port))
		},
	)
	a.peerList.OnSelected = func(id widget.ListItemID) {
		a.selected = id
	}
	a.selected = -1

	a.cancelSend = widget.NewButton("Cancel send", func() {
		if a.activeSendName != "" {
			a.xfer.Cancel(a.activeSendName)
		}
	})
	a.cancelSend.Disable()
	a.cancelRecv = widget.NewButton("Cancel current transfer", func() {
		if a.activeRecvName != "" {
			a.xfer.Cancel(a.activeRecvName)
		}
	})
	a.cancelRecv.Disable()

	a.buildTabs()
	return a
}

// Show launches the engine and displays the window.
func (a *App) Show() {
	a.window.Show()

	if err := a.disc.Start(protocol.TransferPort); err != nil {
		a.status.SetText("Discovery error: " + err.Error())
	}
	if err := a.xfer.StartReceiver(protocol.TransferPort); err != nil {
		// PeerDrop is designed to run once per machine. The transfer port
		// being taken almost always means another instance is already
		// running, so tell the user and quit rather than leaving a
		// silently-dead copy that can neither send nor receive.
		a.guardDialog("PeerDrop is already running",
			"Another copy of PeerDrop LAN is already using this computer's transfer port ("+
				itoa(protocol.TransferPort)+"). Close the other window first, then start PeerDrop again.")
		return
	}

	go a.drainEvents()
	go a.refreshPeersLoop()
	a.window.ShowAndRun()
}

func (a *App) buildTabs() {
	a.window.SetContent(a.makeTabs())
}

func (a *App) makeTabs() fyne.CanvasObject {
	tabs := container.NewAppTabs(
		a.sendTab(),
		a.receiveTab(),
		a.networkTab(),
	)
	tabs.SetTabLocation(container.TabLocationTop)
	return tabs
}

func (a *App) sendTab() *container.TabItem {
	sendFile := widget.NewButton("Send file to selected PCs…", func() {
		a.sendSelected(false)
	})
	sendFolder := widget.NewButton("Send folder…", func() {
		a.sendSelected(true)
	})
	actions := container.NewHBox(sendFile, sendFolder, a.cancelSend)
	progressRow := container.NewVBox(widget.NewLabel("Progress"), a.progress)
	return container.NewTabItem("Send", container.NewVBox(
		widget.NewLabel("Nearby devices"),
		a.peerList,
		actions,
		progressRow,
	))
}

func (a *App) receiveTab() *container.TabItem {
	var pause *widget.Button
	pause = widget.NewButton("Pause listening", func() {
		a.xfer.StopReceiver()
		pause.SetText("Resume listening")
	})
	progressRow := container.NewVBox(widget.NewLabel("Progress"), a.progress)
	logScroll := container.NewVScroll(a.recvLog)
	logScroll.SetMinSize(fyne.NewSize(400, 140))
	return container.NewTabItem("Receive", container.NewVBox(
		container.NewHBox(a.autoAccept, pause, a.cancelRecv),
		widget.NewLabel("Activity"),
		logScroll,
		progressRow,
	))
}

func (a *App) networkTab() *container.TabItem {
	nameEntry := widget.NewEntry()
	nameEntry.SetText(a.identity.Name())
	nameEntry.OnChanged = func(s string) { a.identity.SetName(s) }

	folderEntry := widget.NewEntry()
	folderEntry.SetText(a.xferReceiveDir())
	choose := widget.NewButton("Choose folder", func() {
		dialog.ShowFolderOpen(func(uri fyne.ListableURI, err error) {
			if uri != nil {
				folderEntry.SetText(uri.Path())
				a.xfer.SetReceiveDir(uri.Path())
			}
		}, a.window)
	})

	manual := widget.NewEntry()
	manual.SetPlaceHolder("Add PC by IP (e.g. 192.168.1.50)")
	addBtn := widget.NewButton("Add", func() {
		ip := strings.TrimSpace(manual.Text)
		if ip != "" {
			// best-effort: attempt a transfer probe by adding to discovery is
			// not exposed; for v0.1 we just log intent.
			a.appendLog("Manual peer add not yet wired: " + ip)
		}
	})

	hotspot := widget.NewButton("Open Mobile Hotspot", func() {
		openHotspotSettings()
	})

	refreshNet := widget.NewButton("Refresh network", func() {
		a.disc.AnnounceOnce(protocol.TransferPort)
	})

	// FormLayout gives each entry the full width of the field column, so long
	// paths/nicknames are never clipped. Action buttons live in their own
	// toolbar row beneath the form (nesting a button next to an Entry in an
	// HBox squeezes the Entry to its minimum width and clips the text).
	form := layout.NewFormLayout()
	nickRow := container.New(form,
		widget.NewLabel("Nickname"),
		nameEntry,
	)
	folderRow := container.New(form,
		widget.NewLabel("Receive folder"),
		folderEntry,
	)
	manualRow := container.New(form,
		widget.NewLabel("Add PC by IP"),
		manual,
	)

	toolbar := container.NewHBox(choose, addBtn, hotspot, refreshNet)

	return container.NewTabItem("Network", container.NewVBox(
		widget.NewLabel("This PC"),
		nickRow,
		folderRow,
		manualRow,
		toolbar,
		a.status,
	))
}

func (a *App) xferReceiveDir() string {
	cfg := a.identity.Config()
	return cfg.ReceiveDir
}

// sendSelected sends the chosen file/folder to the currently selected peers.
func (a *App) sendSelected(isFolder bool) {
	if a.selected < 0 || a.selected >= len(a.peerData) {
		dialog.ShowInformation(protocol.ApplicationName, "Select one or more nearby PCs first.", a.window)
		return
	}
	peer := a.peerData[a.selected]
	if isFolder {
		dialog.ShowFolderOpen(func(uri fyne.ListableURI, err error) {
			if err != nil || uri == nil {
				return
			}
			a.startSend(peer, uri.Path(), true)
		}, a.window)
		return
	}
	dialog.ShowFileOpen(func(r fyne.URIReadCloser, err error) {
		if err != nil || r == nil {
			return
		}
		path := r.URI().Path()
		_ = r.Close()
		a.startSend(peer, path, false)
	}, a.window)
}

func (a *App) startSend(peer discovery.Peer, path string, isFolder bool) {
	go func() {
		var e error
		if isFolder {
			e = a.xfer.SendFolder(peer.Host, peer.Port, path, a.identity.Name(), a.identity.Fingerprint())
		} else {
			e = a.xfer.SendFile(peer.Host, peer.Port, path, a.identity.Name(), a.identity.Fingerprint())
		}
		if e != nil {
			a.appendLog("Send failed: " + e.Error())
		}
	}()
}

// drainEvents updates the UI from the transfer event channel.
// All widget mutations are marshalled onto the main thread via fyne.Do.
func (a *App) drainEvents() {
	for e := range a.xfer.Events() {
		switch e.Kind {
		case "send_progress":
			frac := 0.0
			if e.Total > 0 {
				frac = float64(e.Current) / float64(e.Total)
			}
			fyne.Do(func() {
				a.activeSendName = e.Name
				a.setCancelSend(true)
				a.progress.SetValue(frac)
				a.status.SetText(fmt.Sprintf("Sending %s → %s", e.Name, e.Peer))
			})
		case "receive_progress":
			frac := 0.0
			if e.Total > 0 {
				frac = float64(e.Current) / float64(e.Total)
			}
			fyne.Do(func() {
				a.activeRecvName = e.Name
				a.setCancelRecv(true)
				a.progress.SetValue(frac)
				a.status.SetText(fmt.Sprintf("Receiving %s", e.Name))
			})
		case "sent":
			fyne.Do(func() {
				a.appendLog(fmt.Sprintf("Sent %s to %s", e.Name, e.Peer))
				a.progress.SetValue(1)
				a.status.SetText("Ready")
				a.activeSendName = ""
				a.setCancelSend(false)
			})
		case "received":
			fyne.Do(func() {
				a.appendLog(fmt.Sprintf("Received %s", e.Name))
				a.progress.SetValue(1)
				a.status.SetText("Ready")
				a.activeRecvName = ""
				a.setCancelRecv(false)
			})
		case "cancelled":
			fyne.Do(func() {
				a.appendLog(fmt.Sprintf("Cancelled %s", e.Name))
				a.progress.SetValue(0)
				a.status.SetText("Ready")
				a.activeSendName = ""
				a.activeRecvName = ""
				a.setCancelSend(false)
				a.setCancelRecv(false)
			})
		case "error":
			fyne.Do(func() {
				a.appendLog(fmt.Sprintf("Error: %s", e.Name))
				a.status.SetText("Ready")
				a.activeSendName = ""
				a.activeRecvName = ""
				a.setCancelSend(false)
				a.setCancelRecv(false)
			})
		}
	}
}

func (a *App) refreshPeersLoop() {
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	for range ticker.C {
		a.refreshPeers()
	}
}

func (a *App) refreshPeers() {
	a.peerData = a.disc.Peers()
	fyne.Do(func() {
		a.peerList.Refresh()
		a.status.SetText(fmt.Sprintf("Ready on port %d · %d device(s)", protocol.TransferPort, len(a.peerData)))
	})
}

func (a *App) appendLog(line string) {
	current := a.recvLog.Text
	if current == "No activity yet" || current == "" {
		a.recvLog.SetText(line)
	} else {
		a.recvLog.SetText(current + "\n" + line)
	}
}

func (a *App) setCancelSend(on bool) {
	fyne.Do(func() {
		if on {
			a.cancelSend.Enable()
		} else {
			a.cancelSend.Disable()
		}
	})
}

func (a *App) setCancelRecv(on bool) {
	fyne.Do(func() {
		if on {
			a.cancelRecv.Enable()
		} else {
			a.cancelRecv.Disable()
		}
	})
}

// guardDialog shows an informational dialog explaining that another
// instance is already running, then quits once it is dismissed.
func (a *App) guardDialog(title, message string) {
	d := dialog.NewInformation(title, message, a.window)
	d.SetOnClosed(func() { a.fyneApp.Quit() })
	d.Show()
	a.window.ShowAndRun()
}

// openHotspotSettings opens the desktop Wi-Fi / hotspot panel and returns
// without a follow-up dialog (the settings window is the instruction).
func openHotspotSettings() {
	for _, cmd := range [][]string{
		{"gnome-control-center", "wifi"},
		{"gnome-control-center", "network"},
	} {
		if c := exec.Command(cmd[0], cmd[1:]...); c.Start() == nil {
			return
		}
	}
	// Last-resort fallback for non-GNOME desktops.
	_ = exec.Command("xdg-open", "network").Start()
}

// itoa is a small non-negative integer-to-string helper (avoids importing
// strconv just for the port number in UI messages).
func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b [12]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}

// humanBytes formats a byte count for display in the approval dialog.
func humanBytes(n int64) string {
	const unit = 1024
	if n < unit {
		return fmt.Sprintf("%d B", n)
	}
	div, exp := int64(unit), 0
	for m := n / unit; m >= unit; m /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %cB", float64(n)/float64(div), "KMGTPE"[exp])
}
