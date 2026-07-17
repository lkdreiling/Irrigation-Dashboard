-- Supabase schema for per-user irrigation data isolation
-- This file is built to match the current app model, which authenticates users by
-- a username string rather than a Supabase Auth UUID.

create table if not exists public.profiles (
    id bigint generated always as identity primary key,
    user_id text not null unique,
    username text not null unique,
    display_name text,
    created_at timestamptz not null default now()
);

create table if not exists public.properties (
    id bigint generated always as identity primary key,
    user_id text not null,
    property_name text not null,
    zip_code text not null,
    created_at timestamptz not null default now(),
    unique (user_id, property_name)
);

create table if not exists public.zones (
    id bigint generated always as identity primary key,
    user_id text not null,
    property_name text not null,
    zone_name text not null,
    area integer not null default 1000,
    flow numeric not null default 5,
    soil text not null default 'Loam',
    depth integer not null default 12,
    mad integer not null default 50,
    start_date date not null default current_date,
    created_at timestamptz not null default now(),
    unique (user_id, property_name, zone_name)
);

create table if not exists public.watering_logs (
    id bigint generated always as identity primary key,
    user_id text not null,
    property_name text not null,
    zone_name text not null,
    log_date date not null,
    minutes numeric not null,
    inches numeric not null,
    logged_at timestamptz not null default now()
);

alter table public.profiles enable row level security;
alter table public.properties enable row level security;
alter table public.zones enable row level security;
alter table public.watering_logs enable row level security;

create policy "Profiles are viewable only by their owner"
on public.profiles
for select
using (user_id = current_setting('request.jwt.claims', true)::json->>'sub' or user_id = current_setting('request.jwt.claims', true)::json->>'username');

create policy "Profiles can be inserted only by authenticated users"
on public.profiles
for insert
with check (true);

create policy "Properties are viewable only by their owner"
on public.properties
for select
using (user_id = current_setting('request.jwt.claims', true)::json->>'sub' or user_id = current_setting('request.jwt.claims', true)::json->>'username');

create policy "Properties can be inserted only by authenticated users"
on public.properties
for insert
with check (true);

create policy "Zones are viewable only by their owner"
on public.zones
for select
using (user_id = current_setting('request.jwt.claims', true)::json->>'sub' or user_id = current_setting('request.jwt.claims', true)::json->>'username');

create policy "Zones can be inserted only by authenticated users"
on public.zones
for insert
with check (true);

create policy "Watering logs are viewable only by their owner"
on public.watering_logs
for select
using (user_id = current_setting('request.jwt.claims', true)::json->>'sub' or user_id = current_setting('request.jwt.claims', true)::json->>'username');

create policy "Watering logs can be inserted only by authenticated users"
on public.watering_logs
for insert
with check (true);
