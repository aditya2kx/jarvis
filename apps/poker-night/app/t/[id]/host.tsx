"use client";

import { useEffect, useState } from "react";
import { DealModePicker } from "@/components/pickers";
import type { DealMode, Room } from "@/lib/game";
import type { Send } from "./dock";

export const LEVEL_OPTIONS = [0, 10, 15, 20, 30];

export default function HostTools({ room, busy, send }: { room: Room; busy: boolean; send: Send }) {
  const s = room.settings;
  const [sb, setSb] = useState(s.smallBlind);
  const [bb, setBb] = useState(s.bigBlind);
  const [mode, setMode] = useState<DealMode>(s.dealMode);
  const [level, setLevel] = useState(s.blindLevelMinutes);
  const [target, setTarget] = useState(room.players[0]?.id ?? "");
  const [stack, setStack] = useState<number | "">("");
  useEffect(() => {
    setSb(s.smallBlind);
    setBb(s.bigBlind);
    setMode(s.dealMode);
    setLevel(s.blindLevelMinutes);
  }, [s.smallBlind, s.bigBlind, s.dealMode, s.blindLevelMinutes]);
  const tp = room.players.find((p) => p.id === target);
  const dirty = sb !== s.smallBlind || bb !== s.bigBlind || mode !== s.dealMode || level !== s.blindLevelMinutes;

  return (
    <div className="host-body">
      <div className="tool">
        <span className="tool-label">Dealing {room.hand && <em>(change between hands)</em>}</span>
        <DealModePicker value={mode} onChange={setMode} disabled={!!room.hand} />
      </div>
      <div className="tool">
        <span className="tool-label">Blinds & levels</span>
        <div className="inline">
          <input type="number" inputMode="numeric" value={sb} onChange={(e) => setSb(Number(e.target.value))} aria-label="Small blind" />
          <span className="muted">/</span>
          <input type="number" inputMode="numeric" value={bb} onChange={(e) => setBb(Number(e.target.value))} aria-label="Big blind" />
          <select value={level} onChange={(e) => setLevel(Number(e.target.value))} aria-label="Blinds double every">
            {LEVEL_OPTIONS.map((m) => (
              <option key={m} value={m}>
                {m ? `Double every ${m}m` : "Fixed blinds"}
              </option>
            ))}
          </select>
        </div>
        <button
          className="btn primary"
          disabled={busy || !dirty}
          onClick={() => send({ kind: "settings", smallBlind: sb, bigBlind: bb, dealMode: mode, blindLevelMinutes: level })}
        >
          Save table settings
        </button>
      </div>
      <div className="tool">
        <span className="tool-label">Players</span>
        <div className="inline">
          <select value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Player">
            {room.players.map((p) => (
              <option key={p.id} value={p.id}>
                {p.avatar} {p.name}
              </option>
            ))}
          </select>
          <input type="number" inputMode="numeric" placeholder={tp ? String(tp.chips) : "chips"} value={stack} onChange={(e) => setStack(e.target.value === "" ? "" : Number(e.target.value))} aria-label="Set stack" />
          <button className="btn ghost" disabled={busy || stack === ""} onClick={() => send({ kind: "adjust", playerId: target, chips: stack }).then((ok) => ok && setStack(""))}>
            Set stack
          </button>
        </div>
        <div className="inline wrap">
          <button className="btn ghost small-btn" disabled={busy} onClick={() => send({ kind: "rebuy", playerId: target })}>
            Rebuy for {tp?.name}
          </button>
          <button className="btn ghost small-btn" disabled={busy} onClick={() => send({ kind: "sitout", playerId: target, sittingOut: !tp?.sittingOut })}>
            {tp?.sittingOut ? "Sit in" : "Sit out"}
          </button>
          {target !== room.hostId && (
            <button className="btn ghost small-btn danger-text" disabled={busy} onClick={() => confirm(`Remove ${tp?.name} from the table?`) && send({ kind: "remove", playerId: target })}>
              Remove
            </button>
          )}
        </div>
      </div>
      {room.hand && (
        <button className="btn ghost danger-text" disabled={busy} onClick={() => confirm("Cancel this hand and return every bet?") && send({ kind: "cancel" })}>
          Cancel hand &amp; refund bets
        </button>
      )}
    </div>
  );
}
