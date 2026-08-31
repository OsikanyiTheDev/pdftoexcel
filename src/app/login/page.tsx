"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase-browser";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");

  async function signInWithPassword(e: React.FormEvent) {
    e.preventDefault();
    setBusy("pw"); setErr(""); setNote("");
    const supabase = createClient();
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) { setErr(error.message); setBusy(""); return; }
    router.replace("/");
    router.refresh();
  }

  async function sendMagicLink(e: React.FormEvent) {
    e.preventDefault();
    setBusy("magic"); setErr(""); setNote("");
    const supabase = createClient();
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: `${location.origin}/auth/callback` },
    });
    setBusy("");
    if (error) setErr(error.message);
    else {
      setSent(true);
      setNote("Open the link in THIS browser, in the same window you just used. "
            + "If it fails with 'invalid or expired', an email scanner pre-clicked it — "
            + "use the password button instead.");
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-slate-950 text-slate-100 p-6">
      <div className="w-full max-w-sm">
        <h1 className="text-2xl font-semibold tracking-tight mb-1">Statement Intel</h1>
        <p className="text-sm text-slate-400 mb-8">Bank statement extraction &amp; reconciliation</p>

        <form onSubmit={signInWithPassword} className="space-y-4">
          <input
            type="email" required value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@yourdomain.com"
            className="w-full rounded-lg border border-slate-700 bg-slate-900 px-4 py-3 text-sm outline-none focus:border-emerald-600"
          />
          <input
            type="password" value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password (the one set in Supabase)"
            className="w-full rounded-lg border border-slate-700 bg-slate-900 px-4 py-3 text-sm outline-none focus:border-emerald-600"
          />
          {err && <p className="text-sm text-rose-400">{err}</p>}
          <button
            disabled={busy !== "" || !password}
            className="w-full rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-4 py-3 text-sm font-medium">
            {busy === "pw" ? "Signing in…" : "Sign in with password"}
          </button>
        </form>

        <div className="my-6 flex items-center gap-3 text-xs text-slate-600">
          <span className="h-px flex-1 bg-slate-800"/>or<span className="h-px flex-1 bg-slate-800"/>
        </div>

        {sent ? (
          <div className="rounded-lg border border-emerald-800 bg-emerald-950/50 p-4 text-sm text-emerald-300">
            Magic link sent — {note}
          </div>
        ) : (
          <button onClick={sendMagicLink} disabled={busy !== "" || !email}
            className="w-full rounded-lg border border-slate-700 hover:bg-slate-800 px-4 py-3 text-sm text-slate-300 disabled:opacity-50">
            {busy === "magic" ? "Sending…" : "Email me a one-time magic link instead"}
          </button>
        )}
      </div>
    </main>
  );
}
