import { Microphone, RATE, Speaker } from "./audio.js";
import { Hud } from "./hud.js";
import { Gateway, kill } from "./socket.js";
import { ui } from "./ui.js";

const APPROVAL_TIMEOUT_S = 120; // matches the AI service
const token = window.JARVIS_TOKEN;
let gateway, ctx, mic, speaker, state = "idle";
const current = { user: null, assistant: null };

const hud = new Hud(document.getElementById("hud"), () => Math.max(mic?.level() ?? 0, speaker?.level() ?? 0));
hud.start();

function setState(next, detail) {
  state = next;
  ui.setState(next, detail);
  hud.setState(next);
}
const live = () => ["listening", "speaking", "working"].includes(state);

function transcript({ role, delta }) {
  const other = role === "user" ? "assistant" : "user";
  current[other] = null; // the other speaker's next turn starts a new line
  current[role] ??= ui.addMessage(role);
  current[role].append(document.createTextNode(delta));
  ui.scrollLog();
}

function friendlyMicError(e) {
  if (e?.name === "NotAllowedError") return "Microphone access was blocked. Allow it in the address bar, then try again.";
  if (e?.name === "NotFoundError") return "No microphone was found. Plug one in and try again.";
  return `Could not start: ${e?.message ?? e}`;
}

function describe(tool, args) {
  const first = Object.values(args ?? {}).find((v) => typeof v === "string");
  const name = tool.replaceAll("_", " ");
  return first ? `${name} “${first.length > 40 ? first.slice(0, 40) + "…" : first}”` : name;
}

const inFlight = []; // descriptions of tool calls waiting for their result, oldest first
function takeDescription(tool) {
  const i = inFlight.findIndex((c) => c.tool === tool);
  return i >= 0 ? inFlight.splice(i, 1)[0].desc : tool.replaceAll("_", " ");
}

async function start() {
  ui.setBusy(true);
  setState("connecting", "Setting up your microphone…");
  ctx = new AudioContext({ sampleRate: RATE });
  speaker = new Speaker(ctx, {
    onActive: () => state === "listening" && setState("speaking"),
    onIdle: () => state === "speaking" && setState("listening"),
  });
  mic = new Microphone(ctx, (b64) => live() && gateway.audio(b64));
  await mic.start();

  setState("connecting", "Connecting to the assistant…");
  gateway = new Gateway(token, {
    status: (m) => {
      if (m.state === "ready") { ui.setSession(true); setState("listening", "Go ahead, I'm listening."); }
      else if (m.state === "connecting") setState("connecting");
    },
    audio: (m) => speaker.play(m.data),
    transcript,
    sandbox: (m) => ui.setSandbox(m.available),
    windows_ui: (m) => ui.setWindowsUi(m.available),
    permissions: (m) => {
      ui.setGrantedTools(m.granted);
      ui.showPermissionBanner(m.expires_in);
      if (m.reason === "expired") ui.note("Pre-approval expired. Jarvis will ask again.", "error");
    },
    approval_request: (m) => ui.showApproval(m, APPROVAL_TIMEOUT_S, {
      onAnswer: (ok) => gateway.approve(m.id, ok),
      onAllowSession: (tool) => { ui.grantTool(tool); gateway.grant(ui.grantedTools()); },
    }),
    approval_expired: (m) => { ui.removeApproval(m.id); ui.note("An approval request timed out and was denied.", "error"); },
    tool_call: (m) => {
      const desc = describe(m.tool, m.args);
      inFlight.push({ tool: m.tool, desc });
      setState("working", desc);
    },
    tool_result: (m) => {
      const desc = takeDescription(m.tool);
      if (m.ok) ui.note(m.summary || `${desc}: done`, "done");
      else if (/denied/i.test(m.error)) ui.note(`${desc}: you denied this`, "warn");
      else ui.note(`${m.summary || `${desc}: ${(m.error || "didn't work").replace(/[.\s]+$/, "")}`}. Jarvis may try another way.`, "warn");
      setState("listening", "Go ahead, I'm listening.");
    },
    error: (m) => ui.note(m.message, "error"),
    close: () => {
      if (state === "stopped") return;
      ui.clearApprovals();
      ui.showPermissionBanner(0);
      ui.setGrantedTools([]); // grants end with the session
      mic?.stop();
      ctx?.close();
      mic = speaker = null;
      inFlight.length = 0;
      ui.setSession(false);
      setState("idle");
      current.user = current.assistant = null;
    },
  });
  gateway.onOpen(() => {
    gateway.start(ui.mode());
    const granted = ui.grantedTools();
    if (granted.length) gateway.grant(granted);
  });
}

ui.onStart(() => start().catch((e) => {
  ui.note(friendlyMicError(e), "error");
  ui.setBusy(false);
  setState("idle");
}));
ui.onEnd(() => gateway?.stop());
ui.onGrantChange((tools) => gateway?.grant(tools)); // ticking or unticking applies immediately in a session
ui.onClear(() => ui.clearLog());
ui.onSelectAll(() => { ui.selectAllGrants(); gateway?.grant(ui.grantedTools()); });
ui.onRevoke(() => { ui.setGrantedTools([]); gateway?.grant([]); });

ui.initAgreement({
  onSign: (name) => {
    ui.selectAllGrants();
    const granted = ui.grantedTools();
    if (gateway) {
      gateway.setMode(3);
      gateway.grant(granted);
    }
    ui.note(`⚡ Autonomous Agreement signed by ${name}. All actions auto-approved for this session.`, "done");
  },
  onRevoke: () => {
    if (gateway) {
      gateway.setMode(2);
    }
    ui.note("Autonomous Agreement revoked. Reverted to standard approval mode.", "warn");
  },
});

document.querySelectorAll('input[name="mode"]').forEach((r) => {
  r.addEventListener("change", () => {
    const m = Number(r.value);
    ui.updateModeVisuals(m);
    gateway?.setMode(m);
  });
});

ui.onKill(async () => {
  state = "stopped"; // checked by the close handler so the stopped screen is not overwritten
  hud.setState("stopped");
  await kill(token);
  mic?.stop();
  ui.clearApprovals();
  ui.lockAfterKill();
  ui.note("Kill switch pressed. Jarvis and everything it was running have been stopped.");
});

