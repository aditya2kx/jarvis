export const RANKS = "23456789TJQKA";
export const SUITS = "shdc";

export type Card = string;

export const SUIT_SYMBOL: Record<string, string> = { s: "♠", h: "♥", d: "♦", c: "♣" };
const RANK_WORD: Record<number, [string, string]> = {
  2: ["Two", "Twos"],
  3: ["Three", "Threes"],
  4: ["Four", "Fours"],
  5: ["Five", "Fives"],
  6: ["Six", "Sixes"],
  7: ["Seven", "Sevens"],
  8: ["Eight", "Eights"],
  9: ["Nine", "Nines"],
  10: ["Ten", "Tens"],
  11: ["Jack", "Jacks"],
  12: ["Queen", "Queens"],
  13: ["King", "Kings"],
  14: ["Ace", "Aces"],
};

export const rankValue = (c: Card) => RANKS.indexOf(c[0]) + 2;
export const rankLabel = (c: Card) => (c[0] === "T" ? "10" : c[0]);
export const isRed = (c: Card) => c[1] === "h" || c[1] === "d";

export function fullDeck(): Card[] {
  return [...RANKS].flatMap((r) => [...SUITS].map((s) => r + s));
}

function randomInt(max: number) {
  const buf = new Uint32Array(1);
  const limit = Math.floor(0x100000000 / max) * max;
  do globalThis.crypto.getRandomValues(buf);
  while (buf[0] >= limit);
  return buf[0] % max;
}

