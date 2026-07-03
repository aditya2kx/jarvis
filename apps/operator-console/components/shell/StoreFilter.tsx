// Single-store today (Austin/"palmetto"); Houston launches Sept 2026. Kept as
// its own component so multi-store selection is a drop-in later, not a rewrite.
export function StoreFilter({ store }: { store: string }) {
  return (
    <div className="flex items-center gap-1.5 rounded-md border border-border bg-secondary px-2.5 py-1 text-sm text-secondary-foreground">
      <span className="size-1.5 rounded-full bg-emerald-500" />
      {store}
    </div>
  );
}
