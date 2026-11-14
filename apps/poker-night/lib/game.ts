import { evaluate, fullDeck, shuffle, type Card } from "./cards";

export type Street = "preflop" | "flop" | "turn" | "river" | "showdown";
export type DealMode = "physical" | "app";

export type Stats = {
  handsPlayed: number;
  vpip: number;
  allIns: number;
  biggestPot: number;
  streak: number;
  bestStreak: number;
  bestHand: string | null;
  bestHandScore: number;
};

export type Player = {
  id: string;
  name: string;
  avatar: string;
  chips: number;
  boughtIn: number;
  handsWon: number;
  sittingOut: boolean;
  stats: Stats;
};

export type Hand = {
  number: number;
  dealerId: string;
  sbId: string;
  bbId: string;
  order: string[];
  street: Street;
  bets: Record<string, number>;
  contributed: Record<string, number>;
  folded: string[];
  acted: string[];
  vpip: string[];
  toAct: string | null;
  turnStartedAt: number;
  currentBet: number;
  minRaise: number;
  /** In-app dealing only. The view layer strips everything a viewer may not see. */
  deck?: Card[];
  holes?: Record<string, Card[]>;
  board?: Card[];
};

export type Pot = { amount: number; eligible: string[] };

export type Result = {
  hand: number;
  at: number;
  text: string;
  uncontested: boolean;
  winners: { id: string; amount: number; handName?: string }[];
  shown: { id: string; cards: Card[]; handName: string }[];
  board: Card[];
  pot: number;
};

export type LogEntry = { at: number; text: string };
export type Reaction = { id: string; playerId: string; emoji: string; at: number };

export type Settings = {
  startingChips: number;
  smallBlind: number;
  bigBlind: number;
  dealMode: DealMode;
  blindLevelMinutes: number;
};

export type Room = {
  id: string;
  createdAt: number;
  hostId: string;
  settings: Settings;
  blindLevel: number;
  levelStartedAt: number;
  players: Player[];
  hand: Hand | null;
  handCount: number;
  lastDealerId: string | null;
  lastResult: Result | null;
  /** Server-only: last hand's hole cards so a winner can choose to show them. */
  lastHoles?: Record<string, Card[]>;
  log: LogEntry[];
  reactions: Reaction[];
  history: { hand: number; stacks: Record<string, number> }[];
};

export type Action =
  | { type: "fold" }
  | { type: "check" }
  | { type: "call" }
  | { type: "raise"; to: number }
  | { type: "allin" };

export const MAX_PLAYERS = 10;
export const AVATARS = ["🦊", "🐯", "🐼", "🦁", "🐸", "🐙", "🦄", "🐻", "🐵", "🐨", "🐲", "🤠", "😎", "👑", "🦈", "🐺"];
export const REACTIONS = ["😂", "🔥", "😱", "👏", "🤡", "💀", "😤", "🫡"];
export const BOARD_CARDS: Record<Street, number> = { preflop: 0, flop: 3, turn: 4, river: 5, showdown: 5 };
const MAX_LOG = 80;
const MAX_HISTORY = 150;
const STREETS: Street[] = ["preflop", "flop", "turn", "river", "showdown"];

export class GameError extends Error {}

function fail(message: string): never {
  throw new GameError(message);
}

function player(room: Room, id: string): Player {
  return room.players.find((p) => p.id === id) ?? fail("Unknown player");
}

function log(room: Room, text: string) {
  room.log.push({ at: Date.now(), text });
  if (room.log.length > MAX_LOG) room.log.splice(0, room.log.length - MAX_LOG);
}

export function nameOf(room: Room, id: string) {
  return room.players.find((p) => p.id === id)?.name ?? "?";
}

const freshStats = (): Stats => ({
  handsPlayed: 0,
  vpip: 0,
  allIns: 0,
  biggestPot: 0,
  streak: 0,
  bestStreak: 0,
  bestHand: null,
  bestHandScore: -1,
});

