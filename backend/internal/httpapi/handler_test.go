package httpapi_test

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"testing/fstest"
	"time"

	"github.com/coder/websocket"

	"jarvis/backend/internal/gateway"
	"jarvis/backend/internal/httpapi"
	"jarvis/backend/internal/killswitch"
	"jarvis/backend/internal/security"
)

type fakeKiller struct {
	mu      sync.Mutex
	reasons []string
}

func (f *fakeKiller) Trigger(reason string, _ time.Duration) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.reasons = append(f.reasons, reason)
}

const port = 8765
const origin = "http://127.0.0.1:8765"

func setup(t *testing.T, dial gateway.Dialer) (http.Handler, *fakeKiller, string) {
	t.Helper()
	token := "tok"
	k := &fakeKiller{}
	h := httpapi.NewHandler(httpapi.Deps{
		Guard:    security.NewGuard(token, port),
		Frontend: fstest.MapFS{"index.html": {Data: []byte(`<script>var t="__TOKEN__"</script>`)}},
		Proxy:    gateway.NewProxy(dial),
		Killer:   k,
	})
	return h, k, token
}

func req(method, path, host string, headers map[string]string) *http.Request {
	r := httptest.NewRequest(method, path, nil)
	r.Host = host
	for k, v := range headers {
		r.Header.Set(k, v)
	}
	return r
}

func TestIndexInjectsTokenAndRejectsForeignHost(t *testing.T) {
	h, _, token := setup(t, nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req("GET", "/", "127.0.0.1:8765", nil))
	if w.Code != 200 || !strings.Contains(w.Body.String(), token) {
		t.Fatalf("index: %d %q", w.Code, w.Body.String())
	}
	w = httptest.NewRecorder()
	h.ServeHTTP(w, req("GET", "/", "evil.example", nil))
	if w.Code != http.StatusForbidden {
		t.Fatalf("foreign host got %d", w.Code)
	}
}

func TestKillRequiresTokenAndOrigin(t *testing.T) {
	h, k, token := setup(t, nil)
	cases := []map[string]string{
		{},
		{"Origin": origin},
		{"X-Token": token},
		{"X-Token": token, "Origin": "http://evil.example"},
		{"X-Token": "wrong", "Origin": origin},
	}
	for _, hdr := range cases {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, req("POST", "/api/kill", "127.0.0.1:8765", hdr))
		if w.Code != http.StatusForbidden {
			t.Fatalf("%v got %d", hdr, w.Code)
		}
	}
	if len(k.reasons) != 0 {
		t.Fatal("kill fired without authorization")
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req("POST", "/api/kill", "127.0.0.1:8765", map[string]string{"X-Token": token, "Origin": origin}))
	if w.Code != 200 || len(k.reasons) != 1 {
		t.Fatalf("authorized kill: %d %v", w.Code, k.reasons)
	}
}

func TestWebSocketRejectsBadTokenOrOrigin(t *testing.T) {
	h, _, token := setup(t, nil)
	for _, c := range []struct{ query, origin string }{
		{"", origin}, {"?token=wrong", origin}, {"?token=" + token, ""}, {"?token=" + token, "http://evil.example"},
	} {
		w := httptest.NewRecorder()
		hdr := map[string]string{}
		if c.origin != "" {
			hdr["Origin"] = c.origin
		}
		h.ServeHTTP(w, req("GET", "/ws"+c.query, "127.0.0.1:8765", hdr))
		if w.Code != http.StatusForbidden {
			t.Fatalf("%+v got %d", c, w.Code)
		}
	}
}

// echoAI is a fake AI service: records what it receives and replies with a status event.
func echoAI(t *testing.T, got chan<- string) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer c.CloseNow()
		for {
			_, data, err := c.Read(r.Context())
			if err != nil {
				return
			}
			got <- string(data)
			_ = c.Write(r.Context(), websocket.MessageText, []byte(`{"type":"status","state":"ready"}`))
		}
	}))
}

func TestProxyRelaysAllowedAndRejectsUnknownMessages(t *testing.T) {
	got := make(chan string, 4)
	ai := echoAI(t, got)
	defer ai.Close()
	dial := func(ctx context.Context) (*websocket.Conn, error) {
		c, _, err := websocket.Dial(ctx, "ws"+strings.TrimPrefix(ai.URL, "http"), nil)
		return c, err
	}
	h, _, token := setup(t, dial)
	srv := httptest.NewServer(h)
	defer srv.Close()

	// The guard checks Host/Origin against :8765, so dial with matching headers.
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	c, _, err := websocket.Dial(ctx, "ws"+strings.TrimPrefix(srv.URL, "http")+"/ws?token="+token, &websocket.DialOptions{
		Host:       "127.0.0.1:8765",
		HTTPHeader: http.Header{"Origin": {origin}},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer c.CloseNow()

	if err := c.Write(ctx, websocket.MessageText, []byte(`{"type":"start","mode":1}`)); err != nil {
		t.Fatal(err)
	}
	if m := <-got; !strings.Contains(m, `"start"`) {
		t.Fatalf("AI got %q", m)
	}
	_, reply, err := c.Read(ctx)
	if err != nil || !strings.Contains(string(reply), "ready") {
		t.Fatalf("browser got %q %v", reply, err)
	}

	// Permission messages are allowed through.
	if err := c.Write(ctx, websocket.MessageText, []byte(`{"type":"grant","tools":["open_app"]}`)); err != nil {
		t.Fatal(err)
	}
	if m := <-got; !strings.Contains(m, `"grant"`) {
		t.Fatalf("AI got %q", m)
	}
	_, _, _ = c.Read(ctx) // the fake AI's reply to it

	// A message type the browser may not send is refused and never reaches the AI.
	_ = c.Write(ctx, websocket.MessageText, []byte(`{"type":"run_command","cmd":"dir"}`))
	if _, _, err := c.Read(ctx); websocket.CloseStatus(err) != websocket.StatusPolicyViolation {
		t.Fatalf("expected policy violation, got %v", err)
	}
	select {
	case m := <-got:
		t.Fatalf("forbidden message reached AI: %s", m)
	case <-time.After(200 * time.Millisecond):
	}
}

func TestKillSwitchStopsEverythingOnce(t *testing.T) {
	var stopped, exits int
	sw := killswitch.New(nopAudit{}, func(int) { exits++ }, stopFn(func() error { stopped++; return nil }))
	sw.Trigger("a", 0)
	sw.Trigger("b", 0)
	if stopped != 1 || exits != 1 {
		t.Fatalf("stopped=%d exits=%d", stopped, exits)
	}
}

type nopAudit struct{}

func (nopAudit) Record(string, map[string]any) {}

type stopFn func() error

func (f stopFn) Stop() error { return f() }
