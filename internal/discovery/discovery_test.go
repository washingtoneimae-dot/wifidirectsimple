package discovery

import (
	"encoding/json"
	"net"
	"testing"
	"time"

	"peerdrop/internal/protocol"
)

// TestDiscoverySeesPeer sends a crafted announcement (as a peer would) to a
// single listener and verifies it registers the peer.
func TestDiscoverySeesPeer(t *testing.T) {
	const testPort = 48123
	listener := NewService("listener-id", "Listener PC", testPort)
	if err := listener.Start(protocol.TransferPort); err != nil {
		t.Fatalf("listener start: %v", err)
	}
	defer listener.Stop()

	// Emulate a peer broadcasting an announcement on the same port.
	payload, _ := json.Marshal(Announcement{
		Magic:        protocol.Magic,
		Version:      protocol.ProtocolVersion,
		Fingerprint:  "announcer-id",
		Name:         "Announcer PC",
		Port:         protocol.TransferPort,
		Capabilities: []string{"direct-file", "approval", "multi-send"},
	})
	probe, err := net.Dial("udp", "127.0.0.1:"+itoa(testPort))
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer probe.Close()
	if _, err := probe.Write(payload); err != nil {
		t.Fatalf("write announcement: %v", err)
	}

	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if peers := listener.Peers(); len(peers) > 0 {
			p := peers[0]
			if p.ID != "announcer-id" {
				t.Fatalf("unexpected peer id: %s", p.ID)
			}
			if p.Name != "Announcer PC" {
				t.Fatalf("unexpected peer name: %s", p.Name)
			}
			if p.Port != protocol.TransferPort {
				t.Fatalf("unexpected transfer port: %d", p.Port)
			}
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatal("listener never discovered the peer")
}

func TestDiscoveryIgnoresSelf(t *testing.T) {
	const testPort = 48124
	s := NewService("self-id", "Self PC", testPort)
	if err := s.Start(protocol.TransferPort); err != nil {
		t.Fatalf("start: %v", err)
	}
	defer s.Stop()
	if err := s.AnnounceOnce(protocol.TransferPort); err != nil {
		t.Fatalf("announce: %v", err)
	}
	time.Sleep(200 * time.Millisecond)
	if peers := s.Peers(); len(peers) != 0 {
		t.Fatalf("service discovered itself: %+v", peers)
	}
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
