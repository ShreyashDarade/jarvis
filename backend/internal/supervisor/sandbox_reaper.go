package supervisor

import (
	"os"
	"os/exec"
	"strings"
)

// Label every sandbox container carries (see ai/jarvis_ai/sandbox/docker_sandbox.py).
const sandboxLabel = "jarvis=sandbox"

// SandboxReaper force-kills running sandbox containers. Containers are not children of the AI
// process, so killing that process alone would leave them running.
type SandboxReaper struct {
	Docker string // defaults to "docker"
}

const windowsDocker = "C:/Program Files/Docker/Docker/resources/bin/docker.exe"

// findDocker returns docker from PATH, else Docker Desktop's standard install location.
func findDocker() string {
	if p, err := exec.LookPath("docker"); err == nil {
		return p
	}
	if _, err := os.Stat(windowsDocker); err == nil {
		return windowsDocker
	}
	return "docker"
}

// connections mirrors the sandbox: an explicit context, then Docker Desktop's, then the default.
func connections() [][]string {
	if forced := os.Getenv("JARVIS_DOCKER_CONTEXT"); forced != "" {
		return [][]string{{"--context", forced}}
	}
	return [][]string{{"--context", "desktop-linux"}, {}}
}

// cleanEnv drops a stray DOCKER_HOST, which would override the chosen context.
func cleanEnv() []string {
	var env []string
	for _, kv := range os.Environ() {
		if !strings.HasPrefix(strings.ToUpper(kv), "DOCKER_HOST=") {
			env = append(env, kv)
		}
	}
	return env
}

func (r SandboxReaper) Stop() error {
	docker := r.Docker
	if docker == "" {
		docker = findDocker()
	}
	if _, err := exec.LookPath(docker); err != nil {
		return nil // no Docker, nothing to reap
	}
	var lastErr error
	for _, conn := range connections() {
		run := func(args ...string) *exec.Cmd {
			cmd := exec.Command(docker, append(append([]string{}, conn...), args...)...)
			if len(conn) > 0 {
				cmd.Env = cleanEnv()
			}
			return cmd
		}
		out, err := run("ps", "-q", "--filter", "label="+sandboxLabel).Output()
		if err != nil {
			lastErr = err
			continue // this connection is not usable; try the next
		}
		ids := strings.Fields(string(out))
		if len(ids) == 0 {
			return nil
		}
		return run(append([]string{"kill"}, ids...)...).Run()
	}
	return lastErr
}
