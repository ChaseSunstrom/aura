#!/usr/bin/env bash
# install-age.sh DIR — put the pinned `age` and `age-keygen` into DIR (T-0568).
#
# One pin for the three places that need the binary: deploy/Dockerfile (jarvis-backup encrypts
# and drills with it; its age stage reads AGE_VERSION from here and builds that version from
# source, T-0758), test:config-render in .gitlab-ci.yml, and a dev box's .venv/bin for
# deploy/tests/test_backup.py. The tarball's sha256 is checked before anything is unpacked, so
# a moved release or a swapped download fails here instead of becoming the key tool.
#
# Does not cover: a compromised upstream release that was already the one pinned; the hash
# only says this is the file reviewed on 2026-11-08.
set -euo pipefail
AGE_VERSION="v1.3.2"
AGE_SHA256_AMD64="cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10"
AGE_SHA256_ARM64="6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4"

out="${1:?usage: install-age.sh DIR}"
case "$(uname -m)" in
  x86_64|amd64) arch=amd64; want="$AGE_SHA256_AMD64" ;;
  aarch64|arm64) arch=arm64; want="$AGE_SHA256_ARM64" ;;
  *) echo "install-age: no pinned build for $(uname -m)" >&2; exit 1 ;;
esac
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL --retry 5 --retry-delay 5 -o "$tmp/age.tgz" \
  "https://github.com/FiloSottile/age/releases/download/${AGE_VERSION}/age-${AGE_VERSION}-linux-${arch}.tar.gz"
echo "${want}  $tmp/age.tgz" | sha256sum -c -
tar -xzf "$tmp/age.tgz" -C "$tmp"
mkdir -p "$out"
install -m 0755 "$tmp/age/age" "$tmp/age/age-keygen" "$out/"
"$out/age" --version
