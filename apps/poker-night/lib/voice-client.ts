"use client";

import { api } from "./client";

type Member = { id: string; n: string; muted: boolean };
type Signal = { id: string; from: string; body: { type: "offer" | "answer"; sdp: string; n: string; toN: string } };
type Poll = { joined: boolean; members: Member[]; signals: Signal[] };

export type VoiceStatus = {
  joined: boolean;
  muted: boolean;
  /** No mic permission: you can still hear the table. */
  listenOnly: boolean;
  speaking: string[];
  connected: string[];
};

export const VOICE_IDLE: VoiceStatus = { joined: false, muted: false, listenOnly: false, speaking: [], connected: [] };

// Public STUN only (no relay): phones connect to each other directly, so some strict mobile networks won't connect.
const ICE_SERVERS: RTCIceServer[] = [{ urls: "stun:stun.l.google.com:19302" }];
const POLL_MS = 800;
const METER_MS = 120;
const CONNECT_TIMEOUT_MS = 15_000;
const RETRY_MS = 3000;
const GATHER_TIMEOUT_MS = 2500;
const SPEAKING_LEVEL = 0.035;
const SPEAKING_HOLD_MS = 450;

type Peer = { id: string; n: string; pc: RTCPeerConnection; audio: HTMLAudioElement; meter?: Meter; startedAt: number };

class Meter {
  private analyser: AnalyserNode;
  private data: Float32Array<ArrayBuffer>;
  constructor(ctx: AudioContext, stream: MediaStream) {
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 512;
    ctx.createMediaStreamSource(stream).connect(this.analyser);
    this.data = new Float32Array(this.analyser.fftSize);
  }
  level() {
    this.analyser.getFloatTimeDomainData(this.data);
    let sum = 0;
    for (const v of this.data) sum += v * v;
    return Math.sqrt(sum / this.data.length);
  }
}

function nonce() {
  return Array.from(crypto.getRandomValues(new Uint8Array(10)), (b) => "abcdefghijkmnpqrstuvwxyz23456789"[b % 32]).join("");
}

/** Non-trickle ICE: one offer and one answer per pair, each carrying every candidate gathered so far. */
function gathered(pc: RTCPeerConnection) {
  return new Promise<void>((resolve) => {
    if (pc.iceGatheringState === "complete") return resolve();
    const done = () => {
      clearTimeout(timer);
      pc.removeEventListener("icegatheringstatechange", check);
      resolve();
    };
    const check = () => pc.iceGatheringState === "complete" && done();
    const timer = setTimeout(done, GATHER_TIMEOUT_MS);
    pc.addEventListener("icegatheringstatechange", check);
  });
}

/**
 * Full-mesh voice for one table: every pair of joined phones holds one audio connection. The lower
 * player id offers, so each pair negotiates exactly once; offers/answers go through the table's server.
 */
export class VoiceMesh {
  private n = nonce();
  private after = "0";
  private stream: MediaStream | null = null;
  private ctx: AudioContext | null = null;
  private localMeter: Meter | null = null;
  private peers = new Map<string, Peer>();
  private retryAt = new Map<string, number>();
  private loudAt = new Map<string, number>();
  private timers: ReturnType<typeof setInterval>[] = [];
  private polling = false;
  private status: VoiceStatus = VOICE_IDLE;

  constructor(
    private roomId: string,
    private me: string,
    private token: string,
    private onStatus: (s: VoiceStatus) => void,
  ) {}

  /** Call from the tap that joins: Safari only starts audio inside a user gesture. */
  async join() {
    this.ctx = new AudioContext();
    void this.ctx.resume().catch(() => {});
    let listenOnly = false;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch {
      listenOnly = true;
    }
    if (this.stream) this.localMeter = new Meter(this.ctx, this.stream);
    try {
      await this.post({ action: "join", muted: listenOnly });
    } catch (err) {
      this.stream?.getTracks().forEach((t) => t.stop());
      void this.ctx.close().catch(() => {});
      throw err;
    }
    this.set({ joined: true, muted: listenOnly, listenOnly });
    this.timers.push(setInterval(() => void this.tick(), POLL_MS), setInterval(() => this.meter(), METER_MS));
    void this.tick();
    return listenOnly;
  }

  /** Retry playback on a later tap in case the browser held back audio that arrived outside a gesture. */
  unlock() {
    void this.ctx?.resume().catch(() => {});
    for (const p of this.peers.values()) if (p.audio.paused && p.audio.srcObject) void p.audio.play().catch(() => {});
  }

  async setMuted(muted: boolean) {
    if (!this.stream) return;
    for (const t of this.stream.getAudioTracks()) t.enabled = !muted;
    this.set({ muted });
    await this.post({ action: "mute", muted }).catch(() => {});
  }

  async leave() {
    this.timers.forEach(clearInterval);
    this.timers = [];
    for (const id of [...this.peers.keys()]) this.drop(id);
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    void this.ctx?.close().catch(() => {});
    this.ctx = null;
    this.set(VOICE_IDLE);
    await this.post({ action: "leave" }).catch(() => {});
  }

