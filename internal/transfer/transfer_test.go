package transfer

import (
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestLoopbackFileTransfer(t *testing.T) {
	dir := t.TempDir()
	receiveDir := filepath.Join(dir, "received")
	srcDir := filepath.Join(dir, "src")
	if err := os.MkdirAll(receiveDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(srcDir, 0o755); err != nil {
		t.Fatal(err)
	}
	// 5 MB payload.
	payload := make([]byte, 5<<20)
	for i := range payload {
		payload[i] = byte(i)
	}
	src := filepath.Join(srcDir, "big.bin")
	if err := os.WriteFile(src, payload, 0o644); err != nil {
		t.Fatal(err)
	}

	const transferPort = 47999
	recv := NewManager(receiveDir, true, 0)
	recv.SetChunk(4096)
	if err := recv.StartReceiver(transferPort); err != nil {
		t.Fatalf("start receiver: %v", err)
	}
	defer recv.StopReceiver()

	send := NewManager(receiveDir, true, 0)
	// Sender connects to localhost on the receiver's port.
	if err := send.SendFile("127.0.0.1", transferPort, src, "Test PC", "sender-fp"); err != nil {
		t.Fatalf("send failed: %v", err)
	}

	// Verify the file was received identically.
	got := filepath.Join(receiveDir, "big.bin")
	data, err := os.ReadFile(got)
	if err != nil {
		t.Fatalf("read received: %v", err)
	}
	if len(data) != len(payload) {
		t.Fatalf("size mismatch: got %d want %d", len(data), len(payload))
	}
	for i := range payload {
		if data[i] != payload[i] {
			t.Fatalf("byte %d mismatch", i)
		}
	}
}

func TestSenderCanCancelActiveTransfer(t *testing.T) {
	dir := t.TempDir()
	receiveDir := filepath.Join(dir, "received")
	srcDir := filepath.Join(dir, "src")
	_ = os.MkdirAll(receiveDir, 0o755)
	_ = os.MkdirAll(srcDir, 0o755)

	payload := make([]byte, 80<<20)
	src := filepath.Join(srcDir, "big.bin")
	if err := os.WriteFile(src, payload, 0o644); err != nil {
		t.Fatal(err)
	}

	const transferPort = 47998
	recv := NewManager(receiveDir, true, 0)
	recv.SetChunk(4096)
	if err := recv.StartReceiver(transferPort); err != nil {
		t.Fatalf("start receiver: %v", err)
	}
	defer recv.StopReceiver()

	send := NewManager(receiveDir, true, 0)
	send.SetChunk(4096)
	// Drive the send in a goroutine so we can cancel mid-flight.
	done := make(chan error, 1)
	go func() {
		done <- send.SendFile("127.0.0.1", transferPort, src, "Test PC", "sender-fp")
	}()

	// Poll the live active map (populated from register() until SendFile
	// returns) and cancel the moment a transfer is registered. Polling the
	// map avoids racing against stale buffered progress events on loopback,
	// where transfers complete faster than the event channel can be drained.
	deadline := time.Now().Add(10 * time.Second)
	sawProgress := false
	cancelled := false
	for time.Now().Before(deadline) {
		send.mu.Lock()
		_, live := send.active["big.bin"]
		send.mu.Unlock()
		if live {
			sawProgress = true
			if !send.Cancel("big.bin") {
				t.Fatal("cancel found nothing")
			}
			cancelled = true
			break
		}
		time.Sleep(50 * time.Microsecond)
	}
	if !sawProgress {
		t.Fatal("sender never entered an active transfer to cancel")
	}
	<-done
	if !cancelled {
		t.Fatal("failed to cancel in time")
	}
	// Allow async receiver cleanup.
	time.Sleep(500 * time.Millisecond)
}

func TestApprovalHookAcceptsAndDeclines(t *testing.T) {
	dir := t.TempDir()
	receiveDir := filepath.Join(dir, "received")
	srcDir := filepath.Join(dir, "src")
	_ = os.MkdirAll(receiveDir, 0o755)
	_ = os.MkdirAll(srcDir, 0o755)

	payload := []byte("approval hook payload")
	src := filepath.Join(srcDir, "note.txt")
	if err := os.WriteFile(src, payload, 0o644); err != nil {
		t.Fatal(err)
	}

	const transferPort = 47997

	// Case 1: auto-accept OFF, hook approves -> transfer completes.
	recv := NewManager(receiveDir, false, 0)
	recv.SetChunk(4096)
	approved := make(chan struct{}, 1)
	recv.SetApproveHook(func(name string, size int64, sender string) bool {
		close(approved)
		return true
	})
	if err := recv.StartReceiver(transferPort); err != nil {
		t.Fatalf("start receiver: %v", err)
	}
	defer recv.StopReceiver()

	send := NewManager(receiveDir, false, 0)
	send.SetChunk(4096)
	if err := send.SendFile("127.0.0.1", transferPort, src, "Test PC", "sender-fp"); err != nil {
		t.Fatalf("send (approved) failed: %v", err)
	}
	select {
	case <-approved:
	case <-time.After(5 * time.Second):
		t.Fatal("approve hook was never consulted")
	}
	got := filepath.Join(receiveDir, "note.txt")
	data, err := os.ReadFile(got)
	if err != nil || string(data) != string(payload) {
		t.Fatalf("approved transfer not received correctly: err=%v data=%q", err, data)
	}

	// Case 2: hook declines -> sender gets an error.
	recv.StopReceiver()
	// Fresh receiver with a declining hook. Use a distinct file so it is
	// not treated as a duplicate of the one saved in Case 1.
	declineSrc := filepath.Join(srcDir, "note-decline.txt")
	if err := os.WriteFile(declineSrc, payload, 0o644); err != nil {
		t.Fatal(err)
	}
	recv2 := NewManager(receiveDir, false, 0)
	recv2.SetChunk(4096)
	declined := make(chan struct{}, 1)
	recv2.SetApproveHook(func(name string, size int64, sender string) bool {
		close(declined)
		return false
	})
	if err := recv2.StartReceiver(transferPort); err != nil {
		t.Fatalf("start receiver 2: %v", err)
	}
	defer recv2.StopReceiver()

	before := len(readDirNames(t, receiveDir))
	err = send.SendFile("127.0.0.1", transferPort, declineSrc, "Test PC", "sender-fp")
	if err == nil {
		t.Fatal("expected declined transfer to error, got nil")
	}
	select {
	case <-declined:
	case <-time.After(5 * time.Second):
		t.Fatal("decline hook was never consulted")
	}
	// No new file should have been written.
	if got := len(readDirNames(t, receiveDir)); got != before {
		t.Fatalf("declined transfer should not write a file: before=%d after=%d", before, got)
	}
}

func readDirNames(t *testing.T, dir string) []string {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		names = append(names, e.Name())
	}
	return names
}

// TestDuplicateSendIsSkipped verifies that re-sending an identical file
// (same name + size) already present in the receive dir is declined
// before download, so copies don't accumulate.
func TestDuplicateSendIsSkipped(t *testing.T) {
	dir := t.TempDir()
	receiveDir := filepath.Join(dir, "received")
	srcDir := filepath.Join(dir, "src")
	_ = os.MkdirAll(receiveDir, 0o755)
	_ = os.MkdirAll(srcDir, 0o755)

	payload := []byte("duplicate guard payload")
	src := filepath.Join(srcDir, "dup.txt")
	if err := os.WriteFile(src, payload, 0o644); err != nil {
		t.Fatal(err)
	}

	const transferPort = 47998
	m := NewManager(receiveDir, false, 0)
	m.SetChunk(4096)
	gotHook := make(chan string, 1)
	m.SetApproveHook(func(name string, size int64, sender string) bool {
		gotHook <- name
		return true
	})
	if err := m.StartReceiver(transferPort); err != nil {
		t.Fatalf("start: %v", err)
	}
	defer m.StopReceiver()

	send := NewManager(receiveDir, false, 0)
	send.SetChunk(4096)

	// First send: should be accepted and saved.
	if err := send.SendFile("127.0.0.1", transferPort, src, "Peer", "fp"); err != nil {
		t.Fatalf("first send failed: %v", err)
	}
	select {
	case <-gotHook:
	case <-time.After(5 * time.Second):
		t.Fatal("hook not consulted on first send")
	}
	if _, err := os.Stat(filepath.Join(receiveDir, "dup.txt")); err != nil {
		t.Fatalf("first file not saved: %v", err)
	}

	// Second send of the same file: should be skipped (duplicate), hook
	// must NOT be consulted and no second copy written. The sender gets a
	// clean "Already received" rejection.
	if err := send.SendFile("127.0.0.1", transferPort, src, "Peer", "fp"); err == nil {
		t.Fatalf("expected duplicate send to be rejected, got nil")
	}
	select {
	case n := <-gotHook:
		t.Fatalf("duplicate was not skipped, hook consulted for %q", n)
	case <-time.After(1 * time.Second):
	}
	// Exactly one file should exist.
	if got := len(readDirNames(t, receiveDir)); got != 1 {
		t.Fatalf("expected 1 file after duplicate send, got %d", got)
	}
}
// receiver re-binds the port and accepts a new connection.
func TestPauseResumeReceiver(t *testing.T) {
	const port = 45991
	m := NewManager(t.TempDir(), true, 20*1024*1024*1024)
	if err := m.StartReceiver(port); err != nil {
		t.Fatalf("start: %v", err)
	}
	m.StopReceiver()
	if err := m.StartReceiver(port); err != nil {
		t.Fatalf("resume after stop: %v", err)
	}
	// The listener should be reachable again on the same port.
	conn, err := net.DialTimeout("tcp", "127.0.0.1:"+itoa(port), 500*time.Millisecond)
	if err != nil {
		t.Fatalf("could not connect after resume: %v", err)
	}
	_ = conn.Close()
	m.StopReceiver()
}