export function createRoom(id: string, host: { id: string; name: string; avatar?: string }, settings: Settings): Room {
  const now = Date.now();
  const room: Room = {
    id,
    createdAt: now,
    hostId: host.id,
    settings,
    blindLevel: 1,
    levelStartedAt: now,
    players: [],
    hand: null,
    handCount: 0,
    lastDealerId: null,
    lastResult: null,
    log: [],
    reactions: [],
    history: [],
  };
  addPlayer(room, host);
  return room;
}

export function addPlayer(room: Room, p: { id: string; name: string; avatar?: string }) {
  if (room.players.length >= MAX_PLAYERS) fail(`Table is full (${MAX_PLAYERS} max)`);
  const name = cleanName(p.name);
  if (room.players.some((x) => x.name.toLowerCase() === name.toLowerCase())) {
    fail("Someone at the table already has that name");
  }
  const taken = new Set(room.players.map((x) => x.avatar));
  const avatar = p.avatar && AVATARS.includes(p.avatar) ? p.avatar : (AVATARS.find((a) => !taken.has(a)) ?? AVATARS[0]);
  room.players.push({
    id: p.id,
    name,
    avatar,
    chips: room.settings.startingChips,
    boughtIn: room.settings.startingChips,
    handsWon: 0,
    sittingOut: false,
    stats: freshStats(),
  });
  log(room, `${avatar} ${name} sat down with ${room.settings.startingChips}`);
}

export function cleanName(raw: string) {
  const name = raw.replace(/\s+/g, " ").trim().slice(0, 20);
  if (!name) fail("Enter a name");
  return name;
}

function isLive(h: Hand, id: string) {
  return !h.folded.includes(id);
}

function canAct(room: Room, h: Hand, id: string) {
  return isLive(h, id) && player(room, id).chips > 0;
}

function nextAfter(order: string[], from: string, pred: (id: string) => boolean): string | null {
  const start = order.indexOf(from);
  for (let i = 1; i <= order.length; i++) {
    const id = order[(start + i) % order.length];
    if (pred(id)) return id;
  }
  return null;
}

function pay(room: Room, h: Hand, id: string, amount: number) {
  const p = player(room, id);
  const paid = Math.min(amount, p.chips);
  p.chips -= paid;
  h.bets[id] = (h.bets[id] ?? 0) + paid;
  h.contributed[id] = (h.contributed[id] ?? 0) + paid;
  return paid;
}

export function nextDealerId(room: Room): string | null {
  const seated = room.players.filter((p) => !p.sittingOut && p.chips > 0).map((p) => p.id);
  if (seated.length === 0) return null;
  const allIds = room.players.map((p) => p.id);
  const prev = room.lastDealerId;
  return prev && allIds.includes(prev) ? nextAfter(allIds, prev, (id) => seated.includes(id)) : seated[0];
}

/** Minutes until blinds go up, or null when levels are off. */
export function blindsUpAt(room: Room) {
  const m = room.settings.blindLevelMinutes;
  return m > 0 ? room.levelStartedAt + m * 60_000 : null;
}

function maybeRaiseBlinds(room: Room) {
  const at = blindsUpAt(room);
  if (at === null || Date.now() < at) return;
  room.settings.smallBlind *= 2;
  room.settings.bigBlind *= 2;
  room.blindLevel += 1;
  room.levelStartedAt = Date.now();
  log(room, `⏫ Blinds up! Level ${room.blindLevel}: ${room.settings.smallBlind}/${room.settings.bigBlind}`);
}

