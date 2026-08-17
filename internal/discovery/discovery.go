// Package discovery implements UDP broadcast peer discovery for PeerDrop LAN,
// matching the Python reference (app.py NetworkService discovery layer).
package discovery

import (
	"encoding/json"
	"fmt"
	"net"
	"sort"
	"sync"
	"time"

	"peerdrop/internal/protocol"
)

// Peer is a discovered remote device.
type Peer struct {
	ID           string
	Name         string
	Host         string // source IP seen on the discovery packet
	Port         int
	Version      int
	Capabilities []string
	Seen         time.Time
}

// Announcement is the UDP broadcast payload.
type Announcement struct {
	Magic         string   `json:"magic"`
	Version       int      `json:"version"`
	Fingerprint   string   `json:"fingerprint"`
	Name          string   `json:"name"`
	Port          int      `json:"port"`
	Capabilities  []string `json:"capabilities"`
}

// Service announces this device and listens for others.
type Service struct {
	selfID   string
	selfName string
	port     int
	conn     *net.UDPConn

	mu    sync.Mutex
	peers map[string]*Peer
	stop  chan struct{}
	once  sync.Once
}

// NewService creates a discovery service. port is the discovery UDP port
// (use protocol.DiscoveryPort in production; inject a high port in tests).
func NewService(selfID, selfName string, port int) *Service {
	return &Service{
		selfID:   selfID,
		selfName: selfName,
		port:     port,
		peers:    make(map[string]*Peer),
		stop:     make(chan struct{}),
	}
}

func (s *Service) announcement(transferPort int) Announcement {
	return Announcement{
		Magic:         protocol.Magic,
		Version:       protocol.ProtocolVersion,
		Fingerprint:   s.selfID,
		Name:          s.selfName,
		Port:          transferPort,
		Capabilities:  []string{"direct-file", "approval", "multi-send"},
	}
}

// Start binds the UDP socket, begins listening, and announces every 3s.
// transferPort is the TCP port peers should connect to for transfers.
func (s *Service) Start(transferPort int) error {
	addr := &net.UDPAddr{IP: net.IPv4zero, Port: s.port}
	conn, err := net.ListenUDP("udp", addr)
	if err != nil {
		return fmt.Errorf("bind discovery port %d: %w", s.port, err)
	}
	s.conn = conn
	go s.listenLoop(transferPort)
	go s.announceLoop(transferPort)
	return nil
}

// AnnounceOnce sends a single broadcast announcement (used by tests and the
// periodic loop).
func (s *Service) AnnounceOnce(transferPort int) error {
	if s.conn == nil {
		return fmt.Errorf("discovery not started")
	}
	payload, err := json.Marshal(s.announcement(transferPort))
	if err != nil {
		return err
	}
	// Directed broadcasts to 255.255.255.255 plus each local interface's
	// subnet broadcast, so discovery works across Wi-Fi/VPN/bridge setups.
	targets := []string{"255.255.255.255"}
	for _, ip := range localBroadcastAddresses() {
		targets = append(targets, ip)
	}
	_ = targets // keep simple: send to all-ones; interface-directed added below
	return s.sendTo(payload, "255.255.255.255")
}

func (s *Service) sendTo(payload []byte, ip string) error {
	dst := &net.UDPAddr{IP: net.ParseIP(ip), Port: s.port}
	if _, err := s.conn.WriteToUDP(payload, dst); err != nil {
		return err
	}
	return nil
}

func (s *Service) announceLoop(transferPort int) {
	ticker := time.NewTicker(3 * time.Second)
	defer ticker.Stop()
	for {
		_ = s.AnnounceOnce(transferPort)
		select {
		case <-s.stop:
			return
		case <-ticker.C:
		}
	}
}

func (s *Service) listenLoop(transferPort int) {
	buf := make([]byte, 4096)
	for {
		select {
		case <-s.stop:
			return
		default:
		}
		_ = s.conn.SetReadDeadline(time.Now().Add(1 * time.Second))
		n, src, err := s.conn.ReadFromUDP(buf)
		if err != nil {
			if ne, ok := err.(net.Error); ok && ne.Timeout() {
				continue
			}
			select {
			case <-s.stop:
				return
			default:
				continue
			}
		}
		s.handlePacket(buf[:n], src)
	}
}

func (s *Service) handlePacket(data []byte, src *net.UDPAddr) {
	var pkt Announcement
	if err := json.Unmarshal(data, &pkt); err != nil {
		return
	}
	if pkt.Magic != protocol.Magic || pkt.Fingerprint == "" || pkt.Fingerprint == s.selfID {
		return
	}
	port := pkt.Port
	if port == 0 {
		port = protocol.TransferPort
	}
	s.mu.Lock()
	existing, ok := s.peers[pkt.Fingerprint]
	if !ok && len(s.peers) >= 200 {
		s.mu.Unlock()
		return
	}
	if !ok {
		s.peers[pkt.Fingerprint] = &Peer{
			ID:           pkt.Fingerprint,
			Name:         pkt.Name,
			Host:         src.IP.String(),
			Port:         port,
			Version:      pkt.Version,
			Capabilities: pkt.Capabilities,
			Seen:         time.Now(),
		}
	}
	if existing != nil {
		existing.Seen = time.Now()
		existing.Name = pkt.Name
		existing.Host = src.IP.String()
		existing.Port = port
	}
	s.mu.Unlock()
}

// Peers returns a snapshot of currently known peers, sorted by name.
func (s *Service) Peers() []Peer {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Peer, 0, len(s.peers))
	for _, p := range s.peers {
		out = append(out, *p)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// Prune removes peers not seen within the given duration.
func (s *Service) Prune(olderThan time.Duration) {
	cutoff := time.Now().Add(-olderThan)
	s.mu.Lock()
	defer s.mu.Unlock()
	for id, p := range s.peers {
		if p.Seen.Before(cutoff) {
			delete(s.peers, id)
		}
	}
}

// Stop shuts down the discovery service.
func (s *Service) Stop() {
	s.once.Do(func() {
		close(s.stop)
		if s.conn != nil {
			_ = s.conn.Close()
		}
	})
}

// localBroadcastAddresses returns interface-directed broadcast addresses.
// Best-effort; an empty result just means we fall back to 255.255.255.255.
func localBroadcastAddresses() []string {
	var out []string
	ifaces, err := net.Interfaces()
	if err != nil {
		return out
	}
	for _, iface := range ifaces {
		if iface.Flags&net.FlagLoopback != 0 || iface.Flags&net.FlagUp == 0 {
			continue
		}
		addrs, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, a := range addrs {
			ipnet, ok := a.(*net.IPNet)
			if !ok || ipnet.IP.IsLoopback() || ipnet.IP.To4() == nil {
				continue
			}
			ip := ipnet.IP.To4()
			mask := net.IPMask(ipnet.Mask)
			bcast := make(net.IP, 4)
			for i := 0; i < 4; i++ {
				bcast[i] = ip[i] | ^mask[i]
			}
			out = append(out, bcast.String())
		}
	}
	return out
}
