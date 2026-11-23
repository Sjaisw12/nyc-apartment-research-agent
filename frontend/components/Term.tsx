"use client";

import { useId, useState } from "react";
import { GLOSSARY } from "@/lib/glossary";

/** An agency term with a plain-English explanation on hover, focus or tap. */
export function Term({ k, children, className = "" }: { k: keyof typeof GLOSSARY | string; children?: React.ReactNode; className?: string }) {
  const g = GLOSSARY[k];
  const [open, setOpen] = useState(false);
  const id = useId();
  if (!g) return <>{children ?? k}</>;
  return (
    <span className="relative inline-block">
      <button
        type="button"
        className={`cursor-help border-b border-dotted border-current/60 font-inherit leading-tight ${className}`}
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
      >
        {children ?? g.term}
      </button>
      {open && (
        <span
          id={id}
          role="tooltip"
          className="absolute bottom-full left-1/2 z-50 mb-2 w-64 -translate-x-1/2 rounded-xl bg-navy px-3.5 py-2.5 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-white shadow-lift"
        >
          <span className="block font-semibold text-accent">{g.term}</span>
          {g.short}
        </span>
      )}
    </span>
  );
}
