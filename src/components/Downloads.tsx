"use client";
import { useState } from "react";
import { createClient } from "@/lib/supabase-browser";

export default function Downloads({
  xlsxPath, pdfPath, filename,
}: { xlsxPath: string; pdfPath: string; filename: string }) {
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  async function download(bucket: "exports" | "statements", path: string, saveAs: string) {
    setBusy(bucket); setErr("");
    try {
      const sb = createClient();
      const { data, error } = await sb.storage.from(bucket).createSignedUrl(path, 300);
      if (error || !data?.signedUrl)
        throw new Error(error?.message ?? `Could not create a download link in bucket "${bucket}" — is the file there?`);
      // fetch as blob -> triggers a real file download (immune to popup blockers)
      const res = await fetch(data.signedUrl);
      if (!res.ok) throw new Error(`Storage returned HTTP ${res.status} for ${path}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = saveAs;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (e: any) {
      setErr(e?.message ?? "Download failed");
    } finally {
      setBusy("");
    }
  }

  const base = filename.replace(/\.pdf$/i, "");
  return (
    <div className="text-right">
      <div className="flex gap-2 justify-end">
        <button
          onClick={() => download("exports", xlsxPath, `${base}.xlsx`)}
          disabled={busy !== ""}
          className="rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-3 py-1.5 text-sm font-medium">
          {busy === "exports" ? "Preparing…" : "⬇ Excel"}
        </button>
        <button
          onClick={() => download("statements", pdfPath, filename)}
          disabled={busy !== ""}
          className="rounded-lg border border-slate-700 hover:bg-slate-800 px-3 py-1.5 text-sm text-slate-300 disabled:opacity-50">
          {busy === "statements" ? "…" : "Original PDF"}
        </button>
      </div>
      {err && <p className="mt-2 text-xs text-rose-400 max-w-xs">{err}</p>}
    </div>
  );
}
