# Statement Intel

Bank-statement extraction, verification and reconciliation for Ghanaian banks.
Upload multi-page PDFs → every transaction is parsed, copy-written (category / party /
reference), **row-by-row balance-verified**, and stored in Postgres — with a formatted
Excel export of every statement.

**Supported formats (auto-detected):** FirstBank Ghana · Guaranty Trust Bank (Ghana) ·
ADB (Agricultural Development Bank) · OmniBSIC · Ecobank Ghana · MTN Mobile Money (MoMo)
merchant statements. New formats = one parser module + one line in the dispatcher.

## Architecture

```
Browser (Next.js on Vercel)                Python serverless fn (Vercel)         Supabase
┌─────────────────────────────┐   POST     ┌──────────────────────────┐        ┌─────────────────┐
│ /  dashboard + dropzone     │ ────────▶  │ /api/extract             │ ─────▶ │ Storage: PDFs   │
│ /statements/[id] table      │ ◀────────  │  verify JWT → org        │        │          + XLSX │
│      search/filter/download │  JSON      │  parse (proven pipeline) │ ─────▶ │ Postgres:       │
└─────────────────────────────┘            │  verify balances         │        │  accounts       │
        Supabase Auth (magic link)         │  build xlsx              │        │  statements     │
                                           └──────────────────────────┘        │  transactions   │
                                                                               │  extraction_logs│
                                                                               └─────────────────┘
```

- **Parser (Python, `api/_core/`)** is the battle-tested engine that already processed
  10 real statements (~2,700 rows) across 6 institutions with zero balance mismatches
  (MoMo source-data quirks excepted — transcribed as printed). It ships unchanged here.
- **Row-level security**: every table is org-scoped; the server function writes with the
  service role; the UI reads with the signed-in user's JWT. Multi-tenant from day 1.
- **Duplicate protection**: file SHA-256 dedupe on import; `txn_hash` on every row +
  `ledger_dedup` view to merge *overlapping statements* (e.g. two FBN statements that
  share the same day).

## Deploy

1. **Supabase** (free tier is fine): create a project → SQL Editor → run
   `supabase/schema.sql` (tables, RLS, trigger, buckets, view). In
   *Authentication → Providers → Email*: enable; disable "Confirm email" for
   friction-free internal sign-in.
2. **Push this repo to GitHub** and import it in **Vercel** (framework: Next.js —
   auto-detected; the Python function in `api/` deploys automatically).
3. **Vercel → Settings → Environment Variables** (Production + Preview):

   | Name | Value |
   |---|---|
   | `NEXT_PUBLIC_SUPABASE_URL` | `https://<proj>.supabase.co` |
   | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | anon public key |
   | `SUPABASE_URL` | same URL (server-side) |
   | `SUPABASE_SERVICE_ROLE_KEY` | service_role key (server-side **secret**) |
4. Deploy → sign in with a magic link → drop PDFs.

> ⚠️ Vercel **Hobby** tier is licensed for non-commercial use. Fine for evaluation;
> move to Pro (or any commercial-ok host) for business use.

## Local development

```bash
npm install
# terminal 1 — web
npm run dev
# terminal 2 — python function (local)
cd api && pip install -r ../requirements.txt && \
  DEV_BYPASS_AUTH=1 uvicorn extract:app --port 8000 --app-dir .
# then in .env.local set NEXT_PUBLIC_API_BASE=http://localhost:8000
```
(`DEV_BYPASS_AUTH=1` skips JWT checks — **never** enable it in production.)

## Repo layout

```
api/extract.py          FastAPI function: auth → parse → persist → respond
api/_core/parser_fbn.py FirstBank parser (watermark-filtered, coordinate-based)
api/_core/parser_banks.py  GT / ADB / OmniBSIC / Ecobank / MoMo parsers
api/_core/dispatcher.py format sniffing + error taxonomy
api/_core/pipeline.py   Supabase persistence + dedupe + xlsx
api/_core/xlsx.py       openpyxl Excel builder (2 sheets, styled)
supabase/schema.sql     tables, RLS, trigger, buckets, dedupe ledger view
src/                    Next.js UI (dashboard, dropzone, statement detail)
```

## Roadmap (Phase 2)

- [ ] Master-ledger page: all statements merged on `ledger_dedup`, month × category matrix
- [ ] Counterparty analytics (top payees, recurring patterns, salary/fee trends)
- [ ] `DEV_BYPASS` removal + invite-flow org members (role column already exists)
- [ ] OCR fallback for scanned statements
- [ ] Optional "private mode": port parsers to WASM so files never leave the browser
      (existing Python output serves as the golden test oracle)
