import type { Category, Scope } from "@/lib/types";
import { CATEGORY, SCOPE } from "@/lib/format";

export function CategoryChip({ category }: { category: Category }) {
  const c = CATEGORY[category];
  return (
    <span className={`chip ${c.cls}`} title={c.help} data-category={category}>
      {c.label}
    </span>
  );
}

export function ScopeChip({ scope }: { scope: Scope }) {
  const label = SCOPE[scope];
  if (!label) return null;
  const warn = scope !== "this_building";
  return (
    <span
      className={`chip ${
        warn ? "bg-yellow-50 text-yellow-900 ring-yellow-300" : "bg-white text-ink-soft ring-stone-200"
      }`}
      data-scope={scope}
    >
      {warn && (
        <svg viewBox="0 0 20 20" className="h-3 w-3" fill="currentColor" aria-hidden>
          <path d="M10 2 1 18h18L10 2zm0 5 .9 6h-1.8L10 7zm0 9a1 1 0 110-2 1 1 0 010 2z" />
        </svg>
      )}
      {label}
    </span>
  );
}

export function SectionHeading({ id, title, intro }: { id: string; title: string; intro?: string }) {
  return (
    <div id={id} className="scroll-mt-24">
      <h2 className="font-display text-2xl font-semibold tracking-tight">{title}</h2>
      {intro && <p className="mt-1 max-w-3xl text-sm leading-relaxed text-ink-soft">{intro}</p>}
    </div>
  );
}

export function ExternalLink({ href, children, className = "" }: { href: string; children: React.ReactNode; className?: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className={`inline-flex items-center gap-1 text-brand underline decoration-brand/30 underline-offset-2 hover:decoration-brand ${className}`}
    >
      {children}
      <svg viewBox="0 0 20 20" className="h-3 w-3 shrink-0" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <path d="M8 4H4v12h12v-4M11 3h6v6M17 3l-8 8" />
      </svg>
    </a>
  );
}
