"use client";
import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import { createClient } from "@/lib/supabase-browser";

type FileState = { name: string; status: "uploading" | "ok" | "warn" | "dup" | "error"; msg: string; id?: number };

export default function Dropzone() {
  const router = useRouter();
  const [dragging, setDragging] = useState(false);
  const [files, setFiles] = useState<FileState[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const upload = useCallback(async (file: File) => {
    const set = (patch: Partial<FileState>) =>
      setFiles((prev) => prev.map((f) => (f.name === file.name ? { ...f, ...patch } : f)));
    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) { set({ status: "error", msg: "Not signed in" }); return; }
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/extract", {
        method: "POST",
        headers: { Authorization: `Bearer ${session.access_token}` },
        body: fd,
      });
      const body = await res.json();
      if (res.status === 409 || body.status === "DUPLICATE")
        set({ status: "dup", msg: body.message ?? "Already imported", id: body.statement_id });
      else if (!res.ok)
        set({ status: "error", msg: body.detail ?? `HTTP ${res.status}` });
      else if (body.warnings?.length)
        set({ status: "warn", msg: `Imported ${body.rows} rows · ${body.warnings.length} warning(s)`, id: body.statement_id });
      else
        set({ status: "ok", msg: `Imported ${body.rows} rows · verified ✓`, id: body.statement_id });
    } catch (e: any) {
      set({ status: "error", msg: e.message ?? "Upload failed" });
    }
  }, []);

  const addFiles = useCallback(async (list: FileList | null) => {
    if (!list?.length) return;
    const incoming = Array.from(list).filter((f) => f.name.toLowerCase().endsWith(".pdf"));
    setFiles((prev) => [
      ...prev,
      ...incoming.map((f) => ({ name: f.name, status: "uploading" as const, msg: "Parsing…" })),
    ]);
    for (const f of incoming) await upload(f);
    router.refresh();
  }, [upload, router]);

  return (
    <div>
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files); }}
        onClick={() => inputRef.current?.click()}
        className={`cursor-pointer rounded-xl border-2 border-dashed p-10 text-center transition-colors ${
          dragging ? "border-emerald-500 bg-emerald-950/20" : "border-slate-700 hover:border-slate-500 bg-slate-900/40"}`}
      >
        <input ref={inputRef} type="file" accept="application/pdf" multiple hidden
               onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
        <p className="text-sm text-slate-300">
          Drop bank statement PDFs here — <span className="text-slate-500">or click to browse (multi-select supported)</span>
        </p>
        <p className="text-xs text-slate-600 mt-2">
          FBN · GTBank · ADB · OmniBSIC · Ecobank · MTN MoMo — auto-detected, row-by-row balance verified
        </p>
      </div>

      {files.length > 0 && (
        <ul className="mt-4 space-y-2">
          {files.map((f) => (
            <li key={f.name} className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-2.5 text-sm">
              <span className="truncate mr-4">{f.name}</span>
              <span className="flex items-center gap-2 whitespace-nowrap">
                <span className={
                  f.status === "ok" ? "text-emerald-400" :
                  f.status === "warn" ? "text-amber-400" :
                  f.status === "dup" ? "text-sky-400" :
                  f.status === "error" ? "text-rose-400" : "text-slate-400 animate-pulse"}>
                  {f.msg}
                </span>
                {f.id && (
                  <a href={`/statements/${f.id}`} className="text-emerald-400 hover:underline">open →</a>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
