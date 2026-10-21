// Audio in/out. Knows nothing about sockets or the DOM. Both ends expose level() (0..1) for visuals.
export const RATE = 24000;

export function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

function makeMeter(ctx) {
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 512;
  const data = new Uint8Array(analyser.fftSize);
  const level = () => {
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (const v of data) sum += ((v - 128) / 128) ** 2;
    return Math.min(1, Math.sqrt(sum / data.length) * 4);
  };
  return { analyser, level };
}

export class Microphone {
  constructor(ctx, onChunk) {
    this.ctx = ctx; this.onChunk = onChunk;
    this.meter = makeMeter(ctx);
  }
  level() { return this.meter.level(); }
  async start() {
    await this.ctx.audioWorklet.addModule("/static/mic-worklet.js");
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
    const node = new AudioWorkletNode(this.ctx, "mic-processor");
    node.port.onmessage = (e) => this.onChunk(toBase64(e.data));
    const source = this.ctx.createMediaStreamSource(this.stream);
    source.connect(node);
    source.connect(this.meter.analyser);
  }
  stop() { this.stream?.getTracks().forEach((t) => t.stop()); }
}

export class Speaker {
  /** onActive() fires when playback starts, onIdle() when the queued audio has finished. */
  constructor(ctx, { onActive, onIdle }) {
    this.ctx = ctx; this.next = 0; this.onActive = onActive; this.onIdle = onIdle; this.timer = null;
    this.meter = makeMeter(ctx);
    this.meter.analyser.connect(ctx.destination);
  }
  level() { return this.meter.level(); }
  play(b64) {
    const raw = atob(b64);
    const f32 = new Float32Array(raw.length / 2);
    for (let i = 0; i < f32.length; i++) {
      const v = (raw.charCodeAt(2 * i + 1) << 8) | raw.charCodeAt(2 * i);
      f32[i] = (v > 32767 ? v - 65536 : v) / 32768;
    }
    const buf = this.ctx.createBuffer(1, f32.length, RATE);
    buf.copyToChannel(f32, 0);
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.meter.analyser);
    this.next = Math.max(this.next, this.ctx.currentTime);
    src.start(this.next);
    this.next += buf.duration;

    this.onActive();
    clearTimeout(this.timer);
    this.timer = setTimeout(this.onIdle, Math.max(0, (this.next - this.ctx.currentTime) * 1000) + 150);
  }
}
