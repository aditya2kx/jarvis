export default function Loading() {
  return (
    <div className="flex flex-col gap-4 p-1" aria-busy="true" aria-label="Loading">
      <div className="h-8 w-48 animate-pulse rounded bg-muted" />
      <div className="h-64 animate-pulse rounded-lg bg-muted" />
      <div className="h-32 animate-pulse rounded-lg bg-muted" />
    </div>
  );
}
