# Poker Night

> **Personal app — not part of Palmetto, BHAGA or the Operator Console.** It is fully self-contained: its own `package.json`, database, styles and deploy target, and it must not import from (or be imported by) anything outside `apps/poker-night/`. `__tests__/isolation.test.ts` enforces this.

Shared Texas Hold'em table for a home game. Everyone opens the same link on their phone. You can deal **real cards** at the table or let the **app deal** (each phone only sees its own cards; the winner is found automatically).

## Features

- Blinds, dealer button, turn order, min-raises, all-ins and side pots
- **Real cards** or **app deals** (switch between hands)
- Optional blind levels that double on a timer (turbo)
- Coach mode: win chance, pot odds and plain-English tips (toggle with 🎓)
- Hand rankings sheet (`?`) and a "Rules" tab for beginners
- Every seat shows its last move this street (Call 50, Raise 200, Fold…) plus a "latest action" line on the felt
- Table chat: speech bubbles over the seat, quick trash-talk lines, a Chat tab with unread badge (140 chars, light anti-spam)
- Scores tab with net vs buy-ins, streaks, awards, VPIP and sparkline history
- **Voice chat** (🎙 in the top bar): seated players talk live, phone to phone (WebRTC). Mute / leave anytime, a mic badge on every seat in voice and a green glow on whoever is speaking. No mic permission → listen-only. Uses a public STUN server and no relay, so a few strict mobile networks may not connect. Use headphones if you're sitting together, or the phones will echo
- Emoji reactions, lounge music, chip / deal / win / chat sound effects
- On a phone (portrait or landscape), tablet or laptop, the felt, every seat and your controls stay in the first screen while you're playing — no scrolling the table away, including the bet/raise confirm; on small screens the community cards shrink or slide just enough that no seat covers them
- Deploys don't interrupt a game: tables live in Postgres, seats in each phone's localStorage, and open tables reload once onto the new version when it's safe (not your turn, nothing typed); music resumes on the next tap
- Lost your seat (cleared browser, different browser or phone)? Tap your name on the join screen — **anyone at the table** who has played a hand taps "Let them in" (anyone seated can tap No). To stop one person with two phones taking a seat while its owner is still playing, a player's yes only works once that seat's old phone has been off the table for a minute; the host's yes works right away. The host back in the browser that created the table gets their seat at once (host key). Chips, cards and host role come back; requests expire after 3 minutes
- At showdown the host taps the winner and pays out
- Host tools: blinds, dealing mode, set stack, rebuy / sit out / remove, cancel hand, act for a player

## Run

```bash
npm install
npm run test
npm run dev     # in-memory PGlite when DATABASE_URL is unset
```

Published with its own Postgres (`cursor-app.json`). One `rooms` row per table; optimistic locking on `version`; clients poll ~1.2s. Player tokens live in `localStorage` and are stored hashed. Idle tables (30 days) are deleted. Voice presence (`voice_members`, live while a phone polls in the last 12 s) and WebRTC offers/answers (`voice_signals`, kept 60 s) sit outside the room row; only seated players can join or signal, and only to another live voice session at the same table. Audio itself never touches the server. Hole cards never leave the server except to their owner (and at showdown / when someone chooses to show).
