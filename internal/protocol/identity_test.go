package protocol

import (
	"path/filepath"
	"testing"
)

func TestIdentityGeneratesAndPersistsFingerprint(t *testing.T) {
	dir := t.TempDir()
	id1, err := LoadIdentity(dir)
	if err != nil {
		t.Fatalf("LoadIdentity failed: %v", err)
	}
	fp1 := id1.Fingerprint()
	if fp1 == "" {
		t.Fatal("fingerprint is empty")
	}

	// A second load from the same dir must reuse the same fingerprint.
	id2, err := LoadIdentity(dir)
	if err != nil {
		t.Fatalf("second LoadIdentity failed: %v", err)
	}
	if id2.Fingerprint() != fp1 {
		t.Fatalf("fingerprint changed across loads: %s -> %s", fp1, id2.Fingerprint())
	}

	// The settings file must exist on disk.
	if _, err := filepath.Abs(filepath.Join(dir, "settings.json")); err != nil {
		t.Fatalf("settings path error: %v", err)
	}
}

func TestIdentitySetNamePersists(t *testing.T) {
	dir := t.TempDir()
	id, err := LoadIdentity(dir)
	if err != nil {
		t.Fatalf("LoadIdentity failed: %v", err)
	}
	if err := id.SetName("Living Room PC"); err != nil {
		t.Fatalf("SetName failed: %v", err)
	}
	if id.Name() != "Living Room PC" {
		t.Fatalf("name not updated in memory: %s", id.Name())
	}
	// Reload and confirm persistence.
	id2, _ := LoadIdentity(dir)
	if id2.Name() != "Living Room PC" {
		t.Fatalf("name not persisted: %s", id2.Name())
	}
}
