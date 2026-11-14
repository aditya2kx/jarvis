export type Street = "preflop" | "flop" | "turn" | "river" | "showdown";

export type Player = {
  id: string;
  name: string;
  chips: number;
  boughtIn: number;
  handsWon: number;
  sittingOut: boolean;
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
  toAct: string | null;
  currentBet: number;
  minRaise: number;
};

export type Pot = { amount: number; eligible: string[] };

export type LogEntry = { at: number; text: string };

export type Room = {
  id: string;
  createdAt: number;
  hostId: string;
  settings: { startingChips: number; smallBlind: number; bigBlind: number };
  players: Player[];
  hand: Hand | null;
  handCount: number;
  lastDealerId: string | null;
  lastResult: string | null;
  log: LogEntry[];
};

export type Action =
  | { type: "fold" }
  | { type: "check" }
  | { type: "call" }
  | { type: "raise"; to: number }
  | { type: "allin" };

export const MAX_PLAYERS = 10;
const MAX_LOG = 60;
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

export function createRoom(
  id: string,
  host: { id: string; name: string },
  settings: Room["settings"],
): Room {
  const room: Room = {
    id,
    createdAt: Date.now(),
    hostId: host.id,
    settings,
    players: [],
    hand: null,
    handCount: 0,
    lastDealerId: null,
    lastResult: null,
    log: [],
  };
  addPlayer(room, host);
  return room;
}

export function addPlayer(room: Room, p: { id: string; name: string }) {
  if (room.players.length >= MAX_PLAYERS) fail(`Table is full (${MAX_PLAYERS} max)`);
  const name = cleanName(p.name);
  if (room.players.some((x) => x.name.toLowerCase() === name.toLowerCase())) {
    fail("Someone at the table already has that name");
  }
  room.players.push({
    id: p.id,
    name,
    chips: room.settings.startingChips,
    boughtIn: room.settings.startingChips,
    handsWon: 0,
    sittingOut: false,
  });
  log(room, `${name} joined with ${room.settings.startingChips}`);
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

export function startHand(room: Room) {
  if (room.hand) fail("A hand is already in progress");
  const seated = room.players.filter((p) => !p.sittingOut && p.chips > 0).map((p) => p.id);
  if (seated.length < 2) fail("Need at least 2 players with chips");

  const allIds = room.players.map((p) => p.id);
  const prev = room.lastDealerId;
  const dealerId =
    prev && allIds.includes(prev)
      ? nextAfter(allIds, prev, (id) => seated.includes(id))!
      : seated[0];
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
    toAct: null,
    currentBet: room.settings.bigBlind,
    minRaise: room.settings.bigBlind,
  };
  room.hand = h;
  room.handCount = h.number;
  room.lastDealerId = dealerId;
  room.lastResult = null;
  pay(room, h, sbId, room.settings.smallBlind);
  pay(room, h, bbId, room.settings.bigBlind);
  h.currentBet = Math.max(room.settings.bigBlind, ...Object.values(h.bets));
  log(room, `Hand #${h.number} — ${nameOf(room, dealerId)} deals`);

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
      log(room, `${p.name} calls ${paid}${p.chips === 0 ? " (all in)" : ""}`);
      break;
    }
    case "raise":
    case "allin": {
      const max = mine + p.chips;
      const to = action.type === "allin" ? max : Math.floor(action.to);
      if (!Number.isFinite(to) || to <= 0) fail("Enter an amount");
      if (to > max) fail(`You only have ${p.chips}`);
      if (to <= h.currentBet) {
        if (to !== max) fail(`Raise must be more than ${h.currentBet}`);
        pay(room, h, actorId, to - mine);
        log(room, `${p.name} calls ${to - mine} (all in)`);
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
      log(room, `${p.name} ${verb} ${to}${p.chips === 0 ? " (all in)" : ""}`);
      break;
    }
    default: {
      const never: never = action;
      fail(`Unknown action ${JSON.stringify(never)}`);
    }
  }
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
  while (h.toAct === null || !needsToAct(room, h, h.toAct)) {
    h.toAct = nextAfter(h.order, h.toAct ?? h.dealerId, (id) => needsToAct(room, h, id));
    if (h.toAct !== null) return;
    endStreet(room);
    if (h.street === "showdown") return;
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
    log(room, "Showdown — flip your cards");
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

function awardUncontested(room: Room, winnerId: string) {
  const h = room.hand!;
  const total = potTotal(h);
  const w = player(room, winnerId);
  w.chips += total;
  w.handsWon += 1;
  room.lastResult = `${w.name} wins ${total} (everyone folded)`;
  log(room, room.lastResult);
  room.hand = null;
}

export function resolveShowdown(room: Room, winnersPerPot: string[][]) {
  const h = room.hand ?? fail("No hand in progress");
  if (h.street !== "showdown") fail("Finish the betting first");
  const ps = pots(h);
  if (winnersPerPot.length !== ps.length) fail("Pick a winner for every pot");
  const won = new Map<string, number>();
  ps.forEach((pot, i) => {
    const winners = [...new Set(winnersPerPot[i])];
    if (winners.length === 0) fail(`Pick a winner for pot ${i + 1}`);
    if (winners.some((w) => !pot.eligible.includes(w))) fail("That player isn't in this pot");
    const ordered = h.order.filter((id) => winners.includes(id));
    const share = Math.floor(pot.amount / ordered.length);
    let odd = pot.amount - share * ordered.length;
    const fromDealer = [...h.order.slice(h.order.indexOf(h.dealerId) + 1), ...h.order.slice(0, h.order.indexOf(h.dealerId) + 1)];
    for (const id of fromDealer.filter((x) => ordered.includes(x))) {
      const extra = odd > 0 ? 1 : 0;
      odd -= extra;
      won.set(id, (won.get(id) ?? 0) + share + extra);
    }
  });
  const parts: string[] = [];
  for (const [id, amount] of won) {
    const p = player(room, id);
    p.chips += amount;
    p.handsWon += 1;
    parts.push(`${p.name} wins ${amount}`);
  }
  room.lastResult = parts.join(" · ");
  log(room, room.lastResult);
  room.hand = null;
}

export function cancelHand(room: Room) {
  const h = room.hand ?? fail("No hand in progress");
  for (const [id, amount] of Object.entries(h.contributed)) player(room, id).chips += amount;
  room.hand = null;
  room.lastResult = `Hand #${h.number} cancelled — chips returned`;
  log(room, room.lastResult);
}

export function rebuy(room: Room, id: string) {
  const p = player(room, id);
  if (room.hand?.order.includes(id)) fail("Wait for the hand to finish");
  p.chips += room.settings.startingChips;
  p.boughtIn += room.settings.startingChips;
  log(room, `${p.name} rebuys ${room.settings.startingChips}`);
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

export function updateBlinds(room: Room, smallBlind: number, bigBlind: number) {
  if (!Number.isInteger(smallBlind) || !Number.isInteger(bigBlind) || smallBlind < 1 || bigBlind < smallBlind || bigBlind > 1e7) {
    fail("Blinds must be whole numbers with big ≥ small");
  }
  room.settings.smallBlind = smallBlind;
  room.settings.bigBlind = bigBlind;
  log(room, `Blinds are now ${smallBlind}/${bigBlind}`);
}
