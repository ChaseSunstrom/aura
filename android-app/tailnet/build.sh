#!/usr/bin/env bash
# Builds tailnet.aar (the in-app WireGuard link, wg.go + link.go) with gomobile, in
# the official Go image so the host needs neither Go nor gomobile. arm64 only:
# an x86_64 copy was 23 MB for emulators alone (hud-size, 2026-09-25), and the
# library loads only when a WireGuard config is scanned. Needs the
# Android SDK with an NDK: ANDROID_HOME, and ANDROID_NDK_HOME or the one NDK
# under $ANDROID_HOME/ndk. Output: ../app/libs/tailnet.aar.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
sdk="${ANDROID_HOME:?set ANDROID_HOME}"
ndk="${ANDROID_NDK_HOME:-$(ls -d "$sdk"/ndk/* | sort -V | tail -1)}"
mkdir -p "$here/../app/libs"
docker run --rm \
  -v "$here":/src -v "$sdk":/sdk:ro -v "$ndk":/ndk:ro \
  -v jarvis-tailnet-gocache:/go/pkg \
  -e ANDROID_HOME=/sdk -e ANDROID_NDK_HOME=/ndk -e GOFLAGS=-mod=mod \
  -w /src golang:1.26-bookworm bash -c '
    set -e
    apt-get update -qq >/dev/null && apt-get install -y -qq openjdk-17-jdk-headless >/dev/null
    go install golang.org/x/mobile/cmd/gomobile@latest golang.org/x/mobile/cmd/gobind@latest
    export PATH=$PATH:/root/go/bin
    [ -f go.mod ] || go mod init ai.jarvis/tailnet
    go get golang.org/x/mobile@latest
    go mod tidy
    gomobile init
    gomobile bind -target=android/arm64 -androidapi 26 -javapkg ai.jarvis.app.tailnet -ldflags "-s -w" -o /src/tailnet.aar .
  '
mv "$here/tailnet.aar" "$here/../app/libs/tailnet.aar"
rm -f "$here/tailnet-sources.jar"
echo "built app/libs/tailnet.aar"
