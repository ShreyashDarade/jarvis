# Jarvis

A voice assistant for your own Windows PC, built on `gpt-live-1`. You talk to it; it opens apps,
reads and writes files, runs code in a sandbox, and — if you turn it on — reads your screen and
clicks, types and presses keys in other windows. Every action is approved individually by default.

```
browser (frontend/) --ws--> Go gateway (backend/) --ws, loopback + secret--> Python AI (ai/) --ws--> gpt-live-1
```

| Layer | Language | Responsibility |
|---|---|---|
| `frontend/static` | vanilla ES modules (no build step) | mic/speaker, approval cards, KILL button |
| `backend/` | Go | the only network-facing piece: Host/Origin/token checks, message allowlist, relays to the AI, starts/stops the AI process, kill switch |
| `ai/` | Python | the model session, tools, approval policy, audit log |

## ⚠️ What this can actually do to your machine

This is not a sandboxed toy. Read this before you run it or hand anyone else the link.

- **It can run any shell command** (`run_shell_command`, PowerShell/cmd) and **read or write any
  file on disk** (`read_system_file` / `write_system_file`), not just inside a workspace folder.
- **With Windows UI control on** (the default), it can click, type, and press keys in whatever
  window it focuses, and read your screen with OCR to find buttons by their visible label.
- **"Ask before every action" (mode 1) is the only mode that confirms every one of the above
  before it runs.** Mode 2 ("ask before changes") still confirms all of this; only plain reads are
  automatic. **Mode 3 ("Autonomous Mode") skips confirmation for everything, including shell
  commands and file writes**, once you tick its on-screen disclaimer. There is no technical limit
  on what mode 3 can do beyond what the tools below support — the disclaimer is the only gate.
- Sandboxed code execution (`run_code`, the `coder` agent) is the one capability that always asks,
  in every mode, and can never be pre-approved.
- Run it with an account that has only the privileges you're comfortable handing to it, keep mode 3
  for situations where you've genuinely decided to trust it, and treat the audit log
  (`jarvis_audit.log`) as the record of what it actually did.

## What it can do

- **Apps and the web:** open any installed app by name (fuzzy, speech-mishearing-tolerant
  matching against the real Start Menu list — not a fixed list of apps), bring an already-open
  window to the front (`focus_window`), open http(s) URLs.
- **Files:** read/write inside a `workspace/` folder (safe, path-confined), or anywhere on disk via
  `read_system_file`/`write_system_file` (see the warning above).
- **Code:** delegates to a `coder` agent (`gpt-6.1-sol` by default) that writes and runs Python in an
  isolated Docker container — no network, read-only root filesystem, non-root user, memory/CPU/pid
  limits, hard timeout. Disabled entirely if Docker isn't running; never falls back to the host.
- **Shell and system files:** `run_shell_command`, `read_system_file`, `write_system_file` — see the
  warning above.
- **Windows UI control** (on by default, `.\start.ps1 -NoWindowsUi` to turn off): `inspect_active_window`,
  `focus_window`, `read_screen` (native control text + OCR), `click_in_window`, `type_in_window`,
  `press_key_in_window`. Clicking/typing by a visible label (`find_text`) looks up the position fresh
  at the moment of the action, not from an earlier look, so it stays correct in windows whose content
  can move (a chat panel, for example). Refuses input to command shells, terminals, and system
  dialogs, control characters in typed text, and system-wide shortcuts (Alt+Tab, Ctrl+Alt+Del, the
  Windows key).
- **Agents:** `delegate_task` hands a task to a specialist agent (`coder`, `analyst`, or an ad-hoc one
  the model defines on the spot with read-only tools).

## Permission model

| Mode | What asks for approval |
|---|---|
| 1 — Ask before every action | Everything, including reads |
| 2 — Ask before changes | Reads run automatically; anything that changes something still asks |
| 3 — Autonomous Mode | **Nothing.** Enabled only after accepting the on-screen disclaimer |

- **Session grants:** tick a capability (open apps, open web pages, read files/system info, write
  workspace files) to pre-approve it for the session, or click "Approve and allow for this session"
  on any card. Grants expire after an hour, end with the session or the kill switch, and can be
  revoked at any time. Code execution and the Windows UI tools are never grantable — they always ask
  in modes 1 and 2, regardless of grants.
- **Approvals only happen in the browser UI**, never by voice — nothing you say out loud can approve
  an action.
- **Kill switch:** the red button, **Ctrl+Alt+K** anywhere, or Ctrl+C. Stops the AI process tree and
  any sandbox containers. Apps it already opened (e.g. Notepad) belong to Windows and stay open.
- **Audit log:** every tool call, approval decision, and grant/mode change is appended to
  `jarvis_audit.log`. Typed text is logged by length only, never its content.

## Run (Windows)

```
.\start.ps1                # builds the gateway and starts everything; opens http://127.0.0.1:8765
.\start.ps1 -NoWindowsUi    # same, with click/type/key-press in other windows turned off
.\stop.ps1                  # stops the gateway, the AI service, and any sandbox containers
```

Requires `OPENAI_API_KEY` in your environment. The gateway starts the Python service itself and
locates the project folder from its own path, so it can be launched from anywhere.

Python dependencies: `pip install -e ai[windows-ui]` (the `windows-ui` extra is only needed for
`read_screen`'s OCR path; everything else works without it).

Environment variables: `JARVIS_PORT`, `JARVIS_AI_PORT`, `JARVIS_PYTHON`, `JARVIS_BACKEND_MODEL`
(default `gpt-5.5`), `JARVIS_CODE_MODEL` (default `gpt-6.1-sol`), `JARVIS_VOICE` (default `marin`),
`JARVIS_WORKSPACE`, `JARVIS_SANDBOX_IMAGE`, `JARVIS_DOCKER_CONTEXT`, `JARVIS_NO_BROWSER`,
`JARVIS_WINDOWS_UI_CONTROL` (`1` default, `0` to disable).

### Code sandbox (Docker)

Install Docker Desktop, start it once, and run `docker pull python:3.12-slim`. Jarvis finds
`docker.exe` on PATH or in Docker's standard install folder, prefers Docker Desktop's own
connection, and ignores a stray `DOCKER_HOST` such as `tcp://localhost:2375`. The UI shows "Code
sandbox: ready" or "off" when a session starts. Without Docker, code never runs on your PC.

## Known limitations

- `read_screen`'s OCR path depends on the target app's content actually being visible and legible;
  it won't find a button inside a panel that isn't open, and third-party app startup can be flaky
  independent of anything here (observed with Cursor during testing).
- The app-name matcher is deliberately generic (consonant-skeleton phonetic matching, not a
  per-app list), so it generalizes to any installed app, but it's still a heuristic and can ask you
  to disambiguate between close names.

## Tests

```
cd backend && go test ./...      # the sandbox-reaper test uses a real container and skips without Docker
cd ai && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests
```

## License

MIT — see [LICENSE](LICENSE).
