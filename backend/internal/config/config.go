// Package config reads runtime settings from the environment.
package config

import (
	"os"
	"path/filepath"
	"strconv"
)

type Config struct {
	Port        int    // browser-facing port
	AIPort      int    // internal port of the Python AI service
	AIDir       string // directory of the Python package
	PythonExe   string
	FrontendDir string
	AuditLog    string
}

// findRoot locates the project folder (the one containing ai/ and frontend/) so the app can be
// started from any directory: JARVIS_ROOT, then next to the executable, then the working directory.
func findRoot() string {
	if v := os.Getenv("JARVIS_ROOT"); v != "" {
		return v
	}
	var candidates []string
	if exe, err := os.Executable(); err == nil {
		candidates = append(candidates, filepath.Join(filepath.Dir(exe), ".."))
	}
	candidates = append(candidates, "..", ".")
	for _, c := range candidates {
		if st, err := os.Stat(filepath.Join(c, "ai")); err == nil && st.IsDir() {
			return c
		}
	}
	return ".."
}

func FromEnv() Config {
	root := findRoot()
	return Config{
		Port:        envInt("JARVIS_PORT", 8765),
		AIPort:      envInt("JARVIS_AI_PORT", 8766),
		AIDir:       envOr("JARVIS_AI_DIR", filepath.Join(root, "ai")),
		PythonExe:   envOr("JARVIS_PYTHON", "python"),
		FrontendDir: envOr("JARVIS_FRONTEND_DIR", filepath.Join(root, "frontend", "static")),
		AuditLog:    envOr("JARVIS_AUDIT_LOG", filepath.Join(root, "jarvis_audit.log")),
	}
}

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(key)); err == nil {
		return v
	}
	return def
}
