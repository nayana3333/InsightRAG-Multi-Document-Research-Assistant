import { Link } from "react-router-dom";
import BrandMark from "../components/BrandMark";

export default function Landing() {
  return (
    <div className="relative min-h-[100dvh] overflow-hidden bg-[#080b10] px-5 text-white sm:px-8">
      <div className="auth-orbit auth-orbit-one" />
      <div className="auth-orbit auth-orbit-two" />
      <header className="relative mx-auto flex max-w-6xl items-center justify-between border-b border-white/[0.06] py-5">
        <BrandMark compact />
        <Link to="/MainApp" className="rounded-full border border-white/10 px-4 py-2 text-xs font-medium text-gray-300 transition hover:border-blue-300/30 hover:text-blue-200">
          Open workspace
        </Link>
      </header>

      <main className="relative mx-auto grid min-h-[calc(100dvh-82px)] max-w-6xl items-center gap-16 py-16 lg:grid-cols-[1.05fr_0.95fr]">
        <section>
          <p className="mb-6 text-xs font-semibold uppercase tracking-[0.25em] text-blue-300/70">Multi-document intelligence</p>
          <h1 className="max-w-3xl text-5xl font-semibold leading-[0.98] tracking-[-0.055em] text-[#f4f7f5] sm:text-6xl lg:text-7xl">
            Answers you can <span className="text-blue-300">trace.</span>
          </h1>
          <p className="mt-8 max-w-xl text-base leading-7 text-gray-400 sm:text-lg">
            Search across a body of PDFs, follow every claim to its page and keep the reasoning connected to real evidence.
          </p>
          <div className="mt-10 flex flex-wrap items-center gap-4">
            <Link to="/MainApp" className="group inline-flex items-center gap-8 rounded-xl bg-blue-600 px-5 py-3.5 text-sm font-semibold text-white transition hover:bg-blue-500">
              Build an evidence workspace <span className="transition group-hover:translate-x-1">→</span>
            </Link>
            <span className="text-xs text-gray-600">PDFs stay private to your account</span>
          </div>
        </section>

        <section className="relative mx-auto w-full max-w-md" aria-label="InsightRAG workflow preview">
          <div className="absolute -inset-8 rounded-full bg-blue-300/[0.025] blur-3xl" />
          <div className="relative overflow-hidden rounded-3xl border border-white/[0.08] bg-[#0e1218]/90 p-5 shadow-2xl backdrop-blur-xl">
            <div className="flex items-center justify-between border-b border-white/[0.06] pb-4">
              <span className="text-xs text-gray-500">Evidence trail</span>
              <span className="rounded-full bg-blue-300/10 px-2.5 py-1 font-mono text-[10px] text-blue-300">4 sources ranked</span>
            </div>
            <div className="py-5 text-sm leading-6 text-gray-300">
              The strongest evidence indicates that the hybrid model improved recall while preserving precision.
              <span className="ml-1 rounded bg-violet-300/10 px-1.5 py-0.5 text-xs text-violet-200">[Source 2]</span>
            </div>
            <div className="space-y-2.5">
              <div className="rounded-xl border border-blue-300/15 bg-blue-300/[0.035] p-3">
                <div className="flex justify-between text-[11px] text-blue-200"><span>evaluation-report.pdf · p. 14</span><span>94%</span></div>
                <p className="mt-2 text-xs leading-5 text-gray-500">Hybrid retrieval improved the measured recall across exact identifiers and semantic queries…</p>
              </div>
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
                <div className="flex justify-between text-[11px] text-gray-400"><span>architecture-notes.pdf · p. 6</span><span>87%</span></div>
                <p className="mt-2 text-xs leading-5 text-gray-600">Dense candidates are fused with lexical matches before reranking…</p>
              </div>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}
