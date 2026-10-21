//go:build windows

package killswitch

import (
	"context"
	"syscall"
	"time"
)

var getAsyncKeyState = syscall.NewLazyDLL("user32.dll").NewProc("GetAsyncKeyState")

func down(vk uintptr) bool {
	r, _, _ := getAsyncKeyState.Call(vk)
	return r&0x8000 != 0
}

// WatchHotkey calls onPress when Ctrl+Alt+K is held, anywhere on the desktop.
func WatchHotkey(ctx context.Context, onPress func()) {
	go func() {
		t := time.NewTicker(50 * time.Millisecond)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				if down(0x11) && down(0x12) && down(0x4B) { // Ctrl, Alt, K
					onPress()
					return
				}
			}
		}
	}()
}
