/** Shared layout for the trust pages: a navy title band and a readable text column. */
export function ProsePage({
  eyebrow,
  title,
  intro,
  children,
  updated = "November 23, 2026",
}: {
  eyebrow: string;
  title: string;
  intro?: string;
  children: React.ReactNode;
  updated?: string;
}) {
  return (
    <div>
      <section className="bg-navy text-white">
        <div className="container-site py-14 sm:py-16">
          <p className="eyebrow text-accent">{eyebrow}</p>
          <h1 className="mt-3 max-w-3xl font-display text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">{title}</h1>
          {intro && <p className="mt-4 max-w-2xl text-lg leading-relaxed text-slate-300">{intro}</p>}
          <p className="mt-6 text-xs text-slate-400">Last updated {updated}</p>
        </div>
      </section>
      <div className="container-site py-12 sm:py-16">
        <div className="prose-stoop mx-auto max-w-[72ch]">{children}</div>
      </div>
    </div>
  );
}

export function Draft({ children }: { children: React.ReactNode }) {
  return (
    <p className="not-prose rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm leading-relaxed text-amber-900">
      {children}
    </p>
  );
}
