import {
  AlertTriangle,
  Ban,
  BadgeCheck,
  CircleHelp,
  ExternalLink as ExternalIcon,
  FileText,
  Gavel,
  History,
  MapPin,
  MessageSquareWarning,
  type LucideIcon,
} from "lucide-react";
import type { Category, Scope } from "@/lib/types";
import { CATEGORY, SCOPE } from "@/lib/format";

export const CATEGORY_ICON: Record<Category, LucideIcon> = {
  identity: MapPin,
  verified_record: BadgeCheck,
  complaint_allegation: MessageSquareWarning,
  enforcement_action: Gavel,
  historical_event: History,
  administrative_record: FileText,
  uncertain: CircleHelp,
  unavailable: Ban,
};

export const CATEGORY_BAR: Record<Category, string> = {
  identity: "border-l-slate-400",
  verified_record: "border-l-brand",
  complaint_allegation: "border-l-amber-500",
  enforcement_action: "border-l-orange-500",
  historical_event: "border-l-violet-500",
  administrative_record: "border-l-sky-500",
  uncertain: "border-l-slate-400",
  unavailable: "border-l-rose-500",
};

export const CATEGORY_DOT: Record<Category, string> = {
  identity: "bg-slate-400",
  verified_record: "bg-brand",
  complaint_allegation: "bg-amber-500",
  enforcement_action: "bg-orange-500",
  historical_event: "bg-violet-500",
  administrative_record: "bg-sky-500",
  uncertain: "bg-slate-400",
  unavailable: "bg-rose-500",
};

export function CategoryChip({ category, withIcon = false }: { category: Category; withIcon?: boolean }) {
  const c = CATEGORY[category];
  const Icon = CATEGORY_ICON[category];
  return (
    <span className={`chip ${c.cls}`} title={c.help} data-category={category}>
      {withIcon && <Icon className="h-3 w-3" aria-hidden />}
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
      className={`chip ${warn ? "bg-amber-50 text-amber-900 ring-amber-300" : "bg-white text-ink-soft ring-line"}`}
      data-scope={scope}
    >
      {warn && <AlertTriangle className="h-3 w-3" aria-hidden />}
      {label}
    </span>
  );
}

export function SectionHeading({
  id,
  title,
  intro,
  eyebrow,
  right,
}: {
  id: string;
  title: string;
  intro?: string;
  eyebrow?: string;
  right?: React.ReactNode;
}) {
  return (
    <div id={id} className="flex scroll-mt-32 flex-col gap-3 sm:flex-row sm:items-end sm:justify-between lg:scroll-mt-24">
      <div className="min-w-0 max-w-3xl">
        {eyebrow && <p className="eyebrow mb-1.5">{eyebrow}</p>}
        <h2 className="font-display text-[1.7rem] font-semibold leading-tight tracking-tight sm:text-3xl">{title}</h2>
        {intro && <p className="mt-2 text-[15px] leading-relaxed text-ink-soft">{intro}</p>}
      </div>
      {right}
    </div>
  );
}

export function ExternalLink({
  href,
  children,
  className = "",
}: {
  href: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className={`inline-flex items-center gap-1 font-medium text-brand underline decoration-brand/30 underline-offset-2 hover:decoration-brand ${className}`}
    >
      {children}
      <ExternalIcon className="h-3 w-3 shrink-0" aria-hidden />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  );
}