export function startHand(room: Room) {
  if (room.hand) fail("A hand is already in progress");
  const seated = room.players.filter((p) => !p.sittingOut && p.chips > 0).map((p) => p.id);
  if (seated.length < 2) fail("Need at least 2 players with chips");
  maybeRaiseBlinds(room);

  const dealerId = nextDealerId(room)!;
  const order = [...seated];
  const headsUp = order.length === 2;
  const sbId = headsUp ? dealerId : nextAfter(order, dealerId, () => true)!;
  const bbId = nextAfter(order, sbId, () => true)!;

  const h: Hand = {
    number: room.handCount + 1,
    dealerId,
    sbId,
    bbId,
    order,
    street: "preflop",
    bets: {},
    contributed: {},
    folded: [],
    acted: [],
    vpip: [],
    toAct: null,
    turnStartedAt: Date.now(),
    currentBet: room.settings.bigBlind,
    minRaise: room.settings.bigBlind,
  };
  if (room.settings.dealMode === "app") {
    const deck = shuffle(fullDeck());
    const fromDealer = [...order.slice(order.indexOf(dealerId) + 1), ...order.slice(0, order.indexOf(dealerId) + 1)];
    h.holes = {};
    for (const id of fromDealer) h.holes[id] = [deck.pop()!, deck.pop()!];
    h.board = [deck.pop()!, deck.pop()!, deck.pop()!, deck.pop()!, deck.pop()!];
    h.deck = deck;
  }
  room.hand = h;
  room.handCount = h.number;
  room.lastDealerId = dealerId;
  room.lastResult = null;
  room.lastHoles = undefined;
  for (const id of order) player(room, id).stats.handsPlayed += 1;
  pay(room, h, sbId, room.settings.smallBlind);
  pay(room, h, bbId, room.settings.bigBlind);
  h.currentBet = Math.max(room.settings.bigBlind, ...Object.values(h.bets));
  log(room, `🃏 Hand #${h.number} — ${nameOf(room, dealerId)} has the button`);

  h.toAct = nextAfter(order, bbId, (id) => canAct(room, h, id));
  settleIfNoActionNeeded(room);
}

export function act(room: Room, actorId: string, action: Action) {
  const h = room.hand ?? fail("No hand in progress");
  if (h.street === "showdown") fail("Betting is over — pick the winner");
  if (h.toAct !== actorId) fail(`It's ${nameOf(room, h.toAct ?? "")}'s turn`);
  const p = player(room, actorId);
  const mine = h.bets[actorId] ?? 0;
  const owe = h.currentBet - mine;
  const chipsBefore = p.chips;
  let voluntary = false;

  switch (action.type) {
    case "fold":
      h.folded.push(actorId);
      log(room, `${p.name} folds`);
      break;
    case "check":
      if (owe > 0) fail(`You need to call ${owe}`);
      log(room, `${p.name} checks`);
      break;
    case "call": {
      if (owe <= 0) fail("Nothing to call — check instead");
      const paid = pay(room, h, actorId, owe);
      voluntary = true;
      log(room, `${p.name} calls ${paid}${p.chips === 0 ? " — ALL IN" : ""}`);
      break;
    }
    case "raise":
    case "allin": {
      const max = mine + p.chips;
      const to = action.type === "allin" ? max : Math.floor(action.to);
      if (!Number.isFinite(to) || to <= 0) fail("Enter an amount");
      if (to > max) fail(`You only have ${p.chips}`);
      voluntary = true;
      if (to <= h.currentBet) {
        if (to !== max) fail(`Raise must be more than ${h.currentBet}`);
        pay(room, h, actorId, to - mine);
        log(room, `${p.name} calls ${to - mine} — ALL IN`);
        break;
      }
      const raiseBy = to - h.currentBet;
      if (raiseBy < h.minRaise && to !== max) {
        fail(`Minimum ${h.currentBet === 0 ? "bet" : "raise"} is to ${h.currentBet + h.minRaise}`);
      }
      const verb = h.currentBet === 0 ? "bets" : "raises to";
      pay(room, h, actorId, to - mine);
      if (raiseBy >= h.minRaise) {
        h.minRaise = raiseBy;
        h.acted = [];
      }
      h.currentBet = to;
      log(room, `${p.name} ${verb} ${to}${p.chips === 0 ? " — ALL IN" : ""}`);
      break;
    }
    default: {
      const never: never = action;
      fail(`Unknown action ${JSON.stringify(never)}`);
    }
  }
  if (voluntary && h.street === "preflop" && !h.vpip.includes(actorId)) h.vpip.push(actorId);
  if (chipsBefore > 0 && p.chips === 0) p.stats.allIns += 1;
  if (!h.acted.includes(actorId)) h.acted.push(actorId);
  advance(room, actorId);
}

