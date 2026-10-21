package supervisor

import (
	"os/exec"
	"strings"
	"testing"
	"time"
)

// Integration test: needs a working Docker engine and the python:3.12-slim image; skipped otherwise.
func TestReaperKillsRunningSandboxContainers(t *testing.T) {
	dockerExe := findDocker()
	if _, err := exec.LookPath(dockerExe); err != nil {
		t.Skip("docker not installed")
	}
	var conn []string
	for _, c := range connections() {
		cmd := exec.Command(dockerExe, append(append([]string{}, c...), "info")...)
		if len(c) > 0 {
			cmd.Env = cleanEnv()
		}
		if cmd.Run() == nil {
			conn = c
			break
		}
	}
	if conn == nil {
		t.Skip("no usable docker engine")
	}
	docker := func(args ...string) ([]byte, error) {
		cmd := exec.Command(dockerExe, append(append([]string{}, conn...), args...)...)
		if len(conn) > 0 {
			cmd.Env = cleanEnv()
		}
		return cmd.Output()
	}
	if _, err := docker("image", "inspect", "python:3.12-slim"); err != nil {
		t.Skip("python:3.12-slim not pulled")
	}

	out, err := docker("run", "-d", "--rm", "--label", sandboxLabel, "--network", "none", "python:3.12-slim", "sleep", "300")
	if err != nil {
		t.Fatalf("could not start container: %v", err)
	}
	id := strings.TrimSpace(string(out))
	defer docker("kill", id)

	if running, _ := docker("ps", "-q", "--filter", "id="+id); strings.TrimSpace(string(running)) == "" {
		t.Fatal("test container is not running")
	}
	if err := (SandboxReaper{}).Stop(); err != nil {
		t.Fatalf("Stop: %v", err)
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if running, _ := docker("ps", "-q", "--filter", "id="+id); strings.TrimSpace(string(running)) == "" {
			return
		}
		time.Sleep(200 * time.Millisecond)
	}
	t.Fatal("container still running after the reaper ran")
}
