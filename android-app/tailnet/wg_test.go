package tailnet

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"ai.jarvis/tailnet/wgnet"
	"github.com/tailscale/wireguard-go/conn"
	"github.com/tailscale/wireguard-go/device"
	"golang.org/x/crypto/curve25519"
)

func keypair(t *testing.T) (priv, pub []byte) {
	priv = make([]byte, 32)
	if _, err := rand.Read(priv); err != nil {
		t.Fatal(err)
	}
	priv[0] &= 248
	priv[31] = (priv[31] & 127) | 64
	pub, err := curve25519.X25519(priv, curve25519.Basepoint)
	if err != nil {
		t.Fatal(err)
	}
	return priv, pub
}

func freeUDP(t *testing.T) int {
	pc, err := net.ListenPacket("udp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer pc.Close()
	return pc.LocalAddr().(*net.UDPAddr).Port
}

// lastHouse is wgHouse's server, for a test that takes it away.
var lastHouse *device.Device

// wgHouse stands a second, in-process WireGuard peer on loopback in for the
// house's server, with a TCP service ("home" on 10.9.0.1:80) inside its tunnel
// the phone can only reach through the tunnel, and returns the phone's config.
func wgHouse(t *testing.T) string {
	sPriv, sPub := keypair(t)
	cPriv, cPub := keypair(t)

	// The house: 10.9.0.1 inside the tunnel, "home" on port 80.
	tunDev, tnet, err := wgnet.CreateNetTUN([]netip.Addr{netip.MustParseAddr("10.9.0.1")}, nil, 1420)
	if err != nil {
		t.Fatal(err)
	}
	house := device.NewDevice(tunDev, conn.NewDefaultBind(), device.NewLogger(device.LogLevelSilent, ""))
	// The house is Tailscale's wireguard-go fork, which (unlike a real server)
	// does not learn a peer's address from its packets, so it is told the
	// phone's; the phone's config pins that port with ListenPort.
	port, phonePort := freeUDP(t), freeUDP(t)
	if err := house.IpcSet(fmt.Sprintf("private_key=%s\nlisten_port=%d\npublic_key=%s\nendpoint=127.0.0.1:%d\nallowed_ip=10.9.0.2/32\n",
		hex.EncodeToString(sPriv), port, hex.EncodeToString(cPub), phonePort)); err != nil {
		t.Fatal(err)
	}
	if err := house.Up(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(house.Close)
	lastHouse = house
	l, err := tnet.ListenTCPAddrPort(netip.MustParseAddrPort("10.9.0.1:80"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = l.Close() })
	go func() {
		for {
			c, err := l.Accept()
			if err != nil {
				return
			}
			_, _ = io.WriteString(c, "home")
			_ = c.Close()
		}
	}()
	return fmt.Sprintf(`# exported by the WireGuard app
[Interface]
PrivateKey = %s
Address = 10.9.0.2/32
ListenPort = %d

[Peer]
PublicKey = %s
AllowedIPs = 10.9.0.1/32
Endpoint = 127.0.0.1:%d
PersistentKeepalive = 25
`, base64.StdEncoding.EncodeToString(cPriv), phonePort, base64.StdEncoding.EncodeToString(sPub), port)
}

// readVia reads what the house's service answers through a Forward to target.
func readVia(t *testing.T, target string) string {
	t.Helper()
	fwd, err := Forward(target)
	if err != nil {
		t.Fatal(err)
	}
	c, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", fwd), 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	_ = c.SetDeadline(time.Now().Add(20 * time.Second))
	got, _ := io.ReadAll(c)
	return string(got)
}

// The phone scans a wg-quick config and reaches home through it.
func TestWireGuardCarriesForwardAndCovers(t *testing.T) {
	conf := wgHouse(t)
	// Outside the tunnel: a host its AllowedIPs do not cover is dialled directly.
	outside := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, "outside")
	}))
	defer outside.Close()

	if err := StartWireGuard(conf, ""); err != nil {
		t.Fatal(err)
	}
	defer Stop()

	if !Covers("10.9.0.1") || Covers("8.8.8.8") || Covers("127.0.0.1") {
		t.Fatalf("covers: 10.9.0.1 %v, 8.8.8.8 %v, 127.0.0.1 %v", Covers("10.9.0.1"), Covers("8.8.8.8"), Covers("127.0.0.1"))
	}

	if got := readVia(t, "10.9.0.1:80"); got != "home" {
		t.Fatalf("through the tunnel: got %q", got)
	}
	if s := State(); s != "Running" {
		t.Fatalf("state after a handshake: %q", s)
	}

	direct, err := Forward(strings.TrimPrefix(outside.URL, "http://"))
	if err != nil {
		t.Fatal(err)
	}
	resp, err := (&http.Client{Timeout: 10 * time.Second}).Get(fmt.Sprintf("http://127.0.0.1:%d/", direct))
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	_ = resp.Body.Close()
	if string(body) != "outside" {
		t.Fatalf("outside the tunnel: got %q", body)
	}
}