export function shuffle<T>(items: T[]): T[] {
  const a = [...items];
  for (let i = a.length - 1; i > 0; i--) {
    const j = randomInt(i + 1);
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

export const CATEGORY_NAMES = [
  "High card",
  "Pair",
  "Two pair",
  "Three of a kind",
  "Straight",
  "Flush",
  "Full house",
  "Four of a kind",
  "Straight flush",
] as const;

export type HandValue = { score: number; category: number; name: string };

function straightHigh(ranks: Set<number>): number {
  for (let high = 14; high >= 5; high--) {
    let ok = true;
    for (let r = high; r > high - 5; r--) {
      if (!ranks.has(r === 1 ? 14 : r)) {
        ok = false;
        break;
      }
    }
    if (ok) return high;
  }
  return 0;
}

function encode(category: number, kickers: number[]) {
  let score = category;
  for (let i = 0; i < 5; i++) score = score * 15 + (kickers[i] ?? 0);
  return score;
}

const word = (r: number, plural = false) => RANK_WORD[r][plural ? 1 : 0];

/** Best poker hand from any 1–7 cards; higher score wins. */
export function evaluate(cards: Card[]): HandValue {
  const values = cards.map(rankValue).sort((a, b) => b - a);
  const counts = new Map<number, number>();
  for (const v of values) counts.set(v, (counts.get(v) ?? 0) + 1);
  const bySuit = new Map<string, number[]>();
  for (const c of cards) bySuit.set(c[1], [...(bySuit.get(c[1]) ?? []), rankValue(c)]);
  const flush = [...bySuit.values()].find((r) => r.length >= 5)?.sort((a, b) => b - a);

  if (flush) {
    const sf = straightHigh(new Set(flush));
    if (sf) return { score: encode(8, [sf]), category: 8, name: sf === 14 ? "Royal flush" : `Straight flush, ${word(sf)} high` };
  }
  const groups = [...counts.entries()].sort((a, b) => b[1] - a[1] || b[0] - a[0]);
  const quads = groups.find(([, n]) => n === 4)?.[0];
  if (quads) {
    const kicker = values.find((v) => v !== quads) ?? 0;
    return { score: encode(7, [quads, kicker]), category: 7, name: `Four of a kind, ${word(quads, true)}` };
  }
  const trips = groups.filter(([, n]) => n === 3).map(([r]) => r);
  const pairs = groups.filter(([, n]) => n === 2).map(([r]) => r);
  if (trips.length && (trips.length > 1 || pairs.length)) {
    const full = trips.length > 1 ? Math.max(trips[1], pairs[0] ?? 0) : pairs[0];
    return { score: encode(6, [trips[0], full]), category: 6, name: `Full house, ${word(trips[0], true)} full of ${word(full, true)}` };
  }
  if (flush) return { score: encode(5, flush.slice(0, 5)), category: 5, name: `Flush, ${word(flush[0])} high` };
  const straight = straightHigh(new Set(values));
  if (straight) return { score: encode(4, [straight]), category: 4, name: `Straight, ${word(straight)} high` };
  if (trips.length) {
    const kickers = values.filter((v) => v !== trips[0]).slice(0, 2);
    return { score: encode(3, [trips[0], ...kickers]), category: 3, name: `Three of a kind, ${word(trips[0], true)}` };
  }
  if (pairs.length >= 2) {
    const kicker = values.find((v) => v !== pairs[0] && v !== pairs[1]) ?? 0;
    return { score: encode(2, [pairs[0], pairs[1], kicker]), category: 2, name: `Two pair, ${word(pairs[0], true)} and ${word(pairs[1], true)}` };
  }
  if (pairs.length === 1) {
    const kickers = values.filter((v) => v !== pairs[0]).slice(0, 3);
    return { score: encode(1, [pairs[0], ...kickers]), category: 1, name: `Pair of ${word(pairs[0], true)}` };
  }
  return { score: encode(0, values.slice(0, 5)), category: 0, name: values.length ? `High card, ${word(values[0])}` : "No cards" };
}

/** Monte Carlo chance of winning (ties count as a share) against `opponents` random hands. */
export function winChance(hole: Card[], board: Card[], opponents: number, iterations = 1200): number {
  if (hole.length !== 2 || opponents < 1) return 0;
  const known = new Set([...hole, ...board]);
  const rest = fullDeck().filter((c) => !known.has(c));
  const need = 5 - board.length;
  let won = 0;
  for (let i = 0; i < iterations; i++) {
    const draw = opponents * 2 + need;
    for (let j = 0; j < draw; j++) {
      const k = j + Math.floor(Math.random() * (rest.length - j));
      [rest[j], rest[k]] = [rest[k], rest[j]];
    }
    const fullBoard = [...board, ...rest.slice(opponents * 2, draw)];
    const mine = evaluate([...hole, ...fullBoard]).score;
    let best = 0;
    let ties = 0;
    for (let o = 0; o < opponents; o++) {
      const s = evaluate([rest[o * 2], rest[o * 2 + 1], ...fullBoard]).score;
      if (s > best) best = s;
    }
    if (mine > best) won += 1;
    else if (mine === best) {
      for (let o = 0; o < opponents; o++) if (evaluate([rest[o * 2], rest[o * 2 + 1], ...fullBoard]).score === mine) ties++;
      won += 1 / (ties + 1);
    }
  }
  return won / iterations;
}

/** Plain-English description of two starting cards for beginners. */
export function describeStart(hole: Card[]): string {
  if (hole.length !== 2) return "";
  const [a, b] = hole.map(rankValue).sort((x, y) => y - x);
  const suited = hole[0][1] === hole[1][1];
  if (a === b) return a >= 10 ? "Premium pocket pair — raise it up" : "Pocket pair — can hit a set";
  if (a === 14 && b >= 10) return `Big ace${suited ? ", suited" : ""} — strong start`;
  if (a >= 11 && b >= 10) return `Two high cards${suited ? ", suited" : ""} — solid`;
  if (suited && a - b === 1) return "Suited connectors — fun to play cheaply";
  if (suited) return "Suited — small flush chance";
  if (a - b === 1 && b >= 5) return "Connectors — small straight chance";
  if (a <= 9) return "Weak start — folding is fine";
  return "Marginal — play carefully";
}
