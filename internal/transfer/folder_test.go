package transfer

import (
	"archive/zip"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoopbackFolderTransfer(t *testing.T) {
	dir := t.TempDir()
	receiveDir := filepath.Join(dir, "received")
	srcRoot := filepath.Join(dir, "project")
	_ = os.MkdirAll(filepath.Join(srcRoot, "nested"), 0o755)
	_ = os.WriteFile(filepath.Join(srcRoot, "readme.txt"), []byte("top level"), 0o644)
	_ = os.WriteFile(filepath.Join(srcRoot, "nested", "data.txt"), []byte("nested content"), 0o644)

	const transferPort = 47997
	recv := NewManager(receiveDir, true, 0)
	recv.SetChunk(4096)
	if err := recv.StartReceiver(transferPort); err != nil {
		t.Fatalf("start receiver: %v", err)
	}
	defer recv.StopReceiver()

	send := NewManager(receiveDir, true, 0)
	send.SetChunk(4096)
	if err := send.SendFolder("127.0.0.1", transferPort, srcRoot, "Test PC", "sender-fp"); err != nil {
		t.Fatalf("send folder failed: %v", err)
	}

	// Verify the extracted structure.
	gotReadme := filepath.Join(receiveDir, "project", "readme.txt")
	gotData := filepath.Join(receiveDir, "project", "nested", "data.txt")
	rb, err := os.ReadFile(gotReadme)
	if err != nil || string(rb) != "top level" {
		t.Fatalf("readme mismatch: %v %q", err, rb)
	}
	db, err := os.ReadFile(gotData)
	if err != nil || string(db) != "nested content" {
		t.Fatalf("nested data mismatch: %v %q", err, db)
	}
}

func TestSafeExtractRejectsTraversal(t *testing.T) {
	dir := t.TempDir()
	archive := filepath.Join(dir, "evil.zip")
	dest := filepath.Join(dir, "out")
	_ = os.MkdirAll(dest, 0o755)

	// Build a zip whose member uses a ".." segment (path traversal attempt).
	f, _ := os.Create(archive)
	zw := zip.NewWriter(f)
	w, _ := zw.Create("../escape.txt")
	_, _ = w.Write([]byte("pwned"))
	zw.Close()
	f.Close()

	err := safeExtractArchive(archive, dest)
	if err == nil {
		t.Fatal("expected safeExtractArchive to reject traversal")
	}
	if _, statErr := os.Stat(filepath.Join(dir, "escape.txt")); !os.IsNotExist(statErr) {
		t.Fatal("traversal succeeded -- escaped file exists outside dest")
	}
}

func TestSafeExtractNormalizesBackslashes(t *testing.T) {
	dir := t.TempDir()
	archive := filepath.Join(dir, "win.zip")
	dest := filepath.Join(dir, "out")
	_ = os.MkdirAll(dest, 0o755)

	// Simulate a Windows zip writer using backslashes.
	f, _ := os.Create(archive)
	zw := zip.NewWriter(f)
	w, _ := zw.Create("docs\\report.txt")
	_, _ = w.Write([]byte("hello"))
	zw.Close()
	f.Close()

	if err := safeExtractArchive(archive, dest); err != nil {
		t.Fatalf("extract failed: %v", err)
	}
	// The backslash path must be normalized to a nested file.
	got := filepath.Join(dest, "docs", "report.txt")
	data, err := os.ReadFile(got)
	if err != nil || string(data) != "hello" {
		t.Fatalf("backslash normalization failed: %v %q (entries: %s)", err, data,
			strings.Join(listDir(dest), ","))
	}
}

func listDir(root string) []string {
	var out []string
	_ = filepath.Walk(root, func(p string, info os.FileInfo, err error) error {
		if err != nil {
			return nil
		}
		if p == root {
			return nil
		}
		out = append(out, p[len(root):])
		return nil
	})
	return out
}
