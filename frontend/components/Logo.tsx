export function LogoMark({ className = "h-9 w-9" }: { className?: string }) {
  return (
    <span className={`grid shrink-0 place-items-center rounded-[10px] bg-navy text-white ${className}`} aria-hidden>
      <svg viewBox="0 0 24 24" className="h-[62%] w-[62%]" fill="none" strokeLinecap="round" strokeLinejoin="round">
        {/* a stoop: three steps rising to a doorway */}
        <path d="M3 20h18" stroke="#60A5FA" strokeWidth="2" />
        <path d="M5 20v-3h4v-3h4v-3h4" stroke="white" strokeWidth="2" />
        <path d="M17 11V4h3v16" stroke="white" strokeWidth="2" />
      </svg>
    </span>
  );
}
