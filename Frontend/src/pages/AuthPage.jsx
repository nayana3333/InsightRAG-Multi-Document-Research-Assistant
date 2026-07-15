import { useState } from "react";
import BrandMark from "../components/BrandMark";
import useAuth from "../useAuth";

const capabilities = [
  ["Hybrid retrieval", "Semantic meaning and exact evidence, ranked together."],
  ["Page-level traceability", "Every grounded answer points back to its source."],
  ["Private workspaces", "Documents and conversations stay isolated by account."],
];

export default function AuthPage() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const values = { email: form.email, password: form.password };
      if (mode === "register") values.name = form.name;
      await (mode === "register" ? register(values) : login(values));
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-[100dvh] bg-[#080b10] text-white lg:grid lg:grid-cols-[1.08fr_0.92fr]">
      <section className="relative hidden overflow-hidden border-r border-white/[0.07] px-12 py-10 lg:flex lg:flex-col xl:px-20 xl:py-14">
        <div className="auth-orbit auth-orbit-one" />
        <div className="auth-orbit auth-orbit-two" />
        <BrandMark />
        <div className="relative my-auto max-w-xl py-16">
          <p className="mb-5 text-xs font-semibold uppercase tracking-[0.24em] text-blue-300/80">Research without the black box</p>
          <h1 className="max-w-lg text-5xl font-semibold leading-[1.05] tracking-[-0.045em] text-[#f4f7f5] xl:text-6xl">
            Trace every answer back to the page.
          </h1>
          <p className="mt-7 max-w-lg text-base leading-7 text-gray-400">
            InsightRAG turns scattered PDFs into a connected evidence workspace—so conclusions stay useful, inspectable and defensible.
          </p>
          <div className="mt-12 grid gap-3">
            {capabilities.map(([title, description], index) => (
              <div key={title} className="group flex gap-4 rounded-2xl border border-white/[0.06] bg-white/[0.025] p-4 backdrop-blur-sm transition hover:border-blue-300/20 hover:bg-white/[0.04]">
                <span className="mt-0.5 font-mono text-xs text-blue-300/70">0{index + 1}</span>
                <div><h2 className="text-sm font-medium text-gray-100">{title}</h2><p className="mt-1 text-xs leading-5 text-gray-500">{description}</p></div>
              </div>
            ))}
          </div>
        </div>
        <p className="relative text-xs text-gray-600">Built for evidence-heavy decisions.</p>
      </section>

      <main className="grid min-h-[100dvh] place-items-center px-5 py-10 sm:px-10">
        <div className="w-full max-w-md">
          <div className="mb-10 text-xl font-semibold tracking-[-0.03em] text-white lg:hidden">InsightRAG</div>
          <div className="mb-8">
            <h1 className="text-3xl font-semibold tracking-[-0.035em] text-[#f4f7f5]">
              {mode === "register" ? "Start connecting the dots." : "Pick up where you left off."}
            </h1>
            <p className="mt-3 text-sm leading-6 text-gray-500">
              {mode === "register" ? "One account. Multiple document collections. Every answer cited." : "Sign in to your documents, conversations and saved evidence."}
            </p>
          </div>

          <form onSubmit={submit} className="space-y-5">
            {mode === "register" && (
              <label className="block text-xs font-medium uppercase tracking-[0.12em] text-gray-500">Name
                <input required minLength={2} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} className="insight-input" placeholder="How should we address you?" />
              </label>
            )}
            <label className="block text-xs font-medium uppercase tracking-[0.12em] text-gray-500">Email
              <input required type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} className="insight-input" placeholder="you@example.com" />
            </label>
            <label className="block text-xs font-medium uppercase tracking-[0.12em] text-gray-500">Password
              <input required type="password" minLength={8} value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} className="insight-input" placeholder="At least 8 characters" />
            </label>

            {error && <div role="alert" className="rounded-xl border border-red-400/20 bg-red-950/30 p-3 text-sm text-red-200">{error}</div>}
            <button disabled={submitting} className="group flex w-full items-center justify-between rounded-xl bg-blue-600 px-5 py-3.5 text-sm font-semibold text-white transition hover:bg-blue-500 disabled:opacity-50">
              <span>{submitting ? "Securing workspace…" : mode === "register" ? "Create workspace" : "Open workspace"}</span>
              <span aria-hidden="true" className="transition group-hover:translate-x-1">→</span>
            </button>
          </form>

          <button type="button" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }} className="mt-6 text-left text-sm text-gray-500 transition hover:text-blue-300">
            {mode === "login" ? "No workspace yet? Create one" : "Already have a workspace? Sign in"}
          </button>
        </div>
      </main>
    </div>
  );
}