function needsToAct(room: Room, h: Hand, id: string) {
  return canAct(room, h, id) && (!h.acted.includes(id) || (h.bets[id] ?? 0) < h.currentBet);
}

function advance(room: Room, lastId: string) {
  const h = room.hand!;
  const live = h.order.filter((id) => isLive(h, id));
  if (live.length === 1) {
    awardUncontested(room, live[0]);
    return;
  }
  h.toAct = nextAfter(h.order, lastId, (id) => needsToAct(room, h, id));
  settleIfNoActionNeeded(room);
}

function settleIfNoActionNeeded(room: Room) {
  const h = room.hand!;
  h.turnStartedAt = Date.now();
  while (h.toAct === null || !needsToAct(room, h, h.toAct)) {
    h.toAct = nextAfter(h.order, h.toAct ?? h.dealerId, (id) => needsToAct(room, h, id));
    if (h.toAct !== null) return;
    endStreet(room);
    if (room.hand !== h || h.street === "showdown") return;
  }
}

function endStreet(room: Room) {
  const h = room.hand!;
  h.bets = {};
  h.acted = [];
  h.currentBet = 0;
  h.minRaise = room.settings.bigBlind;
  const stillBetting = h.order.filter((id) => canAct(room, h, id));
  const next = STREETS[STREETS.indexOf(h.street) + 1];
  h.street = stillBetting.length < 2 ? "showdown" : next;
  if (h.street === "showdown") {
    h.toAct = null;
    if (room.settings.dealMode === "app" && h.holes && h.board) {
      autoShowdown(room);
      return;
    }
    log(room, "🂠 Showdown — flip your cards");
    return;
  }
  log(room, `— ${h.street} —`);
  h.toAct = nextAfter(h.order, h.dealerId, (id) => canAct(room, h, id));
}

export function pots(h: Hand): Pot[] {
  const levels = [...new Set(Object.values(h.contributed).filter((v) => v > 0))].sort((a, b) => a - b);
  const result: Pot[] = [];
  let prev = 0;
  for (const level of levels) {
    let amount = 0;
    for (const c of Object.values(h.contributed)) amount += Math.max(0, Math.min(c, level) - prev);
    const eligible = h.order.filter((id) => isLive(h, id) && (h.contributed[id] ?? 0) >= level);
    const last = result[result.length - 1];
    if (last && (eligible.length === 0 || sameSet(last.eligible, eligible))) last.amount += amount;
    else if (eligible.length > 0) result.push({ amount, eligible });
    else result.push({ amount, eligible: h.order.filter((id) => isLive(h, id)) });
    prev = level;
  }
  return result;
}

export function potTotal(h: Hand) {
  return Object.values(h.contributed).reduce((a, b) => a + b, 0);
}

function sameSet(a: string[], b: string[]) {
  return a.length === b.length && a.every((x) => b.includes(x));
}

function finishHand(room: Room, result: Omit<Result, "hand" | "at" | "pot" | "board"> & { board?: Card[] }) {
  const h = room.hand!;
  const pot = potTotal(h);
  const winnerIds = new Set(result.winners.map((w) => w.id));
  for (const id of h.order) {
    const p = player(room, id);
    if (h.vpip.includes(id)) p.stats.vpip += 1;
    if (winnerIds.has(id)) {
      p.handsWon += 1;
      p.stats.streak += 1;
      p.stats.bestStreak = Math.max(p.stats.bestStreak, p.stats.streak);
      p.stats.biggestPot = Math.max(p.stats.biggestPot, result.winners.find((w) => w.id === id)!.amount);
    } else {
      p.stats.streak = 0;
    }
  }
  room.lastResult = { ...result, board: result.board ?? [], hand: h.number, at: Date.now(), pot };
  room.lastHoles = h.holes;
  room.history.push({ hand: h.number, stacks: Object.fromEntries(room.players.map((p) => [p.id, p.chips])) });
  if (room.history.length > MAX_HISTORY) room.history.splice(0, room.history.length - MAX_HISTORY);
  log(room, `🏆 ${result.text}`);
  room.hand = null;
}

