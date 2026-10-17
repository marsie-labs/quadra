-- Workspace schema: a workspace owns one canvas, a canvas owns four quadrants.
-- Idempotent: safe to apply any number of times.

create table if not exists public.workspaces (
    id           uuid primary key,
    feature_name text not null check (length(btrim(feature_name)) > 0),
    status       text not null default 'open',
    created_at   timestamptz not null default now()
);

create table if not exists public.canvases (
    id           uuid primary key,
    workspace_id uuid not null references public.workspaces (id) on delete cascade,
    created_at   timestamptz not null default now()
);

create table if not exists public.quadrants (
    id        uuid primary key,
    canvas_id uuid not null references public.canvases (id) on delete cascade,
    name      text not null check (name in ('IS', 'IS NOT', 'DOES', 'DOES NOT')),
    position  smallint not null,
    unique (canvas_id, name)
);

create index if not exists canvases_workspace_id_idx on public.canvases (workspace_id);
create index if not exists quadrants_canvas_id_idx on public.quadrants (canvas_id);

alter table public.workspaces enable row level security;
alter table public.canvases enable row level security;
alter table public.quadrants enable row level security;

grant select, insert on public.workspaces to anon;
grant select, insert on public.canvases to anon;
grant select, insert on public.quadrants to anon;

drop policy if exists workspaces_anon_insert on public.workspaces;
create policy workspaces_anon_insert on public.workspaces
    for insert to anon with check (true);
drop policy if exists workspaces_anon_select on public.workspaces;
create policy workspaces_anon_select on public.workspaces
    for select to anon using (true);

drop policy if exists canvases_anon_insert on public.canvases;
create policy canvases_anon_insert on public.canvases
    for insert to anon with check (true);
drop policy if exists canvases_anon_select on public.canvases;
create policy canvases_anon_select on public.canvases
    for select to anon using (true);

drop policy if exists quadrants_anon_insert on public.quadrants;
create policy quadrants_anon_insert on public.quadrants
    for insert to anon with check (true);
drop policy if exists quadrants_anon_select on public.quadrants;
create policy quadrants_anon_select on public.quadrants
    for select to anon using (true);
