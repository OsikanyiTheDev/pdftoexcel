import Link from "next/link";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase-server";
import { Statement, Txn, fmt, fmtDate } from "@/lib/types";
import TxTable from "@/components/TxTable";
import Downloads from "@/components/Downloads";

export const dynamic = "force-dynamic";

export default async function StatementPage({ params }: { params: { id: string } }) {
  const supabase = createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: sData } = await supabase
    .from("statements").select("*").eq("id", params.id).single();
  if (!sData) {
    return <main className="p-10 text-slate-400">Statement not found. <Link className="text-emerald-400" href="/">Back</Link></main>;
  }
  const s = sData as Statement;

  const { data: txData } = await supabase
    .from("transactions").select("*")
    .eq("statement_id", s.id)
    .order("row_index");
  const txns = (txData ?? []) as Txn[];

  const issues = s.verification?.issues ?? [];
  const stats = s.verification?.stats ?? {};

  return (
    <main className="mx-auto max-w-7xl px-6 py-10">
      <Link href="/" className="text-sm text-slate-400 hover:text-slate-200">← All statements</Link>

      <header className="flex flex-wrap items-start justify-between gap-4 mt-4 mb-8">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{s.original_filename}</h1>
          <p className="text-sm text-slate-400 mt-1">
            {s.bank_name} · {s.account_name} · <span className="font-mono">{s.account_number}</span> ·{" "}
            {fmtDate(s.period_start)} → {fmtDate(s.period_end)} · {s.pages ?? "?"} pages · {s.row_count} rows
          </p>
        </div>
        <Downloads xlsxPath={`exports/${s.storage_path.split("/")[0]}/${s.id}.xlsx`} pdfPath={s.storage_path} filename={s.original_filename} />
      </header>

      <section className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
        <Stat label="Opening" value={fmt(s.opening_balance, s.currency)} />
        <Stat label="Closing" value={fmt(s.closing_balance, s.currency)} />
        <Stat label="Total in" value={fmt(s.total_credit, s.currency)} tone="text-emerald-400" />
        <Stat label="Total out" value={fmt(s.total_debit, s.currency)} tone="text-rose-400" />
        <Stat label="Balance check"
              value={issues.length === 0 ? "✓ every row" : `${stats.bad ?? "?"} mismatch(es)`}
              tone={issues.length === 0 ? "text-emerald-400" : "text-amber-400"} />
      </section>

      {issues.length > 0 && (
        <div className="rounded-xl border border-amber-800 bg-amber-950/30 p-4 mb-6">
          <div className="text-sm font-medium text-amber-400 mb-1">Verification warnings</div>
          <ul className="text-xs text-amber-300/80 list-disc list-inside space-y-0.5">
            {issues.slice(0, 8).map((w, i) => <li key={i}>{w}</li>)}
            {issues.length > 8 && <li>… {issues.length - 8} more (see extraction log)</li>}
          </ul>
          <p className="text-[11px] text-amber-500/70 mt-2">
            Note: MTN MoMo statements contain source-data quirks (broken &quot;balance before&quot; around
            pending/reversed payments). Rows are transcribed exactly as printed.
          </p>
        </div>
      )}

      <TxTable rows={txns} />
    </main>
  );
}

function Stat({ label, value, tone = "text-slate-100" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
      <div className="text-xs uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`mt-1 text-sm font-semibold ${tone}`}>{value}</div>
    </div>
  );
}
