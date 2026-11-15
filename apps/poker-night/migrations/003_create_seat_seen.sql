-- When each seat's phone last polled a table. A seat is handed to a new phone only once its old phone
-- has gone quiet, without writing to the versioned room row on every poll.
create table if not exists seat_seen (
  room_id text not null,
  player_id text not null,
  seen_at timestamptz not null default now(),
  primary key (room_id, player_id)
);
