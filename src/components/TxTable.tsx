"use client";
import { useMemo, useState } from "react";
import { Txn, fmt } from "@/lib/types";

const PAGE = 100;

export default function TxTable({ rows }: { rows: Txn[] }) {
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("ALL");
  const [page, setPage] = useState(0);

  const categories = useMemo(
    () => ["ALL", ...Array.from(new Set(rows.map((r) => r.category))).sort()],
    [rows]
  );
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return rows.filter((r) => {
      if (cat !== "ALL" && r.category !== cat) return false;
      if (!needle) return true;
      return (
        r.raw_details.toLowerCase().includes(needle) ||
        r.party.toLowerCase().includes(needle) ||
        (r.ref_number ?? "").toLowerCase().includes(needle) ||
        r.trans_date.includes(needle)
      );
    });
  }, [rows, q, cat]);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE));
  const p = Math.min(page, pages - 1);
  const view = filtered.slice(p * PAGE, (p + 1) * PAGE);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <input
          value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }}
          placeholder="Search details, party, ref, date…"
          className="w-72 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-emerald-600"
        />
        <select
          value={cat} onChange={(e) => { setCat(e.target.value); setPage(0); }}
          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-emerald-600"
        >
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <span className="text-xs text-slate-500">{filtered.length} rows</span>
        <span className="ml-auto text-xs text-slate-500">
          Page {p + 1}/{pages}
          {"  "}
          <button onClick={() => setPage(Math.max(0, p - 1))} disabled={p === 0}
                  className="mx-1 rounded border border-slate-700 px-2 py-1 disabled:opacity-30">←</button>
          <button onClick={() => setPage(Math.min(pages - 1, p + 1))} disabled={p >= pages - 1}
                  className="rounded border border-slate-700 px-2 py-1 disabled:opacity-30">→</button>
        </span>
      </div>

      <div className="overflow-x-auto rounded-xl border border-slate-800">
        <table className="w-full text-xs">
          <thead className="bg-slate-900 text-slate-400 text-left">
            <tr>
              <th className="px-3 py-2.5">Date</th>
              <th className="px-3 py-2.5">Details</th>
              <th className="px-3 py-2.5">Category</th>
              <th className="px-3 py-2.5">Party</th>
              <th className="px-3 py-2.5 text-right">Withdrawal</th>
              <th className="px-3 py-2.5 text-right">Deposit</th>
              <th className="px-3 py-2.5 text-right">Balance</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/70 font-mono">
            {view.map((r) => (
              <tr key={r.id} className="hover:bg-slate-900/60">
                <td className="px-3 py-2 whitespace-nowrap text-slate-400">{r.trans_date}</td>
                <td className="px-3 py-2 max-w-md truncate" title={r.raw_details}>{r.raw_details}</td>
                <td className="px-3 py-2 whitespace-nowrap font-sans text-slate-300">{r.category}</td>
                <td className="px-3 py-2 max-w-40 truncate font-sans text-slate-400" title={r.party}>{r.party}</td>
                <td className="px-3 py-2 text-right text-rose-400">{r.withdrawal ? fmt(r.withdrawal) : ""}</td>
                <td className="px-3 py-2 text-right text-emerald-400">{r.deposit ? fmt(r.deposit) : ""}</td>
                <td className="px-3 py-2 text-right text-slate-200">{fmt(r.balance)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