function awardUncontested(room: Room, winnerId: string) {
  const h = room.hand!;
  const total = potTotal(h);
  const w = player(room, winnerId);
  w.chips += total;
  finishHand(room, {
    text: `${w.name} wins ${total} — everyone folded`,
    uncontested: true,
    winners: [{ id: winnerId, amount: total }],
    shown: [],
    board: h.board?.slice(0, BOARD_CARDS[h.street]),
  });
}

/** Splits every pot among its winners; odd chips go to the first winners left of the dealer. */
function distribute(h: Hand, ps: Pot[], winnersPerPot: string[][]) {
  const won = new Map<string, number>();
  const dealerAt = h.order.indexOf(h.dealerId);
  const fromDealer = [...h.order.slice(dealerAt + 1), ...h.order.slice(0, dealerAt + 1)];
  ps.forEach((pot, i) => {
    const winners = [...new Set(winnersPerPot[i])];
    if (winners.length === 0) fail(`Pick a winner for pot ${i + 1}`);
    if (winners.some((w) => !pot.eligible.includes(w))) fail("That player isn't in this pot");
    const share = Math.floor(pot.amount / winners.length);
    let odd = pot.amount - share * winners.length;
    for (const id of fromDealer.filter((x) => winners.includes(x))) {
      const extra = odd > 0 ? 1 : 0;
      odd -= extra;
      won.set(id, (won.get(id) ?? 0) + share + extra);
    }
  });
  return won;
}

function autoShowdown(room: Room) {
  const h = room.hand!;
  const board = h.board!;
  const live = h.order.filter((id) => isLive(h, id));
  const values = new Map(live.map((id) => [id, evaluate([...h.holes![id], ...board])]));
  const ps = pots(h);
  const winnersPerPot = ps.map((pot) => {
    const best = Math.max(...pot.eligible.map((id) => values.get(id)!.score));
    return pot.eligible.filter((id) => values.get(id)!.score === best);
  });
  const won = distribute(h, ps, winnersPerPot);
  for (const [id, amount] of won) player(room, id).chips += amount;
  for (const id of live) {
    const v = values.get(id)!;
    const s = player(room, id).stats;
    if (v.score > s.bestHandScore) {
      s.bestHandScore = v.score;
      s.bestHand = v.name;
    }
  }
  const winners = [...won].map(([id, amount]) => ({ id, amount, handName: values.get(id)!.name }));
  finishHand(room, {
    text: winners.map((w) => `${nameOf(room, w.id)} wins ${w.amount} with ${w.handName}`).join(" · "),
    uncontested: false,
    winners,
    shown: live.map((id) => ({ id, cards: h.holes![id], handName: values.get(id)!.name })),
    board,
  });
}

export function resolveShowdown(room: Room, winnersPerPot: string[][]) {
  const h = room.hand ?? fail("No hand in progress");
  if (h.street !== "showdown") fail("Finish the betting first");
  const ps = pots(h);
  if (winnersPerPot.length !== ps.length) fail("Pick a winner for every pot");
  const won = distribute(h, ps, winnersPerPot);
  for (const [id, amount] of won) player(room, id).chips += amount;
  const winners = [...won].map(([id, amount]) => ({ id, amount }));
  finishHand(room, {
    text: winners.map((w) => `${nameOf(room, w.id)} wins ${w.amount}`).join(" · "),
    uncontested: false,
    winners,
    shown: [],
  });
}

