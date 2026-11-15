-- When the host's phone last polled a table. Lets the table vouch a locked-out host back in only once
-- their old phone has gone quiet, without writing to the versioned room row on every poll.
create table if not exists seat_seen (
  room_id text not null,
  player_id text not null,
  seen_at timestamptz not null default now(),
  primary key (room_id, player_id)
);
