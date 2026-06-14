-- iScraper v1 database schema (Supabase Postgres + pgvector)
--
-- Run this once in the Supabase SQL editor (Dashboard -> SQL -> New query).
-- Re-running is safe: it uses IF NOT EXISTS / CREATE OR REPLACE.
--
-- Embedding dimension assumes Google Gemini `gemini-embedding-2` (768 dims).
-- If you change EMBEDDING_MODEL / EMBEDDING_DIM in core/config.py, change the
-- vector(768) columns and the match function signature below to match.

create extension if not exists vector;

-- A person who configures iScraper and receives matched source posts.
create table if not exists users (
    id                       bigint primary key,            -- Telegram user id
    created_at               timestamptz not null default now(),
    -- one default saved match profile per user (nullable until the user saves one)
    match_profile            text,
    timezone                 text   not null default 'Africa/Addis_Ababa',
    past_search_lookback     int    not null default 15,    -- days
    -- Ongoing Alert configuration
    alert_mode               text   not null default 'off', -- 'off' | 'interval' | 'near_live'
    alert_match_profile      text,                          -- scoped profile for the active alert; null => use saved match_profile
    alert_delivery_chat_id   bigint,                        -- active delivery destination; null => DM (== users.id)
    delivery_group_chat_id   bigint,                        -- a group the user registered via /here (remembered even while using DM)
    -- interval (Every N days) settings
    alert_interval_days      int,
    alert_delivery_time      text,                          -- 'HH:MM' in the user's timezone
    alert_last_sent_at       timestamptz,                   -- interval cursor
    -- near-live (Every N minutes) settings
    alert_interval_minutes   int,
    near_live_started_at     timestamptz,                   -- only consider posts at/after this
    near_live_last_checked_at timestamptz
);

-- Per-user multi-step conversation state for the webhook bot (all state lives here
-- because the webhook is stateless between invocations).
create table if not exists conversation_state (
    user_id    bigint primary key references users(id) on delete cascade,
    state      text,                                       -- current step key, null => idle
    data       jsonb not null default '{}'::jsonb,         -- scratch data for the active flow
    updated_at timestamptz not null default now()
);

-- A Telegram channel that iScraper watches for source posts (per user).
create table if not exists source_channels (
    id        bigserial primary key,
    user_id   bigint not null references users(id) on delete cascade,
    username  text   not null,                             -- normalized, lowercase, no '@'
    added_at  timestamptz not null default now(),
    unique (user_id, username)
);

-- A Telegram message from a source channel. Stored globally and reused across all
-- users watching the same channel. Telegram albums are stored as one logical row
-- keyed by the canonical Telegram message link for the album.
create table if not exists source_posts (
    id                 bigserial primary key,
    channel_username   text   not null,                    -- normalized, lowercase, no '@'
    message_id         bigint not null,                     -- canonical message id; album link target when grouped
    message_link       text   not null,
    album_grouped_id   text,                                -- Telegram grouped_id for albums, null for standalone posts
    posted_at          timestamptz,
    edited_at          timestamptz,
    scraped_at         timestamptz not null default now(),
    caption            text,
    image_text         text,
    image_count        int not null default 0,
    content            text   not null,
    normalized_content text   not null,
    embedding          vector(768),
    unique (channel_username, message_id)
);

create index if not exists source_posts_channel_idx on source_posts (channel_username);
create index if not exists source_posts_posted_idx  on source_posts (posted_at);
create index if not exists source_posts_album_idx   on source_posts (channel_username, album_grouped_id);
create unique index if not exists source_posts_message_link_idx on source_posts (message_link);

-- A source post that iScraper considered relevant to a user. Used to deduplicate
-- near-live deliveries (don't send the same post to the same user twice).
create table if not exists matches (
    id             bigserial primary key,
    user_id        bigint not null references users(id) on delete cascade,
    source_post_id bigint not null references source_posts(id) on delete cascade,
    score          real,
    context        text   not null,                        -- 'near_live' | 'interval' | 'past_search'
    matched_at     timestamptz not null default now(),
    unique (user_id, source_post_id, context)
);

-- Small job queue. The bot enqueues long-running work (Past Search) and the
-- scheduled worker drains it.
create table if not exists jobs (
    id          bigserial primary key,
    user_id     bigint not null references users(id) on delete cascade,
    type        text   not null,                           -- 'past_search'
    payload     jsonb  not null default '{}'::jsonb,        -- e.g. {"lookback_days": 15, "match_profile": "..."}
    status      text   not null default 'pending',          -- pending | running | done | error
    created_at  timestamptz not null default now(),
    started_at  timestamptz,
    finished_at timestamptz,
    error       text
);

create index if not exists jobs_status_idx on jobs (status, created_at);

-- Semantic search over stored source posts for one user.
--
-- Returns source posts whose embedding is at least `match_threshold` cosine
-- similarity to `query_embedding`, restricted to the given channel usernames and
-- posted at/after `posted_after`. `posted_after` may be null to ignore the lower
-- time bound. Results are ordered most-similar first.
create or replace function match_source_posts (
    query_embedding vector(768),
    channel_usernames text[],
    match_threshold float,
    posted_after timestamptz default null
)
returns table (
    id           bigint,
    channel_username text,
    message_id   bigint,
    message_link text,
    posted_at    timestamptz,
    content      text,
    similarity   float
)
language sql stable
as $$
    select
        sp.id,
        sp.channel_username,
        sp.message_id,
        sp.message_link,
        sp.posted_at,
        sp.content,
        1 - (sp.embedding <=> query_embedding) as similarity
    from source_posts sp
    where sp.embedding is not null
      and sp.channel_username = any (channel_usernames)
      and (posted_after is null or sp.posted_at >= posted_after)
      and 1 - (sp.embedding <=> query_embedding) >= match_threshold
    order by sp.embedding <=> query_embedding
$$;
