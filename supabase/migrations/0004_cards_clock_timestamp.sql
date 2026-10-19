-- Card versions follow commit order: stamp updates and soft-deletes with clock_timestamp().
-- now() is the start of the transaction, so a transaction that started first but committed
-- last would carry an older version than the change it overwrote, and clients that order
-- changes by updated_at would discard it as stale. A second update of the same card waits
-- for the first one's row lock, so clock_timestamp() is taken after the first one committed.
-- Idempotent: safe to apply any number of times.

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
        new.updated_at := clock_timestamp();
        if new.deleted_at is distinct from old.deleted_at then
            new.deleted_at := case
                when new.deleted_at is null then null
                else clock_timestamp()
            end;
        end if;
    end if;
    return new;
end
$$;