  private post(body: Record<string, unknown>) {
    return api(`/api/rooms/${this.roomId}/voice`, { method: "POST", body: { ...body, n: this.n }, token: this.token });
  }

  private set(patch: Partial<VoiceStatus>) {
    this.status = { ...this.status, ...patch };
    this.onStatus(this.status);
  }

  private async tick() {
    if (this.polling || !this.status.joined) return;
    this.polling = true;
    try {
      const res = await api<Poll>(`/api/rooms/${this.roomId}/voice?n=${this.n}&after=${this.after}`, { token: this.token });
      if (!this.status.joined) return;
      // A phone that slept past the presence window rejoins with the same session.
      if (!res.joined) await this.post({ action: "join", muted: this.status.muted });
      for (const s of res.signals) {
        this.after = s.id;
        await this.handle(s).catch(() => this.drop(s.from));
      }
      this.reconcile(res.members);
    } catch {
      /* transient network error — next tick retries */
    } finally {
      this.polling = false;
    }
  }

  private reconcile(members: Member[]) {
    const want = new Map(members.filter((m) => m.id !== this.me).map((m) => [m.id, m]));
    const now = Date.now();
    for (const [id, peer] of this.peers) {
      const m = want.get(id);
      const stuck = peer.pc.connectionState !== "connected" && now - peer.startedAt > CONNECT_TIMEOUT_MS;
      if (!m || m.n !== peer.n || stuck || peer.pc.connectionState === "failed") {
        this.drop(id);
        this.retryAt.set(id, now + RETRY_MS);
      }
    }
    for (const m of want.values()) {
      if (this.me < m.id && !this.peers.has(m.id) && (this.retryAt.get(m.id) ?? 0) <= now) void this.offer(m).catch(() => this.drop(m.id));
    }
    this.emitConnected();
  }

  private peer(id: string, n: string) {
    const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });
    const audio = document.createElement("audio");
    audio.autoplay = true;
    audio.setAttribute("playsinline", "");
    audio.hidden = true;
    document.body.appendChild(audio);
    const peer: Peer = { id, n, pc, audio, startedAt: Date.now() };
    pc.ontrack = (e) => {
      const stream = e.streams[0] ?? new MediaStream([e.track]);
      audio.srcObject = stream;
      void audio.play().catch(() => {});
      if (this.ctx) peer.meter = new Meter(this.ctx, stream);
    };
    pc.onconnectionstatechange = () => this.emitConnected();
    this.peers.set(id, peer);
    return peer;
  }

  private async attachMic(pc: RTCPeerConnection) {
    const track = this.stream?.getAudioTracks()[0] ?? null;
    const t = pc.getTransceivers()[0] ?? pc.addTransceiver("audio", { direction: "sendrecv" });
    t.direction = "sendrecv";
    if (track) await t.sender.replaceTrack(track);
  }

  private async offer(m: Member) {
    const { pc } = this.peer(m.id, m.n);
    await this.attachMic(pc);
    await pc.setLocalDescription(await pc.createOffer());
    await gathered(pc);
    await this.post({ action: "signal", to: m.id, toN: m.n, type: "offer", sdp: pc.localDescription!.sdp });
  }

  private async handle(s: Signal) {
    const { type, sdp, n, toN } = s.body;
    if (toN !== this.n) return;
    if (type === "offer") {
      this.drop(s.from);
      const { pc } = this.peer(s.from, n);
      await pc.setRemoteDescription({ type: "offer", sdp });
      await this.attachMic(pc);
      await pc.setLocalDescription(await pc.createAnswer());
      await gathered(pc);
      await this.post({ action: "signal", to: s.from, toN: n, type: "answer", sdp: pc.localDescription!.sdp });
      return;
    }
    const peer = this.peers.get(s.from);
    if (peer && peer.n === n && peer.pc.signalingState === "have-local-offer") await peer.pc.setRemoteDescription({ type: "answer", sdp });
  }

  private drop(id: string) {
    const peer = this.peers.get(id);
    if (!peer) return;
    this.peers.delete(id);
    peer.pc.close();
    peer.audio.srcObject = null;
    peer.audio.remove();
  }

  private emitConnected() {
    const connected = [...this.peers.values()].filter((p) => p.pc.connectionState === "connected").map((p) => p.id).sort();
    if (connected.join() !== this.status.connected.join()) this.set({ connected });
  }

  private meter() {
    const now = Date.now();
    const levels: [string, Meter | null | undefined][] = [[this.me, this.status.muted ? null : this.localMeter], ...[...this.peers.values()].map((p) => [p.id, p.meter] as [string, Meter | undefined])];
    for (const [id, m] of levels) if (m && m.level() > SPEAKING_LEVEL) this.loudAt.set(id, now);
    const speaking = levels.map(([id]) => id).filter((id) => now - (this.loudAt.get(id) ?? 0) < SPEAKING_HOLD_MS).sort();
    if (speaking.join() !== this.status.speaking.join()) this.set({ speaking });
  }
}
