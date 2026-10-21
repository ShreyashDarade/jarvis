// Package httpapi is the HTTP surface: UI files, the browser WebSocket and the kill endpoint.
package httpapi

import (
	"bytes"
	"io/fs"
	"net/http"
	"time"

	"github.com/coder/websocket"

	"jarvis/backend/internal/gateway"
	"jarvis/backend/internal/security"
)

// Killer is the part of the kill switch the API needs.
type Killer interface {
	Trigger(reason string, delay time.Duration)
}

type Deps struct {
	Guard    *security.Guard
	Frontend fs.FS
	Proxy    *gateway.Proxy
	Killer   Killer
}

func NewHandler(d Deps) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /{$}", d.index)
	mux.Handle("GET /static/", http.StripPrefix("/static/", http.FileServer(http.FS(d.Frontend))))
	mux.HandleFunc("GET /ws", d.ws)
	mux.HandleFunc("POST /api/kill", d.kill)
	return d.hostCheck(mux)
}

func (d Deps) hostCheck(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !d.Guard.HostOK(r.Host) {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (d Deps) index(w http.ResponseWriter, r *http.Request) {
	page, err := fs.ReadFile(d.Frontend, "index.html")
	if err != nil {
		http.Error(w, "frontend missing", http.StatusInternalServerError)
		return
	}
	page = bytes.ReplaceAll(page, []byte("__TOKEN__"), []byte(d.Guard.Token()))
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	_, _ = w.Write(page)
}

func (d Deps) authorized(r *http.Request, token string) bool {
	return d.Guard.TokenOK(token) && d.Guard.OriginOK(r.Header.Get("Origin"))
}

func (d Deps) ws(w http.ResponseWriter, r *http.Request) {
	if !d.authorized(r, r.URL.Query().Get("token")) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	c, err := websocket.Accept(w, r, nil)
	if err != nil {
		return
	}
	d.Proxy.Serve(r.Context(), c)
}

func (d Deps) kill(w http.ResponseWriter, r *http.Request) {
	if !d.authorized(r, r.Header.Get("X-Token")) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	d.Killer.Trigger("UI kill button", 300*time.Millisecond)
	_, _ = w.Write([]byte(`{"ok":true}`))
}
