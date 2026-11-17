// Package tailnet is Jarvis's in-app WireGuard link to home (wg.go).
//
// Why it exists (the owner, 2026-09-25, 2026-10-29): the car link binds a socket
// to the car's Wi-Fi, and a VPN app's tunnel does not allow bypass, so an app
// inside it gets EPERM for that bind; an app outside it cannot reach home at
// all. With the tunnel in-process the app stays outside any VPN, binds the
// car's network freely, and reaches Jarvis through it -- a userspace link, not
// a VPN. (The package name is from the embedded Tailscale node that was
// removed 2026-11; the gomobile bindings the app calls keep it.)
//
// The app talks to Jarvis through Forward: a loopback listener whose every
// connection is dialled through the tunnel to one target. Nothing listens
// anywhere but 127.0.0.1.
//
// Not covered: DNS for the rest of the phone, subnet routes, exit nodes. It
// is one app's link to one host.
package tailnet

import (
	"bufio"
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"
)

var (
	mu        sync.Mutex
	listeners = map[string]net.Listener{}
	proxy     net.Listener
)

// State is the tunnel's state: "Running" once it has shaken hands, else "Stopped"
// or what wg.go reports while it connects.
func State() string {
	mu.Lock()
	w := wg
	mu.Unlock()
	if w != nil {
		return w.state()
	}
	return "Stopped"
}

// Forward listens on a loopback port and dials target (host:port) through the
// tunnel for every connection. The same target returns the same port. Returns
// the port.
func Forward(target string) (int, error) {
	if err := up(); err != nil {
		return 0, err
	}
	mu.Lock()
	if l, ok := listeners[target]; ok {
		mu.Unlock()
		return l.Addr().(*net.TCPAddr).Port, nil
	}
	mu.Unlock()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	mu.Lock()
	listeners[target] = l
	mu.Unlock()
	go func() {
		for {
			c, err := l.Accept()
			if err != nil {
				return
			}
			go pipe(c, target)
		}
	}()
	return l.Addr().(*net.TCPAddr).Port, nil
}

// dial sends one connection through the WireGuard tunnel (which lets hosts
// outside its AllowedIPs straight out).
func dial(ctx context.Context, network, addr string) (net.Conn, error) {
	mu.Lock()
	w := wg
	mu.Unlock()
	if w == nil {
		return nil, errors.New("tunnel not started")
	}
	return w.dial(ctx, network, addr)
}

func up() error {
	mu.Lock()
	defer mu.Unlock()
	if wg == nil {
		return errors.New("tunnel not started")
	}
	return nil
}

func pipe(c net.Conn, target string) {
	defer c.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	up, err := dial(ctx, "tcp", target)
	cancel()
	if err != nil {
		return
	}
	defer up.Close()
	done := make(chan struct{}, 2)
	go func() { _, _ = io.Copy(up, c); done <- struct{}{} }()
	go func() { _, _ = io.Copy(c, up); done <- struct{}{} }()
	<-done
}

// Proxy starts (once) an HTTP proxy on loopback that dials through the tunnel
// and answers its port. It is for the car's web view (the console at /car):
// Chromium resolves and connects by itself, so the per-host forwarders the
// app's own sockets use never see it. The app sends everything here, and the
// tunnel's dial lets uncovered hosts straight out.
func Proxy() (int, error) {
	if err := up(); err != nil {
		return 0, err
	}
	mu.Lock()
	defer mu.Unlock()
	if proxy != nil {
		return proxy.Addr().(*net.TCPAddr).Port, nil
	}
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	proxy = l
	go serveProxy(l, dial)
	return l.Addr().(*net.TCPAddr).Port, nil
}

// The proxy listens on loopback, where every app on the phone can reach it, so it
// carries only the origins Jarvis named (the console's host:port). Unlisted, a
// CONNECT to any home host and port went out through Jarvis's tunnel. Does not
// cover the Forward listeners: each has one fixed target behind its own key.
var (
	allowMu sync.Mutex
	allowed = map[string]bool{}
)

// ProxyAllow replaces the host:port list the proxy carries with a comma-separated
// one; "" empties it. Matching is exact after lower-casing, so the port must be
// written out (443, 80) as the proxy sees it.
func ProxyAllow(origins string) {
	allowMu.Lock()
	defer allowMu.Unlock()
	allowed = map[string]bool{}
	for _, o := range strings.Split(origins, ",") {
		if o = strings.ToLower(strings.TrimSpace(o)); o != "" {
			allowed[o] = true
		}
	}
}

func proxyAllowed(hostport string) bool {
	allowMu.Lock()
	defer allowMu.Unlock()
	return allowed[strings.ToLower(hostport)]
}

type dialer func(ctx context.Context, network, addr string) (net.Conn, error)

func serveProxy(l net.Listener, dial dialer) {
	for {
		c, err := l.Accept()
		if err != nil {
			return
		}
		go proxyOne(c, dial)
	}
}

// proxyOne answers one client connection: CONNECT (https, and every WebSocket)
// becomes a tunnel; a plain absolute-form request is sent on with
// "Connection: close", so a kept-alive client never reuses the upstream for a
// different host -- one request per connection, which Chromium copes with.
func proxyOne(c net.Conn, dial dialer) {
	defer c.Close()
	br := bufio.NewReader(c)
	req, err := http.ReadRequest(br)
	if err != nil {
		return
	}
	host := req.Host
	// SplitHostPort, not a ":" test: an IPv6 literal ("[fd00::1]") has colons but no port.
	if _, _, err := net.SplitHostPort(host); req.Method != http.MethodConnect && err != nil {
		host += ":80"
	}
	if !proxyAllowed(host) {
		_, _ = io.WriteString(c, "HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	up, err := dial(ctx, "tcp", host)
	cancel()
	if err != nil {
		_, _ = io.WriteString(c, "HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
		return
	}
	defer up.Close()
	if req.Method == http.MethodConnect {
		if _, err := io.WriteString(c, "HTTP/1.1 200 Connection Established\r\n\r\n"); err != nil {
			return
		}
	} else {
		req.Header.Set("Connection", "close")
		req.Header.Del("Proxy-Connection")
		if err := req.Write(up); err != nil {
			return
		}
	}
	done := make(chan struct{}, 2)
	go func() { _, _ = io.Copy(up, br); done <- struct{}{} }()
	go func() { _, _ = io.Copy(c, up); done <- struct{}{} }()
	<-done
}

// Stop closes the forwarders, the proxy and the WireGuard tunnel.
func Stop() {
	mu.Lock()
	defer mu.Unlock()
	if wg != nil {
		wg.dev.Close()
		wg = nil
	}
	if proxy != nil {
		_ = proxy.Close()
		proxy = nil
	}
	for k, l := range listeners {
		_ = l.Close()
		delete(listeners, k)
	}
	ProxyAllow("")
}
