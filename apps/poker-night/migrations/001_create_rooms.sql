create table if not exists rooms (
  id text primary key,
  state jsonb not null,
  tokens jsonb not null default '{}'::jsonb,
  version integer not null default 0,
  updated_at timestamptz not null default now()
);

create index if not exists rooms_updated_at_idx on rooms (updated_at);
