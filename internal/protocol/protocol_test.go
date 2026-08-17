package protocol

import (
	"bytes"
	"encoding/binary"
	"testing"
)

func TestPackHeaderRoundTrip(t *testing.T) {
	hdr := Header{
		"magic":    Magic,
		"version":  ProtocolVersion,
		"type":     "file",
		"name":     "report.txt",
		"size":     1234,
		"sender":   "Test PC",
		"accepted": true,
	}
	packed := PackHeader(hdr)

	// Length prefix must be big-endian and equal the JSON body length.
	if len(packed) < 4 {
		t.Fatalf("packed header too short: %d bytes", len(packed))
	}
	declared := binary.BigEndian.Uint32(packed[:4])
	body := packed[4:]
	if int(declared) != len(body) {
		t.Fatalf("length prefix %d != body length %d", declared, len(body))
	}

	// Python uses json.dumps with separators (",",":") -> no spaces between
	// structural tokens. A space inside a string value (e.g. "Test PC") is fine.
	if bytes.Contains(body, []byte(": ")) || bytes.Contains(body, []byte(", ")) {
		t.Fatalf("packed JSON contains structural spaces; not wire-compatible: %q", body)
	}

	parsed, err := ReadHeader(bytes.NewReader(packed))
	if err != nil {
		t.Fatalf("ReadHeader failed: %v", err)
	}
	if parsed["magic"] != Magic {
		t.Errorf("magic mismatch: got %v", parsed["magic"])
	}
	if parsed["name"] != "report.txt" {
		t.Errorf("name mismatch: got %v", parsed["name"])
	}
	if parsed["accepted"] != true {
		t.Errorf("accepted mismatch: got %v", parsed["accepted"])
	}
}

func TestReadHeaderRejectsTruncated(t *testing.T) {
	// Declare 100 bytes but provide none.
	buf := make([]byte, 4)
	binary.BigEndian.PutUint32(buf, 100)
	_, err := ReadHeader(bytes.NewReader(buf))
	if err == nil {
		t.Fatal("expected error for truncated header, got nil")
	}
}

func TestReadHeaderTooLarge(t *testing.T) {
	buf := make([]byte, 4)
	binary.BigEndian.PutUint32(buf, 1<<25) // 32 MB, over the 16 MB bound
	_, err := ReadHeader(bytes.NewReader(buf))
	if err == nil {
		t.Fatal("expected error for oversized header, got nil")
	}
}