/** Lets a player flip their last-hand cards for everyone (e.g. to show a bluff). */
export function showCards(room: Room, id: string) {
  const r = room.lastResult ?? fail("Nothing to show");
  const cards = room.lastHoles?.[id] ?? fail("You have no cards to show");
  if (r.shown.some((s) => s.id === id)) fail("Already shown");
  const handName = evaluate([...cards, ...r.board]).name;
  r.shown.push({ id, cards, handName });
  log(room, `👀 ${nameOf(room, id)} shows ${handName.toLowerCase()}`);
}

export function cancelHand(room: Room) {
  const h = room.hand ?? fail("No hand in progress");
  for (const [id, amount] of Object.entries(h.contributed)) player(room, id).chips += amount;
  for (const id of h.order) player(room, id).stats.handsPlayed -= 1;
  room.hand = null;
  room.lastResult = null;
  log(room, `Hand #${h.number} cancelled — chips returned`);
}

export function rebuy(room: Room, id: string) {
  const p = player(room, id);
  if (room.hand?.order.includes(id)) fail("Wait for the hand to finish");
  p.chips += room.settings.startingChips;
  p.boughtIn += room.settings.startingChips;
  log(room, `💸 ${p.name} rebuys ${room.settings.startingChips}`);
}

export function setSittingOut(room: Room, id: string, sittingOut: boolean) {
  const p = player(room, id);
  p.sittingOut = sittingOut;
  log(room, `${p.name} ${sittingOut ? "sits out" : "is back in"}`);
}

export function adjustChips(room: Room, id: string, chips: number) {
  if (room.hand?.order.includes(id)) fail("Wait for the hand to finish");
  if (!Number.isInteger(chips) || chips < 0 || chips > 1e9) fail("Enter a whole number of chips");
  const p = player(room, id);
  log(room, `Host set ${p.name}'s stack ${p.chips} → ${chips}`);
  p.chips = chips;
}

export function removePlayer(room: Room, id: string) {
  if (id === room.hostId) fail("The host can't be removed");
  if (room.hand?.order.includes(id)) fail("Wait for the hand to finish");
  const p = player(room, id);
  room.players = room.players.filter((x) => x.id !== id);
  log(room, `${p.name} left the table`);
}

export function updateSettings(room: Room, next: Pick<Settings, "smallBlind" | "bigBlind" | "dealMode" | "blindLevelMinutes">) {
  const { smallBlind, bigBlind, dealMode, blindLevelMinutes } = next;
  if (!Number.isInteger(smallBlind) || !Number.isInteger(bigBlind) || smallBlind < 1 || bigBlind < smallBlind || bigBlind > 1e7) {
    fail("Blinds must be whole numbers with big ≥ small");
  }
  if (dealMode !== room.settings.dealMode && room.hand) fail("Change the dealing mode between hands");
  const s = room.settings;
  if (s.smallBlind !== smallBlind || s.bigBlind !== bigBlind) log(room, `Blinds are now ${smallBlind}/${bigBlind}`);
  if (s.dealMode !== dealMode) log(room, dealMode === "app" ? "📱 The app deals the cards now" : "🃏 Real cards from now on");
  if (s.blindLevelMinutes !== blindLevelMinutes) {
    room.levelStartedAt = Date.now();
    log(room, blindLevelMinutes ? `⏱ Blinds double every ${blindLevelMinutes} min` : "⏱ Blind levels off");
  }
  room.settings = { ...s, smallBlind, bigBlind, dealMode, blindLevelMinutes };
}

export function react(room: Room, id: string, emoji: string) {
  if (!REACTIONS.includes(emoji)) fail("Unknown reaction");
  const now = Date.now();
  const mine = room.reactions.filter((r) => r.playerId === id && now - r.at < 3000);
  if (mine.length >= 3) fail("Easy there — one sec");
  room.reactions.push({ id: `${now}-${id}`, playerId: id, emoji, at: now });
  room.reactions = room.reactions.filter((r) => now - r.at < 20_000).slice(-24);
}
