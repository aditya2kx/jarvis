"use client";

const CHORDS = [
  [48, 55, 59, 64], // Cmaj7
  [45, 52, 55, 60], // Am7
  [41, 48, 52, 57], // Fmaj7
  [43, 50, 53, 59], // G7
];
const MELODY = [72, 74, 76, 79, 81, 84];
const BAR_SECONDS = 8;

const hz = (midi: number) => 440 * 2 ** ((midi - 69) / 12);

function impulse(ctx: AudioContext, seconds: number) {
  const length = ctx.sampleRate * seconds;
  const buffer = ctx.createBuffer(2, length, ctx.sampleRate);
  for (let c = 0; c < 2; c++) {
    const data = buffer.getChannelData(c);
    for (let i = 0; i < length; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / length) ** 2.5;
  }
  return buffer;
}

/** Soft generative lounge loop (warm pad chords, low bass, sparse music-box melody) plus table sound effects. */
export class TableAudio {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private bus: AudioNode | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private nextBar = 0;
  private bar = 0;
  private volume = 0.5;
  private sfxVolume = 0.9;

  get playing() {
    return this.timer !== null;
  }

  private init() {
    if (!this.ctx) {
      const ctx = new AudioContext();
      const master = ctx.createGain();
      master.gain.value = 0;
      master.connect(ctx.destination);
      const filter = ctx.createBiquadFilter();
      filter.type = "lowpass";
      filter.frequency.value = 1800;
      const reverb = ctx.createConvolver();
      reverb.buffer = impulse(ctx, 3.5);
      const wet = ctx.createGain();
      wet.gain.value = 0.55;
      filter.connect(master);
      filter.connect(reverb).connect(wet).connect(master);
      this.ctx = ctx;
      this.master = master;
      this.bus = filter;
    }
    void this.ctx.resume();
    return this.ctx;
  }

  start() {
    if (this.timer) return;
    const ctx = this.init();
    this.master!.gain.cancelScheduledValues(ctx.currentTime);
    this.master!.gain.setTargetAtTime(this.volume * 0.35, ctx.currentTime, 1.2);
    this.nextBar = Math.max(this.nextBar, ctx.currentTime + 0.1);
    this.schedule();
    this.timer = setInterval(() => this.schedule(), 1000);
  }

  stop() {
    if (!this.ctx || !this.timer) return;
    clearInterval(this.timer);
    this.timer = null;
    this.master!.gain.setTargetAtTime(0, this.ctx.currentTime, 0.4);
  }

  setVolume(v: number) {
    this.volume = v;
    if (this.ctx && this.timer) this.master!.gain.setTargetAtTime(v * 0.35, this.ctx.currentTime, 0.2);
  }

  /** Short sound effects; they play whether or not the music is on. */
  sfx(kind: "chip" | "check" | "fold" | "deal" | "turn" | "win" | "bigwin") {
    const ctx = this.init();
    const t = ctx.currentTime;
    switch (kind) {
      case "chip":
        for (const offset of [0, 0.045, 0.085]) this.tick(t + offset, 2200 + Math.random() * 900, 0.05);
        return;
      case "check":
        this.knock(t, 0.12);
        this.knock(t + 0.13, 0.09);
        return;
      case "fold":
        this.swish(t, 0.18, 0.05, 900);
        return;
      case "deal":
        for (let i = 0; i < 3; i++) this.swish(t + i * 0.09, 0.07, 0.08, 2600);
        return;
      case "turn":
        this.bell(t, hz(84), 0.09);
        this.bell(t + 0.12, hz(88), 0.08);
        return;
      case "win":
      case "bigwin": {
        const notes = kind === "bigwin" ? [72, 76, 79, 84, 88] : [76, 79, 84];
        notes.forEach((n, i) => this.bell(t + i * 0.11, hz(n), 0.07));
        for (let i = 0; i < (kind === "bigwin" ? 8 : 4); i++) this.tick(t + 0.3 + i * 0.05, 2000 + Math.random() * 1200, 0.04);
        return;
      }
      default: {
        const never: never = kind;
        return never;
      }
    }
  }

  private out() {
    const ctx = this.ctx!;
    const g = ctx.createGain();
    g.gain.value = this.sfxVolume;
    g.connect(ctx.destination);
    return g;
  }

  private tick(t: number, freq: number, level: number) {
    const ctx = this.ctx!;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "triangle";
    osc.frequency.setValueAtTime(freq, t);
    osc.frequency.exponentialRampToValueAtTime(freq * 0.4, t + 0.05);
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(level, t + 0.004);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.07);
    osc.connect(gain).connect(this.out());
    osc.start(t);
    osc.stop(t + 0.08);
  }

  private knock(t: number, level: number) {
    const ctx = this.ctx!;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(180, t);
    osc.frequency.exponentialRampToValueAtTime(70, t + 0.08);
    gain.gain.setValueAtTime(level, t);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.12);
    osc.connect(gain).connect(this.out());
    osc.start(t);
    osc.stop(t + 0.14);
  }

  private swish(t: number, dur: number, level: number, freq: number) {
    const ctx = this.ctx!;
    const buffer = ctx.createBuffer(1, Math.ceil(ctx.sampleRate * dur), ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    const filter = ctx.createBiquadFilter();
    filter.type = "bandpass";
    filter.frequency.setValueAtTime(freq, t);
    filter.frequency.exponentialRampToValueAtTime(freq * 2, t + dur);
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(level, t + dur * 0.3);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(filter).connect(gain).connect(this.out());
    src.start(t);
  }

  private bell(t: number, freq: number, level: number) {
    const ctx = this.ctx!;
    for (const [mult, lvl] of [[1, level], [2.01, level * 0.3]] as const) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq * mult;
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(lvl, t + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.9);
      osc.connect(gain).connect(this.out());
      osc.start(t);
      osc.stop(t + 1);
    }
  }

  private schedule() {
    const ctx = this.ctx!;
    while (this.nextBar < ctx.currentTime + 4) {
      const chord = CHORDS[this.bar % CHORDS.length];
      const t = this.nextBar;
      for (const note of chord) this.pad(hz(note), t, BAR_SECONDS + 1.5);
      this.voice(hz(chord[0] - 12), t, BAR_SECONDS, "sine", 0.22, 0.4);
      let beat = 0.5 + Math.random();
      while (beat < BAR_SECONDS - 0.5) {
        if (Math.random() < 0.7) {
          const note = MELODY[Math.floor(Math.random() * MELODY.length)];
          this.voice(hz(note), t + beat, 2.2, "sine", 0.05, 0.01);
        }
        beat += [1, 1.5, 2][Math.floor(Math.random() * 3)];
      }
      this.nextBar += BAR_SECONDS;
      this.bar += 1;
    }
  }

  private pad(freq: number, t: number, dur: number) {
    this.voice(freq, t, dur, "triangle", 0.045, 2.5, -6);
    this.voice(freq, t, dur, "sine", 0.06, 2.5, 6);
  }

  private voice(freq: number, t: number, dur: number, type: OscillatorType, level: number, attack: number, detune = 0) {
    const ctx = this.ctx!;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = type;
    osc.frequency.value = freq;
    osc.detune.value = detune;
    gain.gain.setValueAtTime(0, t);
    gain.gain.linearRampToValueAtTime(level, t + attack);
    gain.gain.setTargetAtTime(0, t + dur - Math.min(2.5, dur / 2), dur / 6);
    osc.connect(gain).connect(this.bus!);
    osc.start(t);
    osc.stop(t + dur + 2);
  }
}
