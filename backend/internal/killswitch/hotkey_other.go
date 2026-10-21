//go:build !windows

package killswitch

import "context"

// WatchHotkey is a no-op off Windows; use the UI button or Ctrl+C.
func WatchHotkey(ctx context.Context, onPress func()) {}
