// Package protocol implements the PeerDrop LAN wire format shared by the
// Python, Windows, Go, and (future) Rust clients.
package protocol

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
)

// Wire constants. These MUST match the Python reference implementation
// (app.py): same magic string, version, and ports.
const (
	Magic            = "PEERDROP1"
	ProtocolVersion  = 1
	DiscoveryPort    = 45871
	TransferPort     = 45872
	ApplicationName  = "PeerDrop LAN"
	DefaultChunkSize = 256 * 1024
	MinChunkSize     = 64 * 1024
	MaxChunkSize     = 1 * 1024 * 1024
	AutoAcceptMax    = 20 * 1024 * 1024 * 1024 // 20 GB
)

// Header is the JSON payload carried after the 4-byte length prefix on the
// TCP control channel (offers, accept replies, completion replies, presence).
type Header map[string]any

// PackHeader serializes a header exactly like the Python reference:
// json.dumps with separators (",",":") (compact, no spaces), prefixed by a
// 4-byte big-endian length. json.Marshal/Encoder always insert a space after
// ':' and ',', so we compact those structural spaces to match the Python
// framing byte-for-byte.
func PackHeader(data Header) []byte {
	raw, err := json.Marshal(data)
	if err != nil {
		panic(err)
	}
	// Remove the single space that Go inserts after ':' and ','.
	// Our header values (names, filenames, ports) never contain the exact
	// ", " or ": " structural sequences, so this is safe and exact.
	compact := bytes.ReplaceAll(raw, []byte(", "), []byte(","))
	compact = bytes.ReplaceAll(compact, []byte(": "), []byte(":"))
	out := make([]byte, 4+len(compact))
	binary.BigEndian.PutUint32(out[:4], uint32(len(compact)))
	copy(out[4:], compact)
	return out
}

// ReadHeader reads one framed header from conn. It returns an error if the
// stream is closed or the declared length cannot be read.
func ReadHeader(conn interface {
	Read([]byte) (int, error)
}) (Header, error) {
	var lenBuf [4]byte
	if _, err := readExact(conn, lenBuf[:]); err != nil {
		return nil, err
	}
	n := binary.BigEndian.Uint32(lenBuf[:])
	if n > 1<<24 { // 16 MB sanity bound on a control header
		return nil, errHeaderTooLarge(n)
	}
	body := make([]byte, int(n))
	if _, err := readExact(conn, body); err != nil {
		return nil, err
	}
	var hdr Header
	if err := json.Unmarshal(body, &hdr); err != nil {
		return nil, err
	}
	return hdr, nil
}

func readExact(conn interface {
	Read([]byte) (int, error)
}, buf []byte) (int, error) {
	total := 0
	for total < len(buf) {
		n, err := conn.Read(buf[total:])
		if n > 0 {
			total += n
		}
		if err != nil {
			return total, err
		}
	}
	return total, nil
}

type headerTooLargeError uint32

func (e headerTooLargeError) Error() string {
	return "protocol: control header too large"
}

func errHeaderTooLarge(n uint32) error { return headerTooLargeError(n) }
