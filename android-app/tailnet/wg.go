package tailnet

// WireGuard mode (the owner, 2026-10-29: "scan a wireguard tunnel instead of
// tailscale in the app for the car"). Home is reached over WireGuard since
// Tailscale went (2026-10-27), and the WireGuard app's VPN blocks the car
// link's Wi-Fi bind exactly as Tailscale's did. So, like the tailnet node,
// the tunnel runs in-process: wireguard-go over a userspace network stack,
// its UDP socket on the phone's ordinary network, and only the app's own
// dials -- the ones whose host is in the config's AllowedIPs -- go inside.
//
// Not covered: UDP and ICMP through the tunnel (the app only dials TCP), and
// an endpoint whose address changes (resolved once at start, as wg-quick
// does; a restart of the link picks the new one up).

import (
	"context"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"net"
	"net/netip"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"ai.jarvis/tailnet/wgnet"
	"github.com/tailscale/wireguard-go/conn"
	"github.com/tailscale/wireguard-go/device"
)

type wgLink struct {
	dev     *device.Device
	net     *wgnet.Net
	allowed []netip.Prefix
	dns     bool
	// ponytail: covered hosts are cached until the link restarts; an uncovered
	// one is resolved again each time (a lookup made before the first handshake
	// must not pin a home name outside the tunnel).
	covered sync.Map
	pinMu   sync.Mutex
	pins    map[string]netip.Addr // a home name's last covered address, kept in pinFile
	pinFile string
}

var wg *wgLink // under mu

// maxPins bounds the home names remembered: a house has a handful.
const maxPins = 32

// lookupHost is the phone's own resolver; a test stands in for the house's DNS.
var lookupHost = net.DefaultResolver.LookupHost

// StartWireGuard brings up the tunnel a wg-quick config describes (what the
// WireGuard app's QR holds), replacing one already up. Forward and Proxy then
// dial through it. dir keeps what the link learns across restarts (the app's
// files dir); "" keeps it in memory only.
func StartWireGuard(conf, dir string) (err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("wireguard failed to start: %v", r)
		}
	}()
	c, err := parseWG(conf)
	if err != nil {
		return err
	}
	tunDev, tnet, err := wgnet.CreateNetTUN(c.addrs, c.dns, c.mtu)
	if err != nil {
		return err
	}
	dev := device.NewDevice(tunDev, conn.NewDefaultBind(), device.NewLogger(device.LogLevelSilent, ""))
	if err := dev.IpcSet(c.uapi); err != nil {
		dev.Close()
		return err
	}
	if err := dev.Up(); err != nil {
		dev.Close()
		return err
	}
	// Swapped in only once it is up: a config that fails leaves the link that
	// was working.
	// ponytail: a new config with the old one's ListenPort fails while the old
	// holds the port; Forget, then scan, until that matters.
	mu.Lock()
	old := wg
	link := &wgLink{dev: dev, net: tnet, allowed: c.allowed, dns: len(c.dns) > 0, pins: map[string]netip.Addr{}}
	if dir != "" {
		link.pinFile = filepath.Join(dir, "wireguard-hosts")
		link.pins = readPins(link.pinFile)
	}
	wg = link
	mu.Unlock()
	if old != nil {
		old.dev.Close()
	}
	return nil
}

