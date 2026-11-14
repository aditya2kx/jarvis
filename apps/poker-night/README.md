# Poker Night

Shared Texas Hold'em table for a home game. Everyone opens the same link on their phone. You can deal **real cards** at the table or let the **app deal** (each phone only sees its own cards; the winner is found automatically).

## Features

- Blinds, dealer button, turn order, min-raises, all-ins and side pots
- **Real cards** or **app deals** (switch between hands)
- Optional blind levels that double on a timer (turbo)
- Coach mode: win chance, pot odds and plain-English tips (toggle with 🎓)
- Hand rankings sheet (`?`) and a "How to play" tab for beginners
- Leaderboard with net vs buy-ins, streaks, awards, VPIP and sparkline history
- Emoji reactions, lounge music, chip / deal / win sound effects
- Host tools: blinds, dealing mode, set stack, rebuy / sit out / remove, cancel hand, act for a player

## Run

```bash
npm install
npm run test
npm run dev     # in-memory PGlite when DATABASE_URL is unset
```

Published with its own Postgres (`cursor-app.json`). One `rooms` row per table; optimistic locking on `version`; clients poll ~1.2s. Player tokens live in `localStorage` and are stored hashed. Idle tables (30 days) are deleted. Hole cards never leave the server except to their owner (and at showdown / when someone chooses to show).
