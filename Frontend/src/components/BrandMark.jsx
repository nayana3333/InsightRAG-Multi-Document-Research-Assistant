export default function BrandMark({ compact = false, heading = false, showIcon = true }) {
  const Name = heading ? "h2" : "strong";
  return (
    <div className="inline-flex items-center gap-3" aria-label="InsightRAG">
      {showIcon && (
        <span className={`${compact ? "h-8 w-8" : "h-10 w-10"} relative grid shrink-0 place-items-center rounded-xl border border-white/10 bg-white/[0.04]`} aria-hidden="true">
          <svg viewBox="0 0 32 32" className={`${compact ? "h-5 w-5" : "h-6 w-6"} fill-none`}>
            <path d="M12 7H8.5A2.5 2.5 0 0 0 6 9.5v13A2.5 2.5 0 0 0 8.5 25H12" stroke="#93c5fd" strokeWidth="2.4" strokeLinecap="round" />
            <path d="M20 7h3.5A2.5 2.5 0 0 1 26 9.5v13a2.5 2.5 0 0 1-2.5 2.5H20" stroke="#a99df5" strokeWidth="2.4" strokeLinecap="round" />
            <path d="m12.5 18.5 3-6 1.7 3.4h3.3" stroke="white" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      )}
      <span className="leading-none">
        <Name className={`${compact ? "text-sm" : "text-base"} block font-semibold tracking-[-0.02em] text-white`}>InsightRAG</Name>
        {!compact && <span className="mt-1 block text-[10px] uppercase tracking-[0.22em] text-gray-500">Evidence, connected</span>}
      </span>
    </div>
  );
}
