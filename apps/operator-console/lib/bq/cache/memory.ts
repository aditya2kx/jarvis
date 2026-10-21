/** In-process LRU of serialized results (L1). Entries are JSON text, so a hit is a fresh copy. */
export class MemoryLru {
  private map = new Map<string, { json: string; at: number }>();
  private chars = 0;

  constructor(
    private readonly maxChars: number,
    private readonly ttlMs: number,
  ) {}

  get(key: string, now = Date.now()): string | null {
    const e = this.map.get(key);
    if (!e) return null;
    if (now - e.at > this.ttlMs) {
      this.delete(key);
      return null;
    }
    this.map.delete(key);
    this.map.set(key, e);
    return e.json;
  }

  set(key: string, json: string, now = Date.now()): void {
    if (json.length > this.maxChars) return;
    this.delete(key);
    this.map.set(key, { json, at: now });
    this.chars += json.length;
    for (const [k] of this.map) {
      if (this.chars <= this.maxChars) break;
      this.delete(k);
    }
  }

  private delete(key: string): void {
    const e = this.map.get(key);
    if (!e) return;
    this.chars -= e.json.length;
    this.map.delete(key);
  }

  size(): { entries: number; chars: number } {
    return { entries: this.map.size, chars: this.chars };
  }
}
