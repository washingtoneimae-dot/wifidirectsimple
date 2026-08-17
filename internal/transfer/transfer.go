// Package transfer implements the PeerDrop LAN TCP transfer protocol
// (send/receive files and folders) and the cancel semantics, matching the
// Python reference (app.py NetworkService send/receive workers).
package transfer

import (
	"archive/zip"
	"context"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"peerdrop/internal/protocol"
)

// Event is emitted on the manager's event channel for UI updates.
type Event struct {
	Kind string // "send_progress","receive_progress","sent","received","cancelled","error","sending"
	Name string
	Peer string
	Current int64
	Total   int64
}

// Manager coordinates transfers and exposes cancel.
type Manager struct {
	receiveDir      string
	autoAccept      bool
	autoCap         int64
	chunk           int // bytes per read/write; 0 means DefaultChunkSize

	mu               sync.Mutex
	active           map[string]*activeTransfer
	events           chan Event
	receiverEnabled  bool
	listener         net.Listener
}

type activeTransfer struct {
	cancel context.CancelFunc
	name   string
}

// NewManager creates a transfer manager. events is an unbuffered-or-buffered
// channel the UI drains; pass nil to discard.
func NewManager(receiveDir string, autoAccept bool, autoCap int64) *Manager {
	if autoCap <= 0 {
		autoCap = protocol.AutoAcceptMax
	}
	return &Manager{
		receiveDir:     receiveDir,
		autoAccept:     autoAccept,
		autoCap:        autoCap,
		active:         make(map[string]*activeTransfer),
		events:         make(chan Event, 64),
		receiverEnabled: true,
	}
}

// Events returns the event channel.
func (m *Manager) Events() <-chan Event { return m.events }

// SetChunk overrides the transfer chunk size in bytes (used for tests and
// auto-tuning). 0 restores the default.
func (m *Manager) SetChunk(n int) {
	m.mu.Lock()
	m.chunk = n
	m.mu.Unlock()
}

func (m *Manager) chunkSize() int {
	m.mu.Lock()
	c := m.chunk
	m.mu.Unlock()
	if c <= 0 {
		return protocol.DefaultChunkSize
	}
	return c
}
func (m *Manager) SetReceiveDir(dir string) {
	m.mu.Lock()
	m.receiveDir = dir
	m.mu.Unlock()
	_ = os.MkdirAll(dir, 0o755)
}

// SetAutoAccept toggles automatic acceptance for files up to autoCap.
func (m *Manager) SetAutoAccept(on bool) {
	m.mu.Lock()
	m.autoAccept = on
	m.mu.Unlock()
}

func (m *Manager) emit(e Event) {
	if m.events == nil {
		return
	}
	select {
	case m.events <- e:
	default: // drop if UI is not draining fast enough
	}
}

// Cancel aborts any active (or pending) transfer matching name (case-insensitive),
// mirroring Python cancel_transfer. Returns true if something matched.
func (m *Manager) Cancel(identifier string) bool {
	m.mu.Lock()
	var matched []context.CancelFunc
	needle := lower(identifier)
	for key, at := range m.active {
		if lower(key) == needle || lower(at.name) == needle {
			matched = append(matched, at.cancel)
			delete(m.active, key)
		}
	}
	m.mu.Unlock()
	for _, c := range matched {
		c()
	}
	return len(matched) > 0
}

func (m *Manager) register(key, name string) (context.Context, context.CancelFunc) {
	ctx, cancel := context.WithCancel(context.Background())
	m.mu.Lock()
	m.active[key] = &activeTransfer{cancel: cancel, name: name}
	m.mu.Unlock()
	return ctx, cancel
}

func (m *Manager) unregister(key string) {
	m.mu.Lock()
	delete(m.active, key)
	m.mu.Unlock()
}

// SendFile transfers a single file to peer (host:port). The peer's accept
// decision is made remotely; this side honors an "accepted:false" reply.
func (m *Manager) SendFile(peerHost string, peerPort int, path string, senderName, senderFP string) error {
	return m.sendFileWithName(peerHost, peerPort, path, filepath.Base(path), "file", senderName, senderFP)
}

