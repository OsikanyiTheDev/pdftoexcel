"use client";
import { useState } from "react";
import { createClient } from "@/lib/supabase-browser";

export default function Downloads({ xlsxPath, pdfPath }: { xlsxPath: string; pdfPath: string }) {
  const [busy, setBusy] = useState("");
  async function go(bucket: string, path: string) {
    setBusy(bucket);
    try {
      const sb = createClient();
      const { data, error } = await sb.storage.from(bucket).createSignedUrl(path, 300);
      if (error || !data) throw error ?? new Error("no url");
      window.open(data.signedUrl, "_blank");
    } finally {
      setBusy("");
    }
  }
  return (
    <div className="flex gap-2">
      <button onClick={() => go("exports", xlsxPath)} disabled={busy !== ""}
        className="rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-3 py-1.5 text-sm font-medium">
        {busy === "exports" ? "Preparing…" : "⬇ Excel"}
      </button>
      <button onClick={() => go("statements", pdfPath)} disabled={busy !== ""}
        className="rounded-lg border border-slate-700 hover:bg-slate-800 px-3 py-1.5 text-sm text-slate-300">
        {busy === "statements" ? "…" : "Original PDF"}
      </button>
    </div>
  );
}
