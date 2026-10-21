package killswitch

import (
	"encoding/json"
	"os"
	"sync"
	"time"
)

// FileAuditor appends JSON lines to the shared audit log.
type FileAuditor struct {
	Path string
	mu   sync.Mutex
}

func (a *FileAuditor) Record(kind string, data map[string]any) {
	a.mu.Lock()
	defer a.mu.Unlock()
	f, err := os.OpenFile(a.Path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	line := map[string]any{"t": time.Now().Format("2006-01-02 15:04:05"), "src": "gateway", "kind": kind}
	for k, v := range data {
		line[k] = v
	}
	_ = json.NewEncoder(f).Encode(line)
}
