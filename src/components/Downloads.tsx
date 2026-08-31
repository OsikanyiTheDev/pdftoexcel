"use client";
import { useState } from "react";
import { createClient } from "@/lib/supabase-browser";

export default function Downloads({
  statementId, pdfPath, filename,
}: { statementId: number; pdfPath: string; filename: string }) {
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  function saveBlob(blob: Blob, saveAs: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = saveAs;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }

  async function downloadExcel() {
    setBusy("excel"); setErr("");
    try {
      const sb = createClient();
      const { data: { session } } = await supabase_session(sb);
      if (!session) throw new Error("Not signed in — refresh the page and sign in again.");
      const res = await fetch(`/api/extract?id=${statementId}`, {
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { msg = (await res.json()).detail ?? msg; } catch { /* html */ }
        throw new Error(msg);
      }
      saveBlob(await res.blob(), filename.replace(/\.pdf$/i, "") + ".xlsx");
    } catch (e: any) {
      setErr(e?.message ?? "Excel download failed");
    } finally {
      setBusy("");
    }
  }

  async function downloadPdf() {
    setBusy("pdf"); setErr("");
    try {
      const sb = createClient();
      const { data, error } = await sb.storage.from("statements").createSignedUrl(pdfPath, 300);
      if (error || !data?.signedUrl)
        throw new Error(`Could not sign a link for "${pdfPath}" (${error?.message ?? "unknown"})`);
      const res = await fetch(data.signedUrl);
      if (!res.ok)
        throw new Error(`Storage returned HTTP ${res.status} for "${pdfPath}". If this persists, re-import the statement.`);
      saveBlob(await res.blob(), filename);
    } catch (e: any) {
      setErr(e?.message ?? "PDF download failed");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="text-right">
      <div className="flex gap-2 justify-end">
        <button onClick={downloadExcel} disabled={busy !== ""}
          className="rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-3 py-1.5 text-sm font-medium">
          {busy === "excel" ? "Building…" : "⬇ Excel"}
        </button>
        <button onClick={downloadPdf} disabled={busy !== ""}
          className="rounded-lg border border-slate-700 hover:bg-slate-800 px-3 py-1.5 text-sm text-slate-300 disabled:opacity-50">
          {busy === "pdf" ? "…" : "Original PDF"}
        </button>
      </div>
      {err && <p className="mt-2 text-xs text-rose-400 max-w-sm">{err}</p>}
    </div>
  );
}

async function supabase_session(sb: any) {
  return { data: { session: (await sb.auth.getSession()).data.session } };
}
