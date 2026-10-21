// All DOM work lives here. Nothing in this file knows about sockets or audio.
const $ = (id) => document.getElementById(id);
const checks = () => [...document.querySelectorAll('#grants input[type="checkbox"]')];

const LABELS = {
  idle: "Standing by",
  connecting: "Connecting",
  listening: "Listening",
  speaking: "Speaking",
  working: "Working",
  stopped: "Stopped",
};
const TONES = { speaking: "gold", working: "gold", stopped: "red" };

const TOOL_TITLES = {
  open_app: "Open an app",
  open_url: "Open a web page",
  list_files: "List files in the workspace",
  read_file: "Read a file in the workspace",
  write_file: "Write a file in the workspace",
  run_code: "Run code in the sandbox",
  create_tool: "Create a new tool",
  delegate_task: "Hand a task to a specialist agent",
  find_apps: "Look up installed apps",
  system_info: "Read basic system information",
  inspect_active_window: "Inspect the active Windows app",
  focus_window: "Focus target app window",
  read_screen: "Read screen text & controls via OCR",
  click_in_window: "Click in a Windows app",
  type_in_window: "Type into a Windows app",
  press_key_in_window: "Press a key in a Windows app",
  run_shell_command: "Run host shell command",
  read_system_file: "Read system file",
  write_system_file: "Write system file",
};
const toolTitle = (name) => TOOL_TITLES[name] ?? `Use the “${name}” tool`;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

let countdown = null;
let lastNote = null; // { el, text, count } so identical consecutive lines collapse into one with a count
let agreementSigner = null;

const CAP_HINTS = {
  code: "Running code is off. Start Docker Desktop, then restart Jarvis.",
  windows: "Typing and clicking in apps is off. Start Jarvis with .\\start.ps1 (without -NoWindowsUi) to turn it on.",
};

// Keep the newest line visible by scrolling the log itself. scrollIntoView would also scroll the page.
const stickToBottom = () => { const log = $("log"); log.scrollTop = log.scrollHeight; };

