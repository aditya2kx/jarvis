export type Verdict = { tone: "good" | "bad" | "neutral"; text: string };

/** Share of the final pot you must win for a call to break even. */
export function potOddsNeeded(owe: number, pot: number) {
  return owe > 0 ? owe / (pot + owe) : 0;
}

export function verdict(owe: number, pot: number, equity: number | null): Verdict | null {
  if (equity === null) {
    if (owe === 0) return { tone: "neutral", text: "Nobody has bet — checking is free." };
    const need = potOddsNeeded(owe, pot);
    return { tone: "neutral", text: `To call profitably you need to win at least ${pct(need)} of the time.` };
  }
  if (owe === 0) {
    if (equity >= 0.6) return { tone: "good", text: "You're likely ahead — betting builds the pot." };
    if (equity >= 0.35) return { tone: "neutral", text: "Middling hand — a free check is fine." };
    return { tone: "neutral", text: "Weak right now — check and see the next card for free." };
  }
  const need = potOddsNeeded(owe, pot);
  if (equity >= need + 0.08) return { tone: "good", text: `Call is profitable: you win ~${pct(equity)}, you only need ${pct(need)}.` };
  if (equity <= need - 0.08) return { tone: "bad", text: `Odds say fold: you win ~${pct(equity)} but need ${pct(need)}.` };
  return { tone: "neutral", text: `Close call: ~${pct(equity)} to win vs ${pct(need)} needed.` };
}

export const pct = (x: number) => `${Math.round(x * 100)}%`;

export const MOVE_HELP = {
  fold: "Give up this hand. You lose what you've already bet.",
  check: "Stay in without betting. Only when nobody has bet.",
  call: "Match the current bet to stay in.",
  raise: "Bet more. Others must match it or fold.",
  allin: "Bet every chip you have.",
} as const;
