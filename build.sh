#!/usr/bin/env bash
# Build the PeerDrop LAN (Go) binary and package it as a .deb.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${ROOT}/dist"
PKG="${ROOT}/.pkg"
VERSION="${1:-1.0.0}"

rm -rf "${OUT}" "${PKG}"
mkdir -p "${OUT}" "${PKG}/DEBIAN" "${PKG}/usr/bin" "${PKG}/usr/share/applications" "${PKG}/usr/share/pixmaps"

# Static binary (no CGO needed at runtime; build deps only required to compile).
cd "${ROOT}"
CGO_ENABLED=1 go build -o "${PKG}/usr/bin/peerdrop-go" ./cmd/peerdrop

cp packaging/DEBIAN/control "${PKG}/DEBIAN/control"
# stamp version
sed -i "s/^Version: .*/Version: ${VERSION}/" "${PKG}/DEBIAN/control"
cp packaging/usr/share/applications/peerdrop-go.desktop "${PKG}/usr/share/applications/"
cp packaging/usr/share/pixmaps/peerdrop-go.png "${PKG}/usr/share/pixmaps/"

chmod 755 "${PKG}/usr/bin/peerdrop-go"
dpkg-deb --build --root-owner-group "${PKG}" "${OUT}/peerdrop-go_${VERSION}_amd64.deb"
echo "Built ${OUT}/peerdrop-go_${VERSION}_amd64.deb"