// Covers reports whether host (a name or an address) goes through the
// WireGuard tunnel: one of its addresses is in the config's AllowedIPs.
// False when no tunnel is up.
func Covers(host string) bool {
	mu.Lock()
	w := wg
	mu.Unlock()
	if w == nil {
		return false
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return w.via(ctx, host).IsValid()
}

// via is the address to dial host at inside the tunnel, or the zero Addr when
// the tunnel does not cover it. A name is asked of the tunnel's DNS first (a
// home name may resolve nowhere else), then of the phone's. A covered answer
// is remembered, and a name with no carried answer falls back to it: a home name
// in the house's DNS only (jarvis.home.example, 2026-10-29) resolves on
// home Wi-Fi and nowhere else, and the scanned config's DNS rarely is the
// house's. Not covered: a name never resolved at home, with no tunnel DNS
// that knows it, stays unreachable.
func (w *wgLink) via(ctx context.Context, host string) netip.Addr {
	if v, ok := w.covered.Load(host); ok {
		return v.(netip.Addr)
	}
	for _, n := range w.names(ctx, host) {
		a, err := netip.ParseAddr(n)
		if err != nil || a.Unmap().IsLoopback() {
			continue
		}
		if a = a.Unmap(); w.carries(a) {
			w.covered.Store(host, a)
			// Only a home address: a full tunnel covers every site the web
			// view opens, and a public name must never dial a stale address.
			if a.IsPrivate() && a.String() != host {
				w.pin(host, a)
			}
			return a
		}
	}
	// No answer the tunnel carries: no resolver here knows the name, or one
	// answers every name with itself (a hotel's portal). The pin is used but
	// not cached, so the house's own answer replaces it once DNS has one.
	w.pinMu.Lock()
	a, ok := w.pins[host]
	w.pinMu.Unlock()
	if ok && w.carries(a) {
		return a
	}
	return netip.Addr{}
}

// names are host's addresses: itself when it is one, else the tunnel's DNS's
// answer, else the phone's.
func (w *wgLink) names(ctx context.Context, host string) []string {
	if ip, err := netip.ParseAddr(host); err == nil {
		return []string{ip.String()}
	}
	var names []string
	if w.dns {
		c, cancel := context.WithTimeout(ctx, 3*time.Second)
		names, _ = w.net.LookupContextHost(c, host)
		cancel()
	}
	if len(names) == 0 {
		names, _ = lookupHost(ctx, host)
	}
	return names
}

// handshakeWindow is WireGuard's RejectAfterTime: no data flows on a session
// older than this, so an older handshake means the server has gone silent.
var handshakeWindow = 180 * time.Second

// diagnoseWait bounds Diagnose; a test shortens it.
var diagnoseWait = 8 * time.Second

// Diagnose checks the tunnel to host:port step by step for Settings (the owner,
// 2026-10-30: "the WG tunnel wont connect to jarvis") and reports the first
// step that fails, as step|detail: "down|" (no tunnel), "name|" (no resolver
// or remembered pin knows host), "covered|<addr>" (host's address is outside
// AllowedIPs), "handshake|<addr>" (a dial through the tunnel found the server
// silent: the dial is what starts a handshake, so this comes after the
// address), "answer|<addr>|<error>" (the server answers, Jarvis does not), or
// "ok|<addr>". Not covered: TLS or HTTP above the TCP connect, and a server that
// went silent less than 180 s after its last handshake reads as "answer".
func Diagnose(host string, port int) string {
	mu.Lock()
	w := wg
	mu.Unlock()
	if w == nil {
		return "down|"
	}
	ctx, cancel := context.WithTimeout(context.Background(), diagnoseWait)
	defer cancel()
	a := w.via(ctx, host)
	if !a.IsValid() {
		if n := w.names(ctx, host); len(n) > 0 {
			return "covered|" + n[0]
		}
		// The tunnel's DNS is asked through the tunnel: a silent server would
		// otherwise read as an unknown name.
		if w.dns && !w.fresh() {
			return "handshake|"
		}
		return "name|"
	}
	c, err := w.net.DialContextTCPAddrPort(ctx, netip.AddrPortFrom(a, uint16(port)))
	if err != nil {
		if !w.fresh() {
			return "handshake|" + a.String()
		}
		return "answer|" + a.String() + "|" + err.Error()
	}
	_ = c.Close()
	return "ok|" + a.String()
}

func (w *wgLink) carries(a netip.Addr) bool {
	for _, p := range w.allowed {
		if p.Contains(a) {
			return true
		}
	}
	return false
}

// pin remembers host's covered address, in pinFile when there is one. Past
// maxPins a new name pushes out another, so a table filled by other private
// names never stops the home name being learned. A failed write only loses the
// memory across a restart.
func (w *wgLink) pin(host string, a netip.Addr) {
	w.pinMu.Lock()
	defer w.pinMu.Unlock()
	old, ok := w.pins[host]
	if ok && old == a {
		return
	}
	if !ok && len(w.pins) >= maxPins {
		// ponytail: an arbitrary name goes (map order), not the oldest; a home
		// name pushed out is learned again on home Wi-Fi.
		for h := range w.pins {
			delete(w.pins, h)
			break
		}
	}
	w.pins[host] = a
	if w.pinFile == "" {
		return
	}
	var b strings.Builder
	for h, ip := range w.pins {
		fmt.Fprintf(&b, "%s %s\n", h, ip)
	}
	// A temp file of its own (0600): a link being replaced may still be writing.
	f, err := os.CreateTemp(filepath.Dir(w.pinFile), ".wireguard-hosts-*")
	if err == nil {
		_, err = f.WriteString(b.String())
		if cerr := f.Close(); err == nil {
			err = cerr
		}
		if err == nil {
			err = os.Rename(f.Name(), w.pinFile)
		}
		if err != nil {
			_ = os.Remove(f.Name())
		}
	}
	if err != nil {
		log.Printf("tailnet: remembering %s: %v", host, err)
	}
}

func readPins(file string) map[string]netip.Addr {
	pins := map[string]netip.Addr{}
	b, _ := os.ReadFile(file)
	for _, line := range strings.Split(string(b), "\n") {
		if h, v, ok := strings.Cut(line, " "); ok {
			if a, err := netip.ParseAddr(v); err == nil {
				pins[h] = a
			}
		}
	}
	return pins
}

// dial goes through the tunnel when it covers the host, and straight out
// otherwise: the web view's proxy sends everything here in WireGuard mode.
func (w *wgLink) dial(ctx context.Context, network, addr string) (net.Conn, error) {
	host, port, err := net.SplitHostPort(addr)
	if err != nil {
		return nil, err
	}
	if ip := w.via(ctx, host); ip.IsValid() {
		p, err := strconv.ParseUint(port, 10, 16)
		if err != nil {
			return nil, err
		}
		return w.net.DialContextTCPAddrPort(ctx, netip.AddrPortFrom(ip, uint16(p)))
	}
	var d net.Dialer
	return d.DialContext(ctx, network, addr)
}

// fresh is whether the peer answered a handshake within handshakeWindow.
// state() can't say this: the handshake time it reads is never cleared, so a
// server that answered once and then went silent stays "Running".
func (w *wgLink) fresh() bool {
	uapi, err := w.dev.IpcGet()
	if err != nil {
		return false
	}
	for _, line := range strings.Split(uapi, "\n") {
		if v, ok := strings.CutPrefix(line, "last_handshake_time_sec="); ok {
			s, _ := strconv.ParseInt(v, 10, 64)
			return s > 0 && time.Since(time.Unix(s, 0)) < handshakeWindow
		}
	}
	return false
}

// state is "Running" once the peer has answered a handshake, else "Starting".
func (w *wgLink) state() string {
	uapi, err := w.dev.IpcGet()
	if err != nil {
		return err.Error()
	}
	for _, line := range strings.Split(uapi, "\n") {
		if v, ok := strings.CutPrefix(line, "last_handshake_time_sec="); ok && v != "0" {
			return "Running"
		}
	}
	return "Starting"
}

type wgConf struct {
	uapi    string
	addrs   []netip.Addr
	dns     []netip.Addr
	mtu     int
	allowed []netip.Prefix
}

// parseWG turns a wg-quick config into wireguard-go's UAPI. Keys are
// case-insensitive and peer lines may come in any order, as wg-quick allows;
// what only wg-quick acts on (Table, PostUp, SaveConfig, ...) is skipped.
func parseWG(conf string) (*wgConf, error) {
	c := &wgConf{mtu: 1420}
	var iface strings.Builder
	type peer struct {
		pub  string
		rest strings.Builder
	}
	var peers []*peer
	section := ""
	for _, raw := range strings.Split(conf, "\n") {
		line := strings.TrimSpace(raw)
		if i := strings.IndexByte(line, '#'); i >= 0 {
			line = strings.TrimSpace(line[:i])
		}
		if strings.HasPrefix(line, "[") {
			section = strings.ToLower(strings.Trim(line, "[] "))
			if section == "peer" {
				peers = append(peers, &peer{})
			}
			continue
		}
		k, v, ok := strings.Cut(line, "=") // a base64 key keeps its trailing '='
		if !ok {
			continue
		}
		k, v = strings.ToLower(strings.TrimSpace(k)), strings.TrimSpace(v)
		switch section + "." + k {
		case "interface.privatekey":
			h, err := hexKey(v)
			if err != nil {
				return nil, fmt.Errorf("PrivateKey: %w", err)
			}
			fmt.Fprintf(&iface, "private_key=%s\n", h)
		case "interface.listenport":
			fmt.Fprintf(&iface, "listen_port=%s\n", v)
		case "interface.address":
			for _, a := range list(v) {
				if p, err := netip.ParsePrefix(a); err == nil {
					c.addrs = append(c.addrs, p.Addr())
				} else if ip, err := netip.ParseAddr(a); err == nil {
					c.addrs = append(c.addrs, ip)
				} else {
					return nil, fmt.Errorf("Address %q: %w", a, err)
				}
			}
		case "interface.dns":
			for _, a := range list(v) {
				if ip, err := netip.ParseAddr(a); err == nil { // search domains are skipped
					c.dns = append(c.dns, ip)
				}
			}
		case "interface.mtu":
			n, err := strconv.Atoi(v)
			if err != nil {
				return nil, fmt.Errorf("MTU: %w", err)
			}
			c.mtu = n
		case "peer.publickey", "peer.presharedkey", "peer.endpoint", "peer.allowedips", "peer.persistentkeepalive":
			p := peers[len(peers)-1]
			switch k {
			case "publickey":
				h, err := hexKey(v)
				if err != nil {
					return nil, fmt.Errorf("PublicKey: %w", err)
				}
				p.pub = h
			case "presharedkey":
				h, err := hexKey(v)
				if err != nil {
					return nil, fmt.Errorf("PresharedKey: %w", err)
				}
				fmt.Fprintf(&p.rest, "preshared_key=%s\n", h)
			case "endpoint":
				ep, err := net.ResolveUDPAddr("udp", v)
				if err != nil {
					return nil, fmt.Errorf("Endpoint: %w", err)
				}
				fmt.Fprintf(&p.rest, "endpoint=%s\n", ep)
			case "allowedips":
				for _, a := range list(v) {
					pr, err := netip.ParsePrefix(a)
					if err != nil {
						return nil, fmt.Errorf("AllowedIPs %q: %w", a, err)
					}
					c.allowed = append(c.allowed, pr.Masked())
					fmt.Fprintf(&p.rest, "allowed_ip=%s\n", pr.Masked())
				}
			case "persistentkeepalive":
				if v != "off" {
					fmt.Fprintf(&p.rest, "persistent_keepalive_interval=%s\n", v)
				}
			}
		}
	}
	if iface.Len() == 0 || !strings.Contains(iface.String(), "private_key=") || len(c.addrs) == 0 || len(peers) == 0 {
		return nil, errors.New("not a WireGuard config: it needs [Interface] PrivateKey and Address, and a [Peer]")
	}
	var b strings.Builder
	b.WriteString(iface.String())
	for _, p := range peers {
		if p.pub == "" {
			return nil, errors.New("a [Peer] has no PublicKey")
		}
		fmt.Fprintf(&b, "public_key=%s\n%s", p.pub, p.rest.String())
	}
	c.uapi = b.String()
	// A DNS server the tunnel does not carry is dropped: wireguard-go drops its
	// packets, and every name would wait out the tunnel's lookup first.
	var dns []netip.Addr
	for _, d := range c.dns {
		for _, p := range c.allowed {
			if p.Contains(d) {
				dns = append(dns, d)
				break
			}
		}
	}
	c.dns = dns
	return c, nil
}

func hexKey(b64 string) (string, error) {
	k, err := base64.StdEncoding.DecodeString(b64)
	if err != nil || len(k) != 32 {
		return "", errors.New("not a 32-byte base64 key")
	}
	return hex.EncodeToString(k), nil
}

func list(v string) []string {
	var out []string
	for _, s := range strings.Split(v, ",") {
		if s = strings.TrimSpace(s); s != "" {
			out = append(out, s)
		}
	}
	return out
}