// Settings' check (T-0473): the first broken step is named, in the order a
// person checks them, and a working tunnel says where Jarvis answered.
func TestWireGuardDiagnoseNamesTheFailingStep(t *testing.T) {
	if got := Diagnose("10.9.0.1", 80); got != "down|" {
		t.Fatalf("no tunnel: %q", got)
	}
	lookupHost = func(ctx context.Context, host string) ([]string, error) {
		if host == "public.test" {
			return []string{"8.8.8.8"}, nil
		}
		return nil, &net.DNSError{Err: "no such host", Name: host, IsNotFound: true}
	}
	defer func() { lookupHost = net.DefaultResolver.LookupHost }()
	wait := diagnoseWait
	diagnoseWait = 2 * time.Second
	defer func() { diagnoseWait = wait }()

	conf := wgHouse(t)
	// A server that never answers: the dial that starts the handshake hears nothing.
	dead := regexp.MustCompile(`Endpoint = 127\.0\.0\.1:\d+`).ReplaceAllString(conf, fmt.Sprintf("Endpoint = 127.0.0.1:%d", freeUDP(t)))
	if err := StartWireGuard(dead, ""); err != nil {
		t.Fatal(err)
	}
	if got := Diagnose("10.9.0.1", 80); got != "handshake|10.9.0.1" {
		t.Fatalf("silent server: %q", got)
	}
	Stop()
	// Its DNS inside the tunnel: the name fails only because the server is silent.
	if err := StartWireGuard(strings.Replace(dead, "Address = 10.9.0.2/32\n", "Address = 10.9.0.2/32\nDNS = 10.9.0.1\n", 1), ""); err != nil {
		t.Fatal(err)
	}
	if got := Diagnose("nowhere.test", 80); got != "handshake|" {
		t.Fatalf("silent server, tunnel DNS: %q", got)
	}
	Stop()

	if err := StartWireGuard(conf, ""); err != nil {
		t.Fatal(err)
	}
	defer Stop()
	for _, c := range []struct {
		host string
		port int
		want string
	}{
		{"10.9.0.1", 80, "ok|10.9.0.1"},
		{"nowhere.test", 80, "name|"},
		{"public.test", 80, "covered|8.8.8.8"},
		{"10.9.0.1", 81, "answer|10.9.0.1|"},
	} {
		if got := Diagnose(c.host, c.port); !strings.HasPrefix(got, c.want) || (c.want == "answer|10.9.0.1|" && got == c.want) {
			t.Errorf("%s:%d: got %q, want %q", c.host, c.port, got, c.want)
		}
	}
	// The owner's case: it worked at home, then the server went silent (another
	// network blocks its UDP). The old handshake must not read as "Jarvis is down".
	lastHouse.Close()
	window := handshakeWindow
	handshakeWindow = 0 // the session has aged past WireGuard's 180 s
	defer func() { handshakeWindow = window }()
	if got := Diagnose("10.9.0.1", 80); got != "handshake|10.9.0.1" {
		t.Errorf("server gone after a handshake: %q", got)
	}
}

// The owner's case (2026-10-29): Jarvis's name is only in the house's DNS, so
// away from home no resolver finds it and the tunnel was never used. Once the
// phone has resolved it at home, it reaches it from anywhere, across a restart.
func TestWireGuardRemembersAHomeName(t *testing.T) {
	conf := wgHouse(t)
	dir := t.TempDir()
	home := true
	lookupHost = func(ctx context.Context, host string) ([]string, error) {
		if home && host == "home.test" {
			return []string{"10.9.0.1"}, nil
		}
		return nil, &net.DNSError{Err: "no such host", Name: host, IsNotFound: true}
	}
	defer func() { lookupHost = net.DefaultResolver.LookupHost }()

	if err := StartWireGuard(conf, dir); err != nil {
		t.Fatal(err)
	}
	defer Stop()
	if !Covers("home.test") {
		t.Fatal("at home: the name the house resolves is not carried")
	}

	// Away, after the app restarted: the name resolves nowhere.
	Stop()
	home = false
	if err := StartWireGuard(conf, dir); err != nil {
		t.Fatal(err)
	}
	if !Covers("home.test") || Covers("elsewhere.test") {
		t.Fatalf("away: home.test %v, elsewhere.test %v", Covers("home.test"), Covers("elsewhere.test"))
	}
	if got := readVia(t, "home.test:80"); got != "home" {
		t.Fatalf("away, through the tunnel: got %q", got)
	}
}

// A full tunnel (0.0.0.0/0) covers every site the web view opens: only a name
// at a private (home) address is remembered, and only so many, so the file
// stays small and a public name never dials a stale address.
func TestWireGuardRemembersOnlyHomeNames(t *testing.T) {
	lookupHost = func(ctx context.Context, host string) ([]string, error) {
		if host == "home.test" {
			return []string{"10.20.30.30"}, nil
		}
		return []string{"1.2.3.4"}, nil
	}
	defer func() { lookupHost = net.DefaultResolver.LookupHost }()
	w := &wgLink{allowed: []netip.Prefix{netip.MustParsePrefix("0.0.0.0/0")}, pins: map[string]netip.Addr{}}
	w.via(context.Background(), "home.test")
	w.via(context.Background(), "cdn.test")
	if _, ok := w.pins["cdn.test"]; ok || w.pins["home.test"].String() != "10.20.30.30" {
		t.Fatalf("pins: %v", w.pins)
	}
	for i := 0; i < 2*maxPins; i++ {
		w.pin(fmt.Sprintf("h%d.test", i), netip.MustParseAddr("10.0.0.1"))
	}
	if len(w.pins) > maxPins {
		t.Fatalf("pins grew to %d", len(w.pins))
	}
}

