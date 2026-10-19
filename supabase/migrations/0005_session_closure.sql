-- Irreversible session closure, enforced by the database.
-- A session is a row of public.workspaces. Closure and every card write serialize on that
-- row's lock: a card write that holds the lock before closure is retained, and one whose
-- check follows the closure commit is rejected, however early its request started.
-- Idempotent: safe to apply any number of times.

-- A session is open or closed; a session created without a status is open. Every existing
-- row that is not closed is initialized to open before the constraint is enforced, so
-- re-applying this migration never reopens a closed session.
alter table public.workspaces alter column status set default 'open';
update public.workspaces set status = 'open' where status is distinct from 'closed' and status is distinct from 'open';
alter table public.workspaces drop constraint if exists workspaces_status_check;
alter table public.workspaces
    add constraint workspaces_status_check check (status in ('open', 'closed'));

-- Closed is final: a closed session can be neither reopened nor deleted.
create or replace function public.workspaces_protect_closed() returns trigger
language plpgsql as $$
begin
    if old.status = 'closed' then
        if tg_op = 'DELETE' then
            raise exception 'Session is closed and cannot be deleted';
        end if;
        if new.status is distinct from 'closed' then
            raise exception 'Session is closed and cannot be reopened';
        end if;
    end if;
    if tg_op = 'DELETE' then
        return old;
    end if;
    return new;
end
$$;

drop trigger if exists workspaces_protect_closed on public.workspaces;
create trigger workspaces_protect_closed
    before update or delete on public.workspaces
    for each row execute function public.workspaces_protect_closed();

-- Close a session: lock its row, then set it closed. Closing a closed session succeeds.
-- Returns 'closed', or null when no such session exists. Runs with its owner's rights
-- because the application role may not update sessions directly.
create or replace function public.close_session(p_session_id uuid) returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    current_status text;
begin
    select w.status into current_status
    from public.workspaces w
    where w.id = p_session_id
    for update;

    if not found then
        return null;
    end if;
    if current_status <> 'closed' then
        update public.workspaces set status = 'closed' where id = p_session_id;
    end if;
    return 'closed';
end
$$;

revoke all on function public.close_session(uuid) from public;
grant execute on function public.close_session(uuid) to anon;

-- Every card INSERT, UPDATE and DELETE takes a share lock on its owning session row, which
-- conflicts with the exclusive lock closure holds, and then rejects a closed session. The
-- lock read sees a closure that committed while this write waited. An UPDATE that reassigns
-- the card checks the old and the new session, locked in id order to avoid deadlocks.
-- The session row is read with its owner's rights: the application role may not lock it.
create or replace function public.cards_reject_closed_session() returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    locked_id uuid;
    locked_status text;
begin
    for locked_id in
        select distinct s from unnest(array[
            case when tg_op <> 'INSERT' then old.session_id end,
            case when tg_op <> 'DELETE' then new.session_id end
        ]) as s
        where s is not null
        order by s
    loop
        select w.status into locked_status
        from public.workspaces w
        where w.id = locked_id
        for share;

        -- A missing session is left to the foreign key.
        if found and locked_status = 'closed' then
            raise exception 'Session is closed' using errcode = 'P0001';
        end if;
    end loop;

    if tg_op = 'DELETE' then
        return old;
    end if;
    return new;
end
$$;

revoke all on function public.cards_reject_closed_session() from public;

-- Named to fire before cards_enforce_integrity: closure wins over any other rejection.
drop trigger if exists cards_closed_session_guard on public.cards;
create trigger cards_closed_session_guard
    before insert or update or delete on public.cards
    for each row execute function public.cards_reject_closed_session();

notify pgrst, 'reload schema';
