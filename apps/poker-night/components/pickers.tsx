"use client";

import { AVATARS, type DealMode } from "@/lib/game";

export function DealModePicker({ value, onChange, disabled }: { value: DealMode; onChange: (m: DealMode) => void; disabled?: boolean }) {
  return (
    <div className="segmented" role="radiogroup" aria-label="Who deals the cards">
      {(
        [
          ["physical", "🃏 Real cards", "Deal at the table; the app keeps chips & turns"],
          ["app", "📱 App deals", "Cards on each phone; winner found automatically"],
        ] as const
      ).map(([mode, label, sub]) => (
        <button key={mode} type="button" role="radio" aria-checked={value === mode} className={`seg ${value === mode ? "on" : ""}`} disabled={disabled} onClick={() => onChange(mode)}>
          <b>{label}</b>
          <small>{sub}</small>
        </button>
      ))}
    </div>
  );
}

export function AvatarPicker({ value, onChange, taken = new Set() }: { value: string; onChange: (a: string) => void; taken?: Set<string> }) {
  return (
    <div className="avatars" role="radiogroup" aria-label="Pick an avatar">
      {AVATARS.map((a) => (
        <button key={a} type="button" role="radio" aria-checked={value === a} className={`av-btn ${value === a ? "on" : ""}`} disabled={taken.has(a)} onClick={() => onChange(a)}>
          {a}
        </button>
      ))}
    </div>
  );
}