func (m *Manager) sendFileWithName(peerHost string, peerPort int, path, displayName, transferType, senderName, senderFP string) error {
	fi, err := os.Stat(path)
	if err != nil {
		return err
	}
	size := fi.Size()

	ctx, cancel := m.register(displayName, displayName)
	defer m.unregister(displayName)
	defer cancel()

	m.emit(Event{Kind: "sending", Name: displayName, Peer: peerHost})

	conn, err := net.DialTimeout("tcp", net.JoinHostPort(peerHost, itoa(peerPort)), 15*time.Second)
	if err != nil {
		m.emit(Event{Kind: "error", Name: displayName, Peer: peerHost, Current: 0, Total: size})
		return err
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(300 * time.Second))

	offer := protocol.Header{
		"magic":              protocol.Magic,
		"type":               transferType,
		"name":               displayName,
		"size":               size,
		"sender":             senderName,
		"sender_fingerprint": senderFP,
	}
	if _, err := conn.Write(protocol.PackHeader(offer)); err != nil {
		m.emit(Event{Kind: "error", Name: displayName, Peer: peerHost})
		return err
	}
	reply, err := protocol.ReadHeader(conn)
	if err != nil {
		m.emit(Event{Kind: "error", Name: displayName, Peer: peerHost})
		return err
	}
	if acc, _ := reply["accepted"].(bool); !acc {
		reason, _ := reply["reason"].(string)
		if reason == "" {
			reason = "Transfer declined"
		}
		m.emit(Event{Kind: "error", Name: displayName, Peer: peerHost, Current: 0, Total: size})
		return fmt.Errorf("%s", reason)
	}

	// Stream the file bytes.
	file, err := os.Open(path)
	if err != nil {
		m.emit(Event{Kind: "error", Name: displayName, Peer: peerHost})
		return err
	}
	defer file.Close()

	buf := make([]byte, m.chunkSize())
	var sent int64
	for {
		if ctx.Err() != nil {
			// Tell the receiver this was a deliberate cancel, then stop.
			_ = writeReply(conn, protocol.Header{"completed": false, "reason": "Cancelled by Sender"})
			m.emit(Event{Kind: "cancelled", Name: displayName, Peer: "Sender"})
			return context.Canceled
		}
		n, readErr := file.Read(buf)
		if n > 0 {
			block := buf[:n]
			if _, werr := conn.Write(block); werr != nil {
				m.emit(Event{Kind: "error", Name: displayName, Peer: peerHost})
				return werr
			}
			sent += int64(n)
			m.emit(Event{Kind: "send_progress", Name: displayName, Peer: peerHost, Current: sent, Total: size})
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			m.emit(Event{Kind: "error", Name: displayName, Peer: peerHost})
			return readErr
		}
	}

	// Signal end of stream and await confirmation.
	if tc, ok := conn.(*net.TCPConn); ok {
		_ = tc.CloseWrite()
	}
	confirm, err := protocol.ReadHeader(conn)
	if err != nil {
		m.emit(Event{Kind: "error", Name: displayName, Peer: peerHost})
		return err
	}
	if done, _ := confirm["completed"].(bool); !done {
		reason, _ := confirm["reason"].(string)
		m.emit(Event{Kind: "error", Name: displayName, Peer: peerHost, Current: sent, Total: size})
		return fmt.Errorf("%s", reason)
	}
	m.emit(Event{Kind: "sent", Name: displayName, Peer: peerHost, Current: sent, Total: size})
	return nil
}

// SendFolder zips the folder (forward-slash members, mirroring the Python
// reference) and transfers it as a "folder" type.
func (m *Manager) SendFolder(peerHost string, peerPort int, folder string, senderName, senderFP string) error {
	archive, err := os.CreateTemp("", "peerdrop-folder-*.zip")
	if err != nil {
		return err
	}
	archivePath := archive.Name()
	archive.Close()
	defer os.Remove(archivePath)

	if err := createFolderArchive(folder, archivePath); err != nil {
		return err
	}
	return m.sendFileWithName(peerHost, peerPort, archivePath, filepath.Base(folder), "folder", senderName, senderFP)
}

