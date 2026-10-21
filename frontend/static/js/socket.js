// Typed wrapper over the gateway WebSocket.
export class Gateway {
  constructor(token, handlers) {
    this.handlers = handlers; // one handler per server event type, plus close
    this.ws = new WebSocket(`ws://${location.host}/ws?token=${encodeURIComponent(token)}`);
    this.ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      this.handlers[m.type]?.(m);
    };
    this.ws.onclose = () => this.handlers.close?.();
  }
  onOpen(fn) { this.ws.onopen = fn; }
  send(msg) { if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg)); }
  start(mode) { this.send({ type: "start", mode }); }
  setMode(mode) { this.send({ type: "set_mode", mode }); }
  audio(data) { this.send({ type: "audio", data }); }
  approve(id, approved) { this.send({ type: "approval", id, approved }); }
  grant(tools) { this.send({ type: "grant", tools }); }   // an empty list revokes everything
  stop() { this.send({ type: "stop" }); }

}

export async function kill(token) {
  try {
    await fetch("/api/kill", { method: "POST", headers: { "x-token": token } });
  } catch (_) { /* server is already gone */ }
}
