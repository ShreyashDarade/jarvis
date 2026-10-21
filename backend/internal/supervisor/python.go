// Package supervisor runs the Python AI service as a child process.
package supervisor

import (
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"time"
)

var probe = &http.Client{Timeout: 300 * time.Millisecond}

type PythonService struct {
	Exe, Dir string
	Port     int
	Secret   string
	cmd      *exec.Cmd
}

// URL is the internal WebSocket address the gateway dials.
func (p *PythonService) URL() string {
	return fmt.Sprintf("ws://127.0.0.1:%d/?secret=%s", p.Port, p.Secret)
}

func (p *PythonService) Start() error {
	cmd := exec.Command(p.Exe, "-m", "jarvis_ai")
	cmd.Dir = p.Dir
	cmd.Env = append(os.Environ(), "JARVIS_AI_PORT="+strconv.Itoa(p.Port), "JARVIS_AI_SECRET="+p.Secret)
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	if err := cmd.Start(); err != nil {
		return err
	}
	p.cmd = cmd

	exited := make(chan error, 1)
	go func() { exited <- cmd.Wait() }()
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case err := <-exited:
			return fmt.Errorf("AI service exited during startup: %v", err)
		default:
		}
		// A plain HTTP request gets a clean 403 from the AI service once it is listening.
		if resp, err := probe.Get(fmt.Sprintf("http://127.0.0.1:%d/", p.Port)); err == nil {
			resp.Body.Close()
			return nil
		}
		time.Sleep(150 * time.Millisecond)
	}
	return fmt.Errorf("AI service did not start on port %d", p.Port)
}

// Stop kills the service and every process it launched (the whole tree).
func (p *PythonService) Stop() error {
	if p.cmd == nil || p.cmd.Process == nil {
		return nil
	}
	if runtime.GOOS == "windows" {
		return exec.Command("taskkill", "/PID", strconv.Itoa(p.cmd.Process.Pid), "/T", "/F").Run()
	}
	return p.cmd.Process.Kill()
}
