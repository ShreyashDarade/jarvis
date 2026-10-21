// Package security protects the local server from other web pages and processes.
package security

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"fmt"
	"net/url"
)

// NewToken returns a random URL-safe secret.
func NewToken() (string, error) {
	b := make([]byte, 24)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

// Guard validates Host, Origin and the per-launch token.
type Guard struct {
	token string
	hosts map[string]bool
}

func NewGuard(token string, port int) *Guard {
	return &Guard{token: token, hosts: map[string]bool{
		fmt.Sprintf("127.0.0.1:%d", port): true,
		fmt.Sprintf("localhost:%d", port): true,
	}}
}

func (g *Guard) Token() string { return g.token }

// HostOK blocks DNS-rebinding: the Host header must be our own loopback address.
func (g *Guard) HostOK(host string) bool { return g.hosts[host] }

// OriginOK requires a same-site http origin; a missing Origin is rejected.
func (g *Guard) OriginOK(origin string) bool {
	u, err := url.Parse(origin)
	return err == nil && u.Scheme == "http" && g.hosts[u.Host]
}

func (g *Guard) TokenOK(got string) bool {
	return subtle.ConstantTimeCompare([]byte(got), []byte(g.token)) == 1
}
