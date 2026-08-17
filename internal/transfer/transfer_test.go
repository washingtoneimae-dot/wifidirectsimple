package transfer

import (
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
	// No partial file should remain.
	entries, _ := os.ReadDir(receiveDir)
	if len(entries) != 0 {
		t.Fatalf("partial file left after cancel: %v", entries)
	}
}