// Away from home a resolver may answer a name it doesn't know with its own
// address (a hotel's portal, an ISP's search page): the pin still wins over an
// answer the tunnel doesn't carry. It is used uncached, so once DNS answers
// with a carried address again that one is dialled. A full table makes room
// for a new name instead of never learning it, and the file is the owner's alone.
func TestWireGuardPinOutlivesAWrongAnswer(t *testing.T) {
	answer := "203.0.113.5" // the portal
	lookupHost = func(ctx context.Context, host string) ([]string, error) {
		return []string{answer}, nil
	}
	defer func() { lookupHost = net.DefaultResolver.LookupHost }()
	file := filepath.Join(t.TempDir(), "wireguard-hosts")
	w := &wgLink{
		allowed: []netip.Prefix{netip.MustParsePrefix("10.20.30.0/24")},
		pins:    map[string]netip.Addr{"home.test": netip.MustParseAddr("10.20.30.30")},
		pinFile: file,
	}
	if got := w.via(context.Background(), "home.test"); got.String() != "10.20.30.30" {
		t.Fatalf("a portal's answer: got %v, want the pin", got)
	}
	answer = "10.20.30.31" // the house moved it
	if got := w.via(context.Background(), "home.test"); got.String() != "10.20.30.31" {
		t.Fatalf("after DNS recovered: got %v, want the new address", got)
	}
	if w.pins["home.test"].String() != "10.20.30.31" {
		t.Fatalf("the new address is not remembered: %v", w.pins)
	}

	// A pin the tunnel no longer carries is not used.
	w.pins["old.test"] = netip.MustParseAddr("10.1.1.1")
	answer = "203.0.113.5"
	if got := w.via(context.Background(), "old.test"); got.IsValid() {
		t.Fatalf("a pin outside AllowedIPs: got %v", got)
	}

	for i := 0; len(w.pins) < maxPins; i++ {
		w.pins[fmt.Sprintf("h%d.test", i)] = netip.MustParseAddr("10.20.30.40")
	}
	w.pin("new.test", netip.MustParseAddr("10.20.30.50"))
	if w.pins["new.test"].String() != "10.20.30.50" || len(w.pins) > maxPins {
		t.Fatalf("a full table: new.test %v, %d pins", w.pins["new.test"], len(w.pins))
	}
	if fi, err := os.Stat(file); err != nil || fi.Mode().Perm() != 0o600 {
		t.Fatalf("pin file: %v %v", fi, err)
	}
	if got := readPins(file); len(got) != len(w.pins) || got["new.test"] != w.pins["new.test"] {
		t.Fatalf("the file holds %v", got)
	}
}

// A wg-quick config becomes the device's UAPI whatever order its lines come in;
// what wg-quick alone uses (Table, PostUp, SaveConfig) is skipped, and a QR that
// is not a config is refused rather than half-applied.
func TestParseWireGuardConfig(t *testing.T) {
	key := base64.StdEncoding.EncodeToString(make([]byte, 32))
	c, err := parseWG(`[Interface]
privatekey = ` + key + `
Address = 10.8.0.2/24, fd00::2/128
DNS = 10.8.0.1, 1.1.1.1, home.lan
MTU = 1280
Table = off
PostUp = iptables -A FORWARD

[Peer]
Endpoint = 192.0.2.1:51820
AllowedIPs = 10.8.0.0/24, 10.20.30.0/24
PublicKey = ` + key + `
`)
	if err != nil {
		t.Fatal(err)
	}
	zero := strings.Repeat("0", 64)
	want := "private_key=" + zero + "\npublic_key=" + zero + "\nendpoint=192.0.2.1:51820\nallowed_ip=10.8.0.0/24\nallowed_ip=10.20.30.0/24\n"
	if c.uapi != want {
		t.Fatalf("uapi:\n%s\nwant:\n%s", c.uapi, want)
	}
	if len(c.addrs) != 2 || c.addrs[1] != netip.MustParseAddr("fd00::2") || len(c.dns) != 1 || c.dns[0] != netip.MustParseAddr("10.8.0.1") || c.mtu != 1280 || len(c.allowed) != 2 {
		t.Fatalf("parsed %+v", c)
	}
	for _, bad := range []string{"just a token", "[Interface]\nAddress = 10.0.0.2/32\n", "[Interface]\nPrivateKey = " + key + "\n", "[Interface]\nPrivateKey = short\n[Peer]\nPublicKey = " + key + "\n"} {
		if _, err := parseWG(bad); err == nil {
			t.Fatalf("accepted %q", bad)
		}
	}
}
