-- Who is in a table's voice chat right now. A row counts only while its phone keeps polling (seen_at).
create table if not exists voice_members (
  room_id text not null,
  player_id text not null,
  nonce text not null,
  muted boolean not null default false,
  seen_at timestamptz not null default now(),
  primary key (room_id, player_id)
);

-- WebRTC offers/answers between two seated players. Short-lived: read within seconds, pruned after a minute.
create table if not exists voice_signals (
  id bigint generated always as identity primary key,
  room_id text not null,
  from_id text not null,
  to_id text not null,
  body jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists voice_signals_inbox_idx on voice_signals (room_id, to_id, id);
create index if not exists voice_signals_created_idx on voice_signals (created_at);
