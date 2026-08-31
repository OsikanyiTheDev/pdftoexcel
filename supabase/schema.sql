-- ============================================================
-- Statement Intel — Supabase schema (multi-tenant ready)
-- Run this in Supabase Dashboard → SQL Editor (once per project).
-- ============================================================

-- ---------- tables ----------
create table if not exists public.orgs (
  id         uuid primary key default gen_random_uuid(),
  name       text not null default 'My Workspace',
  created_at timestamptz not null default now()
);

create table if not exists public.profiles (
  id         uuid primary key references auth.users(id) on delete cascade,
  org_id     uuid not null references public.orgs(id) on delete cascade,
  email      text,
  role       text not null default 'owner',
  created_at timestamptz not null default now()
);

create table if not exists public.accounts (
  id             bigserial primary key,
  org_id         uuid not null references public.orgs(id) on delete cascade,
  bank_name      text not null,
  account_number text not null,
  account_name   text,
  account_type   text,
  currency       text not null default 'GHS',
  created_at     timestamptz not null default now(),
  unique (org_id, bank_name, account_number)
);

create table if not exists public.statements (
  id                bigserial primary key,
  org_id            uuid not null references public.orgs(id) on delete cascade,
  account_id        bigint references public.accounts(id) on delete set null,
  bank_name         text not null,
  account_number    text not null,
  account_name      text,
  currency          text not null default 'GHS',
  period_start      date,
  period_end        date,
  opening_balance   numeric(18,2),
  closing_balance   numeric(18,2),
  available_balance numeric(18,2),
  total_debit       numeric(18,2),
  total_credit      numeric(18,2),
  row_count         integer not null default 0,
  pages             integer,
  original_filename text,
  file_sha256       text,
  storage_path      text,
  dedupe_key        text unique,
  status            text not null default 'OK',
  verification      jsonb,
  created_by        uuid references auth.users(id),
  created_at        timestamptz not null default now()
);
create index if not exists idx_statements_org on public.statements (org_id, created_at desc);
create index if not exists idx_statements_acct on public.statements (account_id);

create table if not exists public.transactions (
  id            bigserial primary key,
  statement_id  bigint not null references public.statements(id) on delete cascade,
  org_id        uuid not null references public.orgs(id) on delete cascade,
  account_id    bigint references public.accounts(id) on delete set null,
  row_index     integer not null,
  trans_date    date,
  value_date    date,
  ref_number    text,
  raw_details   text,
  category      text,
  party         text,
  reference_id  text,
  withdrawal    numeric(18,2) not null default 0,
  deposit       numeric(18,2) not null default 0,
  balance       numeric(18,2),
  txn_hash      text,
  created_at    timestamptz not null default now()
);
create index if not exists idx_txn_stmt   on public.transactions (statement_id, row_index);
create index if not exists idx_txn_org    on public.transactions (org_id, trans_date);
create index if not exists idx_txn_hash   on public.transactions (account_id, txn_hash);
create index if not exists idx_txn_party  on public.transactions (org_id, party);

create table if not exists public.extraction_logs (
  id           bigserial primary key,
  statement_id bigint not null references public.statements(id) on delete cascade,
  org_id       uuid not null references public.orgs(id) on delete cascade,
  bank_key     text,
  warnings     jsonb,
  stats        jsonb,
  created_at   timestamptz not null default now()
);

-- ---------- new user -> org + profile ----------
create or replace function public.handle_new_user()
returns trigger
language plpgsql security definer set search_path = public as $$
declare
  new_org uuid;
begin
  insert into public.orgs (name)
  values (coalesce(new.raw_user_meta_data->>'full_name', 'My Workspace'))
  returning id into new_org;
  insert into public.profiles (id, org_id, email) values (new.id, new_org, new.email);
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- ---------- RLS ----------
alter table public.orgs            enable row level security;
alter table public.profiles        enable row level security;
alter table public.accounts        enable row level security;
alter table public.statements      enable row level security;
alter table public.transactions    enable row level security;
alter table public.extraction_logs enable row level security;

create policy "org members read orgs" on public.orgs
  for select to authenticated
  using (id = (select org_id from public.profiles where id = auth.uid()));

create policy "users read own profile" on public.profiles
  for select to authenticated
  using (id = auth.uid());

create policy "org members read accounts" on public.accounts
  for select to authenticated
  using (org_id = (select org_id from public.profiles where id = auth.uid()));

create policy "org members read statements" on public.statements
  for select to authenticated
  using (org_id = (select org_id from public.profiles where id = auth.uid()));

create policy "org members read transactions" on public.transactions
  for select to authenticated
  using (org_id = (select org_id from public.profiles where id = auth.uid()));

create policy "org members read logs" on public.extraction_logs
  for select to authenticated
  using (org_id = (select org_id from public.profiles where id = auth.uid()));
-- writes happen only via the server-side function (service_role bypasses RLS).

-- ---------- storage buckets (private) ----------
insert into storage.buckets (id, name, public)
values ('statements', 'statements', false), ('exports', 'exports', false)
on conflict (id) do nothing;

create policy "authenticated read statement files" on storage.objects
  for select to authenticated
  using (bucket_id in ('statements', 'exports'));

-- ---------- master ledger view (deduped across overlapping statements) ----------
create or replace view public.ledger_dedup as
  select distinct on (account_id, txn_hash)
    id, org_id, account_id, statement_id, trans_date, value_date,
    ref_number, raw_details, category, party, reference_id,
    withdrawal, deposit, balance, txn_hash
  from public.transactions
  where txn_hash is not null
  order by account_id, txn_hash, trans_date, row_index;

grant select on public.ledger_dedup to authenticated;
