import Link from "next/link";
import SearchBox from "@/components/SearchBox";
import { FALLBACK_EXAMPLES } from "@/lib/api";

const steps = [
  {
    title: "Confirm the building",
    body: "Your address is matched to a tax lot and building ID using two official city sources. If more than one building fits, you choose. If none fits reliably, we say so instead of guessing.",
  },
  {
    title: "Gather the official record",
    body: "HPD violations and complaints, DOB complaints, ECB summonses, 311 requests, and HPD building and registration records, each with its data date.",
  },
  {
    title: "Read a cited report",
    body: "Every statement links to the records behind it. Findings, complaints, charges and past orders are labelled differently, and missing data is shown as missing.",
  },
];

const legend = [
  ["Verified record", "An agency’s own finding, such as an HPD violation after an inspection.", "bg-emerald-500"],
  ["Complaint · allegation", "What a tenant or neighbor reported. Useful, but not proof.", "bg-amber-500"],
  ["Historical event", "An order on a past date. The data does not show whether it applies today.", "bg-violet-500"],
  ["Unavailable or uncertain", "What could not be checked. Never treated as “no problems”.", "bg-rose-500"],
];

export default function Home() {
  return (
    <div>
      <section className="relative overflow-hidden">
        <div className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_top,_#e6f0ef,_transparent_60%)]" />
        <div className="mx-auto max-w-4xl px-4 pb-16 pt-14 text-center sm:px-6 sm:pt-20">
          <p className="mb-4 inline-flex items-center gap-2 rounded-full bg-white px-3 py-1 text-xs font-medium text-brand ring-1 ring-brand/20">
            Official NYC records · cited · free
          </p>
          <h1 className="font-display text-4xl font-semibold leading-tight tracking-tight sm:text-6xl">
            Know the building <span className="text-brand">before you sign</span> the lease.
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg text-ink-soft">
            Enter an NYC address. Stoop finds the exact building, pulls its housing violations,
            complaints and enforcement history, and writes a report where every claim links to the
            city record behind it.
          </p>
          <div className="mx-auto mt-8 max-w-2xl">
            <SearchBox />
          </div>
          <div className="mx-auto mt-5 max-w-2xl">
            <p className="mb-2 text-sm text-ink-faint">Try an example from the demo dataset</p>
            <div className="flex flex-wrap justify-center gap-2">
              {FALLBACK_EXAMPLES.map((ex) => (
                <Link
                  key={ex.key}
                  href={`/investigate?address=${encodeURIComponent(ex.address)}`}
                  className="group rounded-xl border border-stone-200 bg-white px-3 py-2 text-left text-sm shadow-sm transition hover:border-brand/40 hover:shadow-card"
                  data-testid="example-link"
                >
                  <span className="block font-medium text-ink group-hover:text-brand">{ex.address}</span>
                  <span className="block text-xs text-ink-faint">{ex.why}</span>
                </Link>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section id="how" className="mx-auto max-w-6xl px-4 sm:px-6">
        <div className="grid gap-4 md:grid-cols-3">
          {steps.map((s, i) => (
            <div key={s.title} className="card p-6">
              <div className="mb-3 grid h-8 w-8 place-items-center rounded-full bg-brand-light font-semibold text-brand">
                {i + 1}
              </div>
              <h2 className="font-display text-xl font-semibold">{s.title}</h2>
              <p className="mt-2 text-sm leading-relaxed text-ink-soft">{s.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="mx-auto mt-14 max-w-6xl px-4 sm:px-6">
        <div className="card grid gap-8 p-6 sm:p-8 md:grid-cols-[1fr_1.2fr]">
          <div>
            <h2 className="font-display text-2xl font-semibold">Honest about what the records say</h2>
            <p className="mt-3 text-sm leading-relaxed text-ink-soft">
              A complaint is not a violation, a summons is not a ruling, and a vacate order from
              2019 is not proof of anything today. Stoop keeps them apart, never mixes in records
              from a neighbouring building on the same lot, and tells you when a source could not
              be checked. Optional AI explanations are only shown if every sentence passes a check
              against the cited records.
            </p>
          </div>
          <ul className="grid gap-3 sm:grid-cols-2">
            {legend.map(([t, d, c]) => (
              <li key={t} className="rounded-xl border border-stone-200 p-4">
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <span className={`h-2.5 w-2.5 rounded-full ${c}`} />
                  {t}
                </div>
                <p className="mt-1 text-sm text-ink-soft">{d}</p>
              </li>
            ))}
          </ul>
        </div>
      </section>
    </div>
  );
}