// createFolderArchive writes folder to dst as a zip with POSIX (forward-slash)
// member names, mirroring the Python create_folder_archive.
func createFolderArchive(folder, dst string) error {
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()
	zw := zip.NewWriter(out)
	defer zw.Close()

	base := filepath.Clean(folder)
	return filepath.Walk(folder, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(base, path)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		// Forward-slash member names (zip standard), matching Python as_posix().
		rel = filepath.ToSlash(rel)
		if info.IsDir() {
			rel += "/"
			_, err := zw.Create(rel)
			return err
		}
		src, err := os.Open(path)
		if err != nil {
			return err
		}
		defer src.Close()
		w, err := zw.Create(rel)
		if err != nil {
			return err
		}
		_, err = io.Copy(w, src)
		return err
	})
}
func (m *Manager) StartReceiver(transferPort int) error {
	ln, err := net.Listen("tcp", net.JoinHostPort("0.0.0.0", itoa(transferPort)))
	if err != nil {
		return err
	}
	m.mu.Lock()
	m.listener = ln
	m.mu.Unlock()
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			go m.handleIncoming(conn)
		}
	}()
	return nil
}

// StopReceiver closes the listening socket.
func (m *Manager) StopReceiver() {
	m.mu.Lock()
	ln := m.listener
	m.mu.Unlock()
	if ln != nil {
		_ = ln.Close()
	}
}

func (m *Manager) handleIncoming(conn net.Conn) {
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(300 * time.Second))

	offer, err := protocol.ReadHeader(conn)
	if err != nil {
		return
	}
	if offer["magic"] != protocol.Magic {
		return
	}
	transferType, _ := offer["type"].(string)
	name, _ := offer["name"].(string)
	sizeF, _ := offer["size"].(float64)
	size := int64(sizeF)

	ctx, cancel := m.register(name, name)
	defer m.unregister(name)
	defer cancel()

	// Accept automatically when enabled and within the size cap; otherwise
	// decline. (A future GUI prompt path can inject a live decision here.)
	if !(m.autoAccept && size <= m.autoCap) {
		_ = writeReply(conn, protocol.Header{"accepted": false, "reason": "Not accepted"})
		return
	}
	if _, err := conn.Write(protocol.PackHeader(protocol.Header{"accepted": true})); err != nil {
		return
	}

	dest := uniqueDestination(m.receiveDir, name)
	partial := dest
	if transferType == "folder" {
		partial = uniqueDestination(m.receiveDir, "."+name+".zip")
	}

	out, err := os.Create(partial)
	if err != nil {
		_ = writeReply(conn, protocol.Header{"completed": false, "reason": "Cannot write file"})
		return
	}

	var received int64
	buf := make([]byte, m.chunkSize())
	for received < size {
		if ctx.Err() != nil {
			out.Close()
			cleanupPartial(partial, transferType == "folder")
			_ = writeReply(conn, protocol.Header{"completed": false, "reason": "Cancelled by Receiver"})
			m.emit(Event{Kind: "cancelled", Name: name, Peer: "Receiver"})
			return
		}
		toRead := int(size - received)
		if toRead > m.chunkSize() {
			toRead = m.chunkSize()
		}
		n, readErr := conn.Read(buf[:toRead])
		if n > 0 {
			if _, werr := out.Write(buf[:n]); werr != nil {
				out.Close()
				cleanupPartial(partial, transferType == "folder")
				_ = writeReply(conn, protocol.Header{"completed": false, "reason": "Write error"})
				m.emit(Event{Kind: "error", Name: name, Peer: "Receiver"})
				return
			}
			received += int64(n)
			m.emit(Event{Kind: "receive_progress", Name: name, Peer: "Receiver", Current: received, Total: size})
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			out.Close()
			cleanupPartial(partial, transferType == "folder")
			_ = writeReply(conn, protocol.Header{"completed": false, "reason": "Sender disconnected"})
			m.emit(Event{Kind: "error", Name: name, Peer: "Receiver"})
			return
		}
	}
	out.Close()

	// If we did not receive the full declared size, the transfer was
	// interrupted (sender cancelled or disconnected). Clean up the partial
	// file and report it as cancelled rather than pretending it succeeded.
	if received < size {
		cleanupPartial(partial, transferType == "folder")
		_ = writeReply(conn, protocol.Header{"completed": false, "reason": "Cancelled by Receiver"})
		m.emit(Event{Kind: "cancelled", Name: name, Peer: "Receiver"})
		return
	}

	if transferType == "folder" {
		if err := safeExtractArchive(partial, uniqueDestination(m.receiveDir, name)); err != nil {
			cleanupPartial(partial, true)
			_ = writeReply(conn, protocol.Header{"completed": false, "reason": "Folder extraction failed"})
			m.emit(Event{Kind: "error", Name: name, Peer: "Receiver"})
			return
		}
		_ = os.Remove(partial)
	}

	_ = writeReply(conn, protocol.Header{"completed": true})
	m.emit(Event{Kind: "received", Name: name, Peer: "Receiver", Current: received, Total: size})
}