export const ui = {
  mode: () => Number(document.querySelector('input[name="mode"]:checked')?.value ?? 1),
  setMode(mode) {
    const r = document.querySelector(`input[name="mode"][value="${mode}"]`);
    if (r) r.checked = true;
    ui.updateModeVisuals(mode);
  },
  updateModeVisuals(mode) {
    const isAuto = mode === 3;
    const autoBanner = $("autonomous-banner");
    if (autoBanner) autoBanner.hidden = !isAuto;
  },
  isAutonomousSigned: () => Boolean(agreementSigner),
  getSignerName: () => agreementSigner,

  initAgreement({ onSign, onRevoke }) {
    const agreeCheck = $("agree-terms");
    const nameInput = $("signer-name");
    const signBtn = $("sign-agree-btn");
    const badge = $("agreement-signed-badge");
    const inputRow = $("signature-input-row");
    const signedName = $("signed-by-name");
    const revokeBtn = $("revoke-agreement-btn");
    const revokeAuto = $("revoke-auto");
    const mode3Radio = $("mode-3-radio");
    const disclaimerCard = $("disclaimer-card");

    if (!agreeCheck || !nameInput || !signBtn) return;

    const checkForm = () => {
      const ok = agreeCheck.checked && nameInput.value.trim().length > 0;
      signBtn.disabled = !ok;
    };
    agreeCheck.onchange = checkForm;
    nameInput.oninput = checkForm;

    const doSign = () => {
      const name = nameInput.value.trim() || "User";
      agreementSigner = name;
      signedName.textContent = name;
      badge.hidden = false;
      inputRow.hidden = true;
      agreeCheck.closest("label").hidden = true;
      ui.setMode(3);
      ui.selectAllGrants();
      $("autonomous-banner").hidden = false;
      $("autonomous-banner-text").textContent = `⚡ AUTONOMOUS MODE: All actions auto-approved (Signed: ${name})`;
      onSign?.(name);
    };

    const doRevoke = () => {
      agreementSigner = null;
      badge.hidden = true;
      inputRow.hidden = false;
      agreeCheck.closest("label").hidden = false;
      agreeCheck.checked = false;
      nameInput.value = "";
      signBtn.disabled = true;
      ui.setMode(2);
      $("autonomous-banner").hidden = true;
      onRevoke?.();
    };

    signBtn.onclick = doSign;
    revokeBtn.onclick = doRevoke;
    if (revokeAuto) revokeAuto.onclick = doRevoke;

    // Direct click on Mode 3 radio prompt to sign if not yet signed
    if (mode3Radio) {
      mode3Radio.onchange = () => {
        if (mode3Radio.checked && !agreementSigner) {
          disclaimerCard?.scrollIntoView({ behavior: "smooth", block: "center" });
          nameInput?.focus();
        }
      };
    }
  },

  onStart: (fn) => ($("start").onclick = fn),
  onEnd: (fn) => ($("end").onclick = fn),
  onKill: (fn) => ($("kill").onclick = fn),
  onRevoke: (fn) => ($("revoke").onclick = fn),
  onSelectAll: (fn) => ($("select-all").onclick = fn),
  /** Ticks every option in the pre-approval list. */
  selectAllGrants() { checks().forEach((c) => (c.checked = true)); },

  /** Tool names the user has ticked under "Allow once for this session". */
  grantedTools: () => checks().filter((c) => c.checked).flatMap((c) => c.dataset.tools.split(",")),
  onGrantChange: (fn) => checks().forEach((c) => (c.onchange = () => fn(ui.grantedTools()))),
  setGrantedTools(tools) {
    const set = new Set(tools);
    checks().forEach((c) => (c.checked = c.dataset.tools.split(",").every((t) => set.has(t))));
  },
  /** Tick the box that covers `tool`. */
  grantTool(tool) {
    checks().filter((c) => c.dataset.tools.split(",").includes(tool)).forEach((c) => (c.checked = true));
  },
  canGrant: (tool) => checks().some((c) => c.dataset.tools.split(",").includes(tool)),

  setState(state, detail = "") {
    $("state-label").textContent = LABELS[state] ?? state;
    $("state-label").dataset.tone = TONES[state] ?? "";
    $("state-detail").textContent = detail;
  },

  setSession(active) {
    $("start").hidden = active;
    $("end").hidden = !active;
    document.querySelectorAll('input[name="mode"]').forEach((r) => (r.disabled = active));
    if (!active) $("start").disabled = false;
  },
  setBusy: (busy) => ($("start").disabled = busy),

  /** Shows what Jarvis can do right now. */
  setCapability(name, on) {
    const item = document.querySelector(`#caps [data-cap="${name}"]`);
    if (item) item.dataset.on = on ? "yes" : "no";
    const hint = $({ code: "sandbox-status", windows: "windows-ui-status" }[name] ?? "");
    if (hint) {
      hint.hidden = on;
      hint.textContent = on ? "" : CAP_HINTS[name];
    }
  },
  setSandbox: (available) => ui.setCapability("code", available),
  setWindowsUi: (available) => ui.setCapability("windows", available),

  onClear: (fn) => ($("clear-log").onclick = fn),
  clearLog() {
    $("log").textContent = "";
    $("empty").hidden = false;
    lastNote = null;
  },

  lockAfterKill() {
    $("start").disabled = true;
    $("end").hidden = true;
    $("kill").disabled = true;
    const autoBanner = $("autonomous-banner");
    if (autoBanner) autoBanner.hidden = true;
    const signBtn = $("sign-agree-btn");
    if (signBtn) signBtn.disabled = true;
    ui.showPermissionBanner(0);
    ui.setState("stopped", "Everything was stopped. Start Jarvis again to continue.");
  },

  /** Shows the amber "pre-approved" chip with a live countdown; pass 0/null to hide it. */
  showPermissionBanner(secondsLeft) {
    clearInterval(countdown);
    const banner = $("perm-banner");
    banner.hidden = !secondsLeft;
    if (!secondsLeft) return;
    const end = Date.now() + secondsLeft * 1000;
    const tick = () => {
      const s = Math.max(0, Math.round((end - Date.now()) / 1000));
      $("perm-timer").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    };
    tick();

    countdown = setInterval(tick, 1000);
  },

  /** Call after text is appended to an existing message. */
  scrollLog: stickToBottom,

  addMessage(role) {
    $("empty").hidden = true;
    lastNote = null;
    const wrap = el("div", `msg ${role}`);
    wrap.append(el("b", "", role === "user" ? "You" : "Jarvis"));
    const text = el("span", "text");
    wrap.append(text);
    $("log").append(wrap);
    stickToBottom();
    return text;
  },

  note(text, kind = "") {
    $("empty").hidden = true;
    if (lastNote && lastNote.text === text && lastNote.kind === kind && $("log").lastElementChild === lastNote.el) {
      lastNote.count += 1;
      lastNote.el.textContent = `${text} ×${lastNote.count}`;
      return;
    }
    const p = el("p", `note ${kind}`.trim(), text);
    $("log").append(p);
    lastNote = { el: p, text, kind, count: 1 };
    stickToBottom();
  },

  showApproval(m, timeoutSeconds, { onAnswer, onAllowSession }) {
    const box = el("div", "approval");
    box.dataset.id = m.id;
    box.append(el("div", "tag", "Authorization required"));
    box.append(el("h2", "", toolTitle(m.tool)));

    const dl = el("dl");
    for (const [key, value] of Object.entries(m.args ?? {})) {
      dl.append(el("dt", "", key.replaceAll("_", " ")));
      const dd = el("dd");
      const text = typeof value === "string" ? value
        : Array.isArray(value) ? (value.join(" + ") || "(none)") : JSON.stringify(value, null, 2);
      dd.append(text.includes("\n") || text.length > 60 ? el("pre", "", text) : el("code", "", text));
      dl.append(dd);
    }
    box.append(dl);

    const buttons = el("div", "buttons");
    const answer = (fn) => () => { box.remove(); fn(); };
    const deny = el("button", "btn", "Deny");
    const approve = el("button", "btn primary", "Approve");
    deny.onclick = answer(() => onAnswer(false));
    approve.onclick = answer(() => onAnswer(true));
    buttons.append(deny, approve);
    if (ui.canGrant(m.tool)) {
      const always = el("button", "btn gold", "Approve and allow for this session");
      always.onclick = answer(() => { onAnswer(true); onAllowSession(m.tool); });
      buttons.append(always);
    }
    box.append(buttons);
    box.append(el("p", "timeout", `No answer within ${Math.round(timeoutSeconds / 60)} minutes counts as Deny.`));
    $("approvals").append(box);
  },

  removeApproval(id) {
    document.querySelector(`.approval[data-id="${CSS.escape(id)}"]`)?.remove();
  },
  clearApprovals: () => ($("approvals").textContent = ""),
};
