# Poker Night

Shared chip tracker for a home Texas Hold'em game played with physical cards. One person opens a table and shares the link; everyone bets from their own phone.

- Blinds post and the dealer button rotates automatically; heads-up rules apply with two players.
- Turn order, minimum raises, all-ins and side pots are enforced by `lib/game.ts`.
- At showdown players tap the winner of each pot (tap several to split).
- Scoreboard shows chips, net versus buy-ins (rebuys included) and hands won.
- Host tools: change blinds, set a stack, rebuy / sit out / remove a player, cancel a hand (refunds every bet), act for someone whose phone died.
- Optional lounge music is generated in the browser with Web Audio (no audio files).

## Run

```bash
npm install
npm run dev     # in-memory PGlite database when DATABASE_URL is unset
npm test        # game engine tests
```

Published on Cursor app hosting with its own Postgres (`cursor-app.json`); migrations in `migrations/` run after `next build`. Each table is one `rooms` row; writes use optimistic locking on `version`, and clients poll every 1.5 s. Players are identified by a random token kept in `localStorage` (stored hashed server-side). Tables idle for 30 days are deleted.
