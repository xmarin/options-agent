-- One-time setup: run this once in the Supabase SQL Editor.
-- Pure stock buy/sell ledger (no options) used to compute the actual cost
-- basis of shares at the moment a covered call assignment sold them --
-- as opposed to today's snapshot in `positions`, which drifts every time
-- more shares of the same ticker are bought later.

create table public.stock_transactions (
  id uuid default gen_random_uuid() primary key,
  created_at timestamptz default now(),
  ticker text not null,
  transaction_date date not null,
  action text not null check (action in ('buy', 'sell')),
  shares numeric(12, 4) not null,
  price numeric(12, 4),
  amount numeric(12, 2),
  -- Lets both the one-time backfill and the weekly Import Schwab append use
  -- upsert (on this constraint) so re-running either is always safe.
  unique (ticker, transaction_date, action, shares, price)
);

create index stock_transactions_ticker_date_idx
  on public.stock_transactions (ticker, transaction_date);

alter table public.stock_transactions enable row level security;

-- Same access level as `positions`: signed-in account only, not the public anon key.
create policy "Authenticated read" on public.stock_transactions
  for select using (auth.role() = 'authenticated');
create policy "Authenticated insert" on public.stock_transactions
  for insert with check (auth.role() = 'authenticated');
create policy "Authenticated update" on public.stock_transactions
  for update using (auth.role() = 'authenticated');
create policy "Authenticated delete" on public.stock_transactions
  for delete using (auth.role() = 'authenticated');