func writeReply(conn net.Conn, h protocol.Header) error {
	_, err := conn.Write(protocol.PackHeader(h))
	return err
}

// --- helpers ---

func uniqueDestination(dir, name string) string {
	_ = os.MkdirAll(dir, 0o755)
	base := name
	candidate := filepath.Join(dir, base)
	if _, err := os.Stat(candidate); os.IsNotExist(err) {
		return candidate
	}
	ext := filepath.Ext(base)
	stem := base[:len(base)-len(ext)]
	for i := 1; ; i++ {
		candidate = filepath.Join(dir, fmt.Sprintf("%s(%d)%s", stem, i, ext))
		if _, err := os.Stat(candidate); os.IsNotExist(err) {
			return candidate
		}
	}
}

func cleanupPartial(path string, isDir bool) {
	if path == "" {
		return
	}
	if isDir {
		_ = os.Remove(path) // zip file
		return
	}
	_ = os.Remove(path)
}

func lower(s string) string {
	b := []byte(s)
	for i, c := range b {
		if c >= 'A' && c <= 'Z' {
			b[i] = c + ('a' - 'A')
		}
	}
	return string(b)
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [12]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}

// safeExtractArchive extracts a zip, normalizing Windows-style backslash
// member paths to forward slashes and rejecting absolute paths or ".."
// segments (path-traversal protection), matching the Python reference.
func safeExtractArchive(archivePath, destDir string) error {
	zr, err := zip.OpenReader(archivePath)
	if err != nil {
		return err
	}
	defer zr.Close()

	var total int64
	const maxExtracted = 1 << 30 // 1 GB expanded cap for v0.1
	for _, member := range zr.File {
		clean := strings.ReplaceAll(member.Name, "\\", "/")
		parts := strings.Split(clean, "/")
		for _, p := range parts {
			if p == ".." {
				return fmt.Errorf("folder archive contains an unsafe path: %s", member.Name)
			}
		}
		target := filepath.Join(destDir, clean)
		// Ensure the resolved target stays within destDir.
		if !strings.HasPrefix(target, filepath.Clean(destDir)+string(os.PathSeparator)) && target != filepath.Clean(destDir) {
			return fmt.Errorf("folder archive escapes destination: %s", member.Name)
		}
		total += int64(member.UncompressedSize64)
		if total > maxExtracted {
			return fmt.Errorf("expanded folder is too large")
		}
		if member.FileInfo().IsDir() {
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		rc, err := member.Open()
		if err != nil {
			return err
		}
		out, err := os.Create(target)
		if err != nil {
			rc.Close()
			return err
		}
		if _, err := io.Copy(out, rc); err != nil {
			out.Close()
			rc.Close()
			return err
		}
		out.Close()
		rc.Close()
	}
	return nil
}

