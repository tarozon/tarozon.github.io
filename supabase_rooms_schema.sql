-- Run this in Supabase SQL Editor to create the rooms table for 실시간 리딩 교환.

create table if not exists rooms (
  id uuid primary key default gen_random_uuid(),
  room_code text unique not null,
  state_json jsonb not null,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index if not exists idx_rooms_room_code on rooms(room_code);
