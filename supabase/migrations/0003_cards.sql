-- Cards: plain-text notes placed in a session's quadrant, soft-deleted rather than removed.
-- Idempotent: safe to apply any number of times.

create table if not exists public.cards (
    id                   uuid primary key,
    session_id           uuid not null references public.workspaces (id) on delete cascade,
    quadrant_id          uuid not null references public.quadrants (id) on delete cascade,
    "index"              integer not null check ("index" >= 0),
    content              text not null,
    creator_display_name text not null check (
        length(creator_display_name) > 0
        and creator_display_name = btrim(creator_display_name)
    ),
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now(),
    deleted_at           timestamptz
);

create index if not exists cards_session_id_idx on public.cards (session_id);
create index if not exists cards_quadrant_id_idx on public.cards (quadrant_id);

-- The server is authoritative: timestamps come from the database clock, authorship and
-- creation time never change, and a card can only sit in a quadrant of its own session.
create or replace function public.cards_enforce_integrity() returns trigger
language plpgsql as $$
begin
    -- A missing session is left to the foreign key; this guards quadrant/session mismatch.
    if exists (select 1 from public.workspaces w where w.id = new.session_id)
       and not exists (
        select 1
        from public.quadrants q
        join public.canvases c on c.id = q.canvas_id
        where q.id = new.quadrant_id and c.workspace_id = new.session_id
    ) then
        raise exception 'quadrant % does not belong to session %',
            new.quadrant_id, new.session_id
            using errcode = '23514';
    end if;

    if tg_op = 'INSERT' then
        new.created_at := now();
        new.updated_at := now();
        new.deleted_at := null;
    else
        if new.session_id <> old.session_id then
            raise exception 'a card cannot move to another session'
                using errcode = '23514';
        end if;
        new.creator_display_name := old.creator_display_name;
        new.created_at := old.created_at;
        new.updated_at := now();
        if new.deleted_at is distinct from old.deleted_at then
            new.deleted_at := case when new.deleted_at is null then null else now() end;
        end if;
    end if;
    return new;
end
$$;

drop trigger if exists cards_enforce_integrity on public.cards;
create trigger cards_enforce_integrity
    before insert or update on public.cards
    for each row execute function public.cards_enforce_integrity();

alter table public.cards enable row level security;

grant select, insert, update on public.cards to anon;

drop policy if exists cards_anon_insert on public.cards;
create policy cards_anon_insert on public.cards
    for insert to anon with check (true);
drop policy if exists cards_anon_select on public.cards;
create policy cards_anon_select on public.cards
    for select to anon using (true);
drop policy if exists cards_anon_update on public.cards;
create policy cards_anon_update on public.cards
    for update to anon using (true) with check (true);

-- Publish card changes (soft-delete is an UPDATE) to Supabase Realtime.
do $$
begin
    if not exists (
        select 1
        from pg_publication_tables
        where pubname = 'supabase_realtime'
          and schemaname = 'public'
          and tablename = 'cards'
    ) then
        alter publication supabase_realtime add table public.cards;
    end if;
end
$$;
