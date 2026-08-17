package protocol

import (
	"crypto/rand"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
)

// SettingsFile is the on-disk JSON settings document, mirroring the Python
// reference (settings.json under ~/.config/PeerDropLAN).
type SettingsFile struct {
	DeviceID     string `json:"device_id"`
	DeviceName   string `json:"device_name"`
	ReceiveDir   string `json:"receive_dir"`
	AutoAccept   bool   `json:"auto_accept"`
	ChunkSize    string `json:"chunk_size"` // "auto" or a fixed size string
	TransferPort int    `json:"transfer_port"`
}

// Identity loads and persists the local device fingerprint and display name.
type Identity struct {
	mu     sync.Mutex
	path   string
	config SettingsFile
}

// LoadIdentity reads settings from dir/settings.json, creating a stable
// device_id (UUID) and default name on first run. If dir is empty, it
// defaults to ~/.config/PeerDropLAN.
func LoadIdentity(dir string) (*Identity, error) {
	if dir == "" {
		cfg, err := os.UserConfigDir()
		if err != nil {
			return nil, err
		}
		dir = filepath.Join(cfg, "PeerDropLAN")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	path := filepath.Join(dir, "settings.json")
	id := &Identity{path: path, config: SettingsFile{
		DeviceName:   defaultName(),
		ReceiveDir:   defaultReceiveDir(),
		ChunkSize:    "auto",
		TransferPort: TransferPort,
	}}
	data, err := os.ReadFile(path)
	if err == nil {
		if err := json.Unmarshal(data, &id.config); err != nil {
			return nil, fmt.Errorf("parse settings: %w", err)
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	if id.config.DeviceID == "" {
		id.config.DeviceID = newUUID()
		if err := id.save(); err != nil {
			return nil, err
		}
	}
	return id, nil
}

// newUUID returns a RFC 4122 v4 UUID string using only the standard library.
func newUUID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		// Crypto rand never fails in practice; fall back to a time-based id.
		return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x", 0, 0, 0, 0, 0)
	}
	b[6] = (b[6] & 0x0f) | 0x40 // version 4
	b[8] = (b[8] & 0x3f) | 0x80 // variant 10
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

// Fingerprint returns the stable device UUID.
func (i *Identity) Fingerprint() string {
	i.mu.Lock()
	defer i.mu.Unlock()
	return i.config.DeviceID
}

// Name returns the display name shown to other PCs.
func (i *Identity) Name() string {
	i.mu.Lock()
	defer i.mu.Unlock()
	if i.config.DeviceName == "" {
		return defaultName()
	}
	return i.config.DeviceName
}

// SetName updates and persists the display name.
func (i *Identity) SetName(name string) error {
	i.mu.Lock()
	i.config.DeviceName = name
	i.mu.Unlock()
	return i.save()
}

// Config returns a copy of the current settings.
func (i *Identity) Config() SettingsFile {
	i.mu.Lock()
	defer i.mu.Unlock()
	return i.config
}

// SetReceiveDir updates and persists the receive folder.
func (i *Identity) SetReceiveDir(dir string) error {
	i.mu.Lock()
	i.config.ReceiveDir = dir
	i.mu.Unlock()
	return i.save()
}

func (i *Identity) save() error {
	i.mu.Lock()
	defer i.mu.Unlock()
	data, err := json.MarshalIndent(i.config, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(i.path, data, 0o644)
}

func defaultName() string {
	host, err := os.Hostname()
	if err != nil || host == "" {
		return "Unknown PC"
	}
	return host
}

func defaultReceiveDir() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return "Downloads/PeerDrop"
	}
	return filepath.Join(home, "Downloads", "PeerDrop")
}
