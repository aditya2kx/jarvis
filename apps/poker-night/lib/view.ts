import { BOARD_CARDS, type Room } from "./game";

/**
 * What one viewer is allowed to see. The deck, undealt board cards and every other
 * player's hole cards never leave the server.
 */
export function viewFor(room: Room, viewerId: string | null): Room {
  const view: Room = structuredClone(room);
  delete view.lastHoles;
  const h = view.hand;
  if (h) {
    delete h.deck;
    if (h.board) h.board = h.board.slice(0, BOARD_CARDS[h.street]);
    if (h.holes) {
      const mine = viewerId ? h.holes[viewerId] : undefined;
      h.holes = mine ? { [viewerId!]: mine } : {};
    }
  }
  return view;
}
