// Command jarvis: composition root. Starts the AI service, the HTTP gateway and the kill switch.
package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"runtime"
	"syscall"
	"time"

	"github.com/coder/websocket"

	"jarvis/backend/internal/config"
	"jarvis/backend/internal/gateway"
	"jarvis/backend/internal/httpapi"
	"jarvis/backend/internal/killswitch"
	"jarvis/backend/internal/security"
	"jarvis/backend/internal/supervisor"
)

func main() {
	if os.Getenv("OPENAI_API_KEY") == "" {
		log.Fatal("OPENAI_API_KEY is not set in the environment")
	}
	cfg := config.FromEnv()

	webToken, err := security.NewToken()
	check(err)
	aiSecret, err := security.NewToken()
	check(err)

	ai := &supervisor.PythonService{Exe: cfg.PythonExe, Dir: cfg.AIDir, Port: cfg.AIPort, Secret: aiSecret}
	check(ai.Start())

	sw := killswitch.New(&killswitch.FileAuditor{Path: cfg.AuditLog}, os.Exit, ai, supervisor.SandboxReaper{})

	proxy := gateway.NewProxy(func(ctx context.Context) (*websocket.Conn, error) {
		c, _, err := websocket.Dial(ctx, ai.URL(), nil)
		return c, err
	})
	handler := httpapi.NewHandler(httpapi.Deps{
		Guard:    security.NewGuard(webToken, cfg.Port),
		Frontend: os.DirFS(cfg.FrontendDir),
		Proxy:    proxy,
		Killer:   sw,
	})

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	killswitch.WatchHotkey(ctx, func() { sw.Trigger("hotkey Ctrl+Alt+K", 0) })
	go func() {
		<-ctx.Done()
		sw.Trigger("Ctrl+C", 0)
	}()

	addr := fmt.Sprintf("127.0.0.1:%d", cfg.Port)
	url := "http://" + addr
	fmt.Printf("Jarvis on %s   Kill switch: Ctrl+Alt+K, the red button, or Ctrl+C\n", url)
	if runtime.GOOS == "windows" && os.Getenv("JARVIS_NO_BROWSER") == "" {
		_ = exec.Command("rundll32", "url.dll,FileProtocolHandler", url).Start()
	}
	srv := &http.Server{Addr: addr, Handler: handler, ReadHeaderTimeout: 10 * time.Second}
	log.Fatal(srv.ListenAndServe())
}

func check(err error) {
	if err != nil {
		log.Fatal(err)
	}
}
