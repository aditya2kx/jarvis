import { isRed, rankLabel, SUIT_SYMBOL } from "@/lib/cards";

type Props = {
  card?: string;
  size?: "xs" | "sm" | "md" | "lg";
  faceDown?: boolean;
  slot?: boolean;
  highlight?: boolean;
  delay?: number;
};

export default function PlayingCard({ card, size = "md", faceDown, slot, highlight, delay = 0 }: Props) {
  if (slot) return <div className={`pc pc-${size} pc-slot`} aria-hidden />;
  if (faceDown || !card) {
    return (
      <div className={`pc pc-${size} pc-back deal-in`} style={{ animationDelay: `${delay}ms` }} aria-label="Face-down card">
        <span />
      </div>
    );
  }
  const suit = SUIT_SYMBOL[card[1]];
  return (
    <div
      className={`pc pc-${size} pc-face flip-in ${isRed(card) ? "red" : ""} ${highlight ? "pc-hl" : ""}`}
      style={{ animationDelay: `${delay}ms` }}
      aria-label={`${rankLabel(card)} of ${{ s: "spades", h: "hearts", d: "diamonds", c: "clubs" }[card[1]]}`}
    >
      <span className="pc-rank">{rankLabel(card)}</span>
      <span className="pc-suit-sm">{suit}</span>
      <span className="pc-suit">{suit}</span>
    </div>
  );
}
