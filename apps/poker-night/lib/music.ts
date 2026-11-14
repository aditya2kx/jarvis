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

/** Soft generative lounge loop: warm pad chords, a low bass and a sparse music-box melody. */
export class LoungeMusic {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private bus: AudioNode | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private nextBar = 0;
  private bar = 0;
  private volume = 0.5;

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

  /** Short soft "clack" for chip actions; plays even when the music is off. */
  chip() {
    const ctx = this.init();
    const t = ctx.currentTime;
    for (const offset of [0, 0.045]) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "triangle";
      osc.frequency.setValueAtTime(2400 + Math.random() * 600, t + offset);
      osc.frequency.exponentialRampToValueAtTime(900, t + offset + 0.05);
      gain.gain.setValueAtTime(0.0001, t + offset);
      gain.gain.exponentialRampToValueAtTime(0.06, t + offset + 0.004);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + offset + 0.07);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t + offset);
      osc.stop(t + offset + 0.08);
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
