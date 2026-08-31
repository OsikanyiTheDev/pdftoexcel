import Link from "next/link";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase-server";
import { Statement, fmt, fmtDate } from "@/lib/types";
import Dropzone from "@/components/Dropzone";
import SignOut from "@/components/SignOut";

export const dynamic = "force-dynamic";

export default async function Dashboard() {
  const supabase = createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: statements } = await supabase
    .from("statements")
    .select("*")
    .order("created_at", { ascending: false });

  const rows = (statements ?? []) as Statement[];
  const totalIn = rows.reduce((a, r) => a + (r.total_credit ?? 0), 0);
  const totalOut = rows.reduce((a, r) => a + (r.total_debit ?? 0), 0);
  const totalTx = rows.reduce((a, r) => a + (r.row_count ?? 0), 0);

  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <header className="flex items-center justify-between mb-10">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Statement Intel</h1>
          <p className="text-sm text-slate-400 mt-1">
            Signed in as <span className="text-slate-300">{user.email}</span>
          </p>
        </div>
        <SignOut />
      </header>

      <section className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <Card label="Statements" value={String(rows.length)} />
        <Card label="Transactions" value={totalTx.toLocaleString()} />
        <Card label="Total in" value={fmt(totalIn)} tone="text-emerald-400" />
        <Card label="Total out" value={fmt(totalOut)} tone="text-rose-400" />
      </section>

      <Dropzone />

      <h2 className="text-sm font-medium text-slate-400 uppercase tracking-wider mt-12 mb-4">
        Imported statements
      </h2>
      {rows.length === 0 ? (
        <p className="text-slate-500 text-sm">Nothing imported yet — drop a PDF above.</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-slate-800">
          <table className="w-full text-sm">
            <thead className="bg-slate-900 text-slate-400 text-left">
              <tr>
                <Th>File</Th><Th>Bank / Account</Th><Th>Period</Th>
                <Th className="text-right">In</Th><Th className="text-right">Out</Th>
                <Th className="text-right">Rows</Th><Th>Status</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {rows.map((s) => (
                <tr key={s.id} className="hover:bg-slate-900/60">
                  <Td>
                    <Link href={`/statements/${s.id}`} className="text-emerald-400 hover:underline">
                      {s.original_filename}
                    </Link>
                  </Td>
                  <Td>{s.bank_name}<span className="text-slate-500 block text-xs">{s.account_number}</span></Td>
                  <Td>{fmtDate(s.period_start)} → {fmtDate(s.period_end)}</Td>
                  <Td className="text-right text-emerald-400">{fmt(s.total_credit, s.currency)}</Td>
                  <Td className="text-right text-rose-400">{fmt(s.total_debit, s.currency)}</Td>
                  <Td className="text-right">{s.row_count}</Td>
                  <Td><StatusPill status={s.status} issues={s.verification?.issues?.length ?? 0} /></Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}

function Card({ label, value, tone = "text-slate-100" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
      <div className="text-xs uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${tone}`}>{value}</div>
    </div>
  );
}
const Th = ({ children, className = "" }: any) => (
  <th className={`px-4 py-3 font-medium ${className}`}>{children}</th>
);
const Td = ({ children, className = "" }: any) => (
  <td className={`px-4 py-3 ${className}`}>{children}</td>
);
function StatusPill({ status, issues }: { status: string; issues: number }) {
  const ok = status === "OK";
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
      ok ? "bg-emerald-950 text-emerald-400 border border-emerald-800" : "bg-amber-950 text-amber-400 border border-amber-800"}`}>
      {ok ? "✓ verified" : `⚠ ${issues} warning${issues === 1 ? "" : "s"}`}
    </span>
  );
}
