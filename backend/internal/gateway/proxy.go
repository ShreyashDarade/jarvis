// Package gateway relays one browser WebSocket to the AI service, allowing only known message types.
package gateway

import (
	"context"
	"encoding/json"
	"sync/atomic"

	"github.com/coder/websocket"
)

const (
	maxBrowserMsg = 1 << 20 // 1 MiB
	maxAIMsg      = 4 << 20 // 4 MiB (audio)
)

// Browser -> AI messages. Anything else is dropped and the connection closed.
var allowedFromBrowser = map[string]bool{"start": true, "audio": true, "approval": true, "set_mode": true, "grant": true, "stop": true}

// Dialer opens a connection to the AI service.
type Dialer func(ctx context.Context) (*websocket.Conn, error)

type Proxy struct {
	dial   Dialer
	active atomic.Bool
}

func NewProxy(dial Dialer) *Proxy { return &Proxy{dial: dial} }

func sendError(ctx context.Context, c *websocket.Conn, msg string) {
	b, _ := json.Marshal(map[string]string{"type": "error", "message": msg})
	_ = c.Write(ctx, websocket.MessageText, b)
}

// Serve blocks until either side closes. Only one browser session may be active.
func (p *Proxy) Serve(ctx context.Context, browser *websocket.Conn) {
	defer browser.CloseNow()
	if !p.active.CompareAndSwap(false, true) {
		sendError(ctx, browser, "another session is already active")
		return
	}
	defer p.active.Store(false)

	ai, err := p.dial(ctx)
	if err != nil {
		sendError(ctx, browser, "AI service unavailable")
		return
	}
	defer ai.CloseNow()
	browser.SetReadLimit(maxBrowserMsg)
	ai.SetReadLimit(maxAIMsg)

	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	done := make(chan struct{}, 2)

	go func() { // browser -> AI
		defer func() { done <- struct{}{} }()
		for {
			typ, data, err := browser.Read(ctx)
			if err != nil {
				return
			}
			var head struct {
				Type string `json:"type"`
			}
			if typ != websocket.MessageText || json.Unmarshal(data, &head) != nil || !allowedFromBrowser[head.Type] {
				browser.Close(websocket.StatusPolicyViolation, "unsupported message")
				return
			}
			if err := ai.Write(ctx, websocket.MessageText, data); err != nil {
				return
			}
		}
	}()
	go func() { // AI -> browser
		defer func() { done <- struct{}{} }()
		for {
			typ, data, err := ai.Read(ctx)
			if err != nil {
				return
			}
			if err := browser.Write(ctx, typ, data); err != nil {
				return
			}
		}
	}()
	<-done
}
