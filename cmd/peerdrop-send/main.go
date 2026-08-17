package main

import (
	"fmt"
	"os"
	"path/filepath"

	"peerdrop/internal/protocol"
	"peerdrop/internal/transfer"
)

// peerdrop-send is a small CLI to push a file (or folder) to a peer's
// host:port using the PeerDrop wire protocol. Useful for headless tests and
// cross-app (Python/Windows) interop verification.
func main() {
	if len(os.Args) < 4 {
		fmt.Fprintf(os.Stderr, "usage: %s <host> <port> <path> [name]\n", os.Args[0])
		os.Exit(2)
	}
	host := os.Args[1]
	portStr := os.Args[2]
	path := os.Args[3]

	port := 0
	fmt.Sscanf(portStr, "%d", &port)
	if port == 0 {
		port = protocol.TransferPort
	}

	id, err := protocol.LoadIdentity("")
	if err != nil {
		id, _ = protocol.LoadIdentity(os.TempDir())
	}

	fi, statErr := os.Stat(path)
	if statErr != nil {
		fmt.Fprintln(os.Stderr, "cannot access path:", statErr)
		os.Exit(1)
	}

	mgr := transfer.NewManager(os.TempDir(), true, protocol.AutoAcceptMax)
	var sendErr error
	if fi.IsDir() {
		sendErr = mgr.SendFolder(host, port, path, id.Name(), id.Fingerprint())
	} else {
		sendErr = mgr.SendFile(host, port, path, id.Name(), id.Fingerprint())
	}
	if sendErr != nil {
		fmt.Fprintln(os.Stderr, "send failed:", sendErr)
		os.Exit(1)
	}
	fmt.Printf("sent %s -> %s:%d\n", filepath.Base(path), host, port)
}
