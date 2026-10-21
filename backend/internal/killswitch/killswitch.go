// Package killswitch stops everything the app started and exits the process.
package killswitch

import (
	"fmt"
	"sync"
	"time"
)

// Stopper is anything that can be forcibly stopped (e.g. the AI service and its children).
type Stopper interface{ Stop() error }

// Auditor records that the switch fired.
type Auditor interface {
	Record(kind string, data map[string]any)
}

type Switch struct {
	stoppers []Stopper
	audit    Auditor
	exit     func(code int)
	once     sync.Once
}

func New(audit Auditor, exit func(int), stoppers ...Stopper) *Switch {
	return &Switch{stoppers: stoppers, audit: audit, exit: exit}
}

// Trigger stops every Stopper, then exits after delay (so an HTTP reply can flush). Idempotent.
func (s *Switch) Trigger(reason string, delay time.Duration) {
	s.once.Do(func() {
		s.audit.Record("kill", map[string]any{"reason": reason})
		for _, st := range s.stoppers {
			_ = st.Stop()
		}
		fmt.Printf("\n[KILL SWITCH] %s - stopped.\n", reason)
		if delay > 0 {
			time.AfterFunc(delay, func() { s.exit(2) })
			return
		}
		s.exit(2)
	})
}
