package tailnet

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

func TestProxyTunnelsAndForwards(t *testing.T) {
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, "car:"+r.URL.Path)
	}))
	defer origin.Close()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	target := origin.Listener.Addr().String()
	ProxyAllow("jarvis.example.home:8199, JARVIS.example.home:443")
	defer ProxyAllow("")
	var dialled []string
	go serveProxy(l, func(ctx context.Context, network, addr string) (net.Conn, error) {
		dialled = append(dialled, addr)
		return (&net.Dialer{}).DialContext(ctx, network, target)
	})
	via, _ := url.Parse("http://" + l.Addr().String())
	client := &http.Client{Transport: &http.Transport{Proxy: http.ProxyURL(via)}}

	res, err := client.Get("http://jarvis.example.home:8199/car")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(res.Body)
	res.Body.Close()
	if string(body) != "car:/car" {
		t.Fatalf("plain request answered %q", body)
	}

	c, err := net.Dial("tcp", l.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	_, _ = io.WriteString(c, "CONNECT jarvis.example.home:443 HTTP/1.1\r\nHost: jarvis.example.home:443\r\n\r\n")
	br := bufio.NewReader(c)
	status, _ := br.ReadString('\n')
	if !strings.Contains(status, "200") {
		t.Fatalf("CONNECT answered %q", status)
	}
	for line, _ := br.ReadString('\n'); line != "\r\n"; line, _ = br.ReadString('\n') {
	}
	_, _ = io.WriteString(c, "GET /tunnel HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
	rest, _ := io.ReadAll(br)
	if !strings.Contains(string(rest), "car:/tunnel") {
		t.Fatalf("tunnel carried %q", rest)
	}
	if len(dialled) != 2 || dialled[0] != "jarvis.example.home:8199" || dialled[1] != "jarvis.example.home:443" {
		t.Fatalf("dialled %v", dialled)
	}
}

// T-0394: the proxy is a loopback port every app on the phone can reach, so it
// carries only what Jarvis allowed (the console's origin). Before, a CONNECT to
// any home host and port went out through Jarvis's tunnel.
func TestProxyRefusesWhatTheAppDidNotAllow(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	ProxyAllow("jarvis.example.home:8199")
	defer ProxyAllow("")
	var dialled []string
	go serveProxy(l, func(ctx context.Context, network, addr string) (net.Conn, error) {
		dialled = append(dialled, addr)
		return nil, fmt.Errorf("must not dial %s", addr)
	})
	for _, req := range []string{
		"CONNECT router.lan:22 HTTP/1.1\r\nHost: router.lan:22\r\n\r\n",
		"CONNECT jarvis.example.home:22 HTTP/1.1\r\nHost: jarvis.example.home:22\r\n\r\n",
		"GET http://jarvis.example.home/x HTTP/1.1\r\nHost: jarvis.example.home\r\n\r\n",
	} {
		c, err := net.Dial("tcp", l.Addr().String())
		if err != nil {
			t.Fatal(err)
		}
		_, _ = io.WriteString(c, req)
		status, _ := bufio.NewReader(c).ReadString('\n')
		c.Close()
		if !strings.Contains(status, "403") {
			t.Fatalf("%q answered %q, not 403", strings.SplitN(req, "\r", 2)[0], status)
		}
	}
	if len(dialled) != 0 {
		t.Fatalf("dialled %v", dialled)
	}
	// An IPv6 console with no port is allowed as [addr]:80; its request must be judged so.
	ProxyAllow("[fd00::1]:80")
	c, err := net.Dial("tcp", l.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	_, _ = io.WriteString(c, "GET http://[fd00::1]/x HTTP/1.1\r\nHost: [fd00::1]\r\n\r\n")
	status, _ := bufio.NewReader(c).ReadString('\n')
	c.Close()
	if strings.Contains(status, "403") || len(dialled) != 1 || dialled[0] != "[fd00::1]:80" {
		t.Fatalf("an allowed IPv6 console answered %q, dialled %v", status, dialled)
	}
	// Each call replaces the list: a console moved to a new address drops the old one.
	ProxyAllow("jarvis.home.example:443")
	if proxyAllowed("jarvis.example.home:8199") || !proxyAllowed("jarvis.home.example:443") {
		t.Fatal("ProxyAllow added to the list instead of replacing it")
	}
	ProxyAllow("")
	if proxyAllowed("jarvis.home.example:443") {
		t.Fatal("an emptied allow-list still allows")
	}
}
