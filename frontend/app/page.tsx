import Link from "next/link";
import {
  AlertOctagon,
  ArrowRight,
  CircleHelp,
  ListChecks,
  SearchCheck,
  BadgeCheck,
  Database,
  FileSearch,
  Layers,
  MapPin,
  ScanSearch,
  ShieldCheck,
} from "lucide-react";
import SearchBox from "@/components/SearchBox";
import { ReportPreview } from "@/components/home/ReportPreview";
import { DemoNotice } from "@/components/home/DemoNotice";
import { EXAMPLES, investigateHref } from "@/lib/examples";

const STEPS = [
  {
    icon: MapPin,
    title: "Pin down the exact building",
    body: "The address is matched to a tax lot (BBL) and building ID (BIN) in two city sources. If two buildings fit, you choose. If none fits reliably, Stoop says so instead of guessing.",
  },
  {
    icon: Database,
    title: "Pull the official record",
    body: "Six NYC Open Data datasets: HPD violations and complaints, DOB complaints, ECB summonses, 311 requests, and HPD building and registration records.",
  },
  {
    icon: Layers,
    title: "Separate what is known",
    body: "Agency findings, tenant allegations, enforcement actions, past orders and missing data each get their own label. Records for a neighboring building stay separate.",
  },
  {
    icon: BadgeCheck,
    title: "Check every claim",
    body: "Each count is recomputed from the cited records before the report is shown. The optional AI explanation is dropped if any sentence fails that check.",
  },
];

const LABELS = [
  ["Verified record", "An agency's own finding, such as an HPD violation issued after an inspection.", "bg-brand", "text-brand-dark"],
  ["Complaint · allegation", "What a tenant or neighbor reported. Useful context, but not proof.", "bg-amber-500", "text-amber-800"],
  ["Enforcement action", "A summons or charge an agency issued. Not by itself a ruling.", "bg-orange-500", "text-orange-800"],
  ["Historical event", "An order recorded on a past date. The data does not show whether it applies today.", "bg-violet-500", "text-violet-800"],
  ["Uncertain", "The data is partial or conflicting, so Stoop does not settle the question.", "bg-slate-400", "text-slate-700"],
  ["Unavailable", "A source could not be checked. Never treated as “no problems”.", "bg-rose-500", "text-rose-800"],
];

const ATTN = [
  ["Priority follow-up", "Records in an agency's most serious category, such as an open HPD Class C violation, that deserve prompt verification.", "bg-amber-100 text-amber-900 ring-amber-300", AlertOctagon],
  ["Some items to check", "Open lower-class violations, recent complaints or past orders worth discussing with the landlord.", "bg-brand-light text-brand-dark ring-brand/30", ListChecks],
  ["No priority items found", "None of the defined triggers in sources that were checked completely. Not a statement that the building is fine.", "bg-slate-100 text-ink ring-line-strong", SearchCheck],
  ["Insufficient information", "A key source could not be checked, or only the tax lot was identified. Stoop says so rather than guess.", "bg-slate-100 text-slate-700 ring-slate-300", CircleHelp],
];

const SOURCES = [
  ["HPD", "Housing Maintenance Code Violations", "wvxf-dwi5", "Inspector-issued violations, Class A to C"],
  ["HPD", "Complaints and Problems", "ygpa-z7cr", "Tenant complaints, including heat and hot water"],
  ["HPD", "Buildings and Registrations", "kj4p-ruqc · tesw-yqqr", "Legal units, stories, registration on file"],
  ["DOB", "DOB Complaints Received", "eabe-havv", "Building complaints and DOB's disposition"],
  ["DOB / ECB", "DOB ECB Violations", "6bgk-3dad", "Summonses, hearing outcomes and balances"],
  ["311", "311 Service Requests", "erm2-nwe9", "Non-HPD requests on the tax lot since 2020"],
];

export default function Home() {
  return (
    <div>
      {/* Hero */}
      <section className="relative overflow-hidden bg-navy text-white">
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.08] [background-image:linear-gradient(#ffffff_1px,transparent_1px),linear-gradient(90deg,#ffffff_1px,transparent_1px)] [background-size:56px_56px] [mask-image:radial-gradient(ellipse_at_25%_30%,black,transparent_75%)]"
          aria-hidden
        />
        <div className="pointer-events-none absolute -right-40 -top-40 h-[520px] w-[520px] rounded-full bg-brand/25 blur-[120px]" aria-hidden />
        <div className="container-site relative grid items-center gap-12 py-14 sm:py-20 lg:grid-cols-[1.08fr_1fr] lg:gap-16 lg:py-24">
          <div id="search" className="scroll-mt-24">
            <p className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1 text-xs font-semibold text-accent">
              <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
              Public NYC records · Every number cited
            </p>
            <h1 className="mt-6 font-display text-[2.7rem] font-semibold leading-[1.04] tracking-tight sm:text-6xl lg:text-[4.2rem]">
              Know the building <span className="italic text-accent">before</span> you sign.
            </h1>
            <p className="mt-6 max-w-xl text-lg leading-relaxed text-slate-300 sm:text-xl">
              Enter an NYC address. Stoop checks the city&apos;s housing, building and 311 records and tells you, in plain
              English, what deserves a closer look and what to ask the landlord.
            </p>
            <div className="mt-8 max-w-2xl">
              <SearchBox />
              <DemoNotice className="mt-4" dark />
            </div>
            <div className="mt-6 flex flex-wrap items-center gap-2">
              <span className="mr-1 text-sm text-slate-400">Try:</span>
              {EXAMPLES.slice(0, 3).map((e) => (
                <Link
                  key={e.key}
                  href={investigateHref(e.address)}
                  className="rounded-full border border-white/15 bg-white/5 px-3.5 py-1.5 text-sm font-medium text-slate-200 transition hover:border-accent/60 hover:text-white"
                  data-testid="example-link"
                >
                  {e.title}
                </Link>
              ))}
            </div>
          </div>
          <ReportPreview />
        </div>
      </section>

      {/* What you get */}
      <section className="border-b border-line bg-white">
        <div className="container-site py-16 sm:py-20">
          <div className="max-w-2xl">
            <p className="eyebrow text-brand">Understand a building in 30 seconds</p>
            <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight sm:text-4xl">
              One clear label, the evidence behind it, and what to ask next
            </h2>
            <p className="mt-3 text-lg leading-relaxed text-ink-soft">
              The Building Attention Summary uses fixed, published rules. It is not a safety grade, a score or a
              prediction, and it never tells you whether to rent.
            </p>
          </div>
          <ul className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {ATTN.map(([label, help, cls, Icon]) => {
              const I = Icon as typeof ShieldCheck;
              return (
                <li key={label as string} className="card p-5">
                  <span className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-semibold ring-1 ring-inset ${cls as string}`}>
                    <I className="h-3.5 w-3.5" aria-hidden />
                    {label as string}
                  </span>
                  <p className="mt-3 text-sm leading-relaxed text-ink-soft">{help as string}</p>
                </li>
              );
            })}
          </ul>
          <Link href="/methodology" className="link mt-6 inline-block text-sm">
            How the label is decided
          </Link>
        </div>
      </section>

      {/* Examples */}
      <section id="examples" className="container-site scroll-mt-20 py-20 sm:py-24">
        <div className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
          <div className="max-w-2xl">
            <p className="eyebrow text-brand">Example buildings</p>
            <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight sm:text-4xl">
              Five real buildings, five different stories
            </h2>
            <p className="mt-3 text-lg text-ink-soft">
              Each example was picked to show a hard case: shared tax lots, condominium lots, missing
              building IDs and partial data.
            </p>
          </div>
        </div>
        <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {EXAMPLES.map((e, i) => (
            <Link
              key={e.key}
              href={investigateHref(e.address)}
              className={`card group flex flex-col p-6 transition hover:-translate-y-0.5 hover:border-brand/30 hover:shadow-lift ${
                i === 0 ? "lg:row-span-2 lg:bg-navy lg:text-white" : ""
              }`}
              data-testid="example-card"
            >
              <div className="flex items-center justify-between gap-3">
                <span className={`eyebrow ${i === 0 ? "lg:text-white/60" : ""}`}>
                  {e.neighborhood} · {e.borough}
                </span>
                <ArrowRight
                  className={`h-4 w-4 text-ink-faint transition group-hover:translate-x-0.5 group-hover:text-brand ${
                    i === 0 ? "lg:text-white/60 lg:group-hover:text-white" : ""
                  }`}
                  aria-hidden
                />
              </div>
              <h3 className={`mt-3 font-display font-semibold leading-tight ${i === 0 ? "text-2xl lg:text-4xl" : "text-2xl"}`}>
                {e.title}
              </h3>
              <p className={`mt-3 text-[15px] leading-relaxed ${i === 0 ? "text-ink-soft lg:text-white/75" : "text-ink-soft"}`}>
                {e.what}
              </p>
              {i === 0 && (
                <dl className="mt-6 hidden grid-cols-2 gap-3 lg:grid">
                  {[
                    ["111", "open HPD violations"],
                    ["40", "open Class C"],
                    ["29", "HPD complaints, last 12 mo"],
                    ["2", "buildings on the tax lot"],
                  ].map(([v, l]) => (
                    <div key={l} className="rounded-xl bg-white/[0.06] px-4 py-3 ring-1 ring-white/10">
                      <dt className="sr-only">{l}</dt>
                      <dd className="font-display text-3xl font-semibold text-white">{v}</dd>
                      <dd className="mt-0.5 text-xs text-white/60">{l}</dd>
                    </div>
                  ))}
                </dl>
              )}
              <div className="mt-auto pt-6">
                <div className="flex flex-wrap gap-1.5">
                  {e.tags.map((t) => (
                    <span
                      key={t}
                      className={`chip ${
                        i === 0
                          ? "bg-paper text-ink-soft ring-line lg:bg-white/10 lg:text-white lg:ring-white/20"
                          : "bg-paper text-ink-soft ring-line"
                      }`}
                    >
                      {t}
                    </span>
                  ))}
                </div>
                <p className={`mt-4 text-sm ${i === 0 ? "text-ink-faint lg:text-white/60" : "text-ink-faint"}`}>
                  <span className={`font-semibold ${i === 0 ? "text-ink lg:text-white" : "text-ink"}`}>
                    {e.records.toLocaleString()}
                  </span>{" "}
                  cited records in the report
                </p>
              </div>
            </Link>
          ))}
        </div>
      </section>

      {/* How it works */}
      <section id="how" className="scroll-mt-20 border-y border-line bg-white py-20 sm:py-24">
        <div className="container-site">
          <div className="max-w-2xl">
            <p className="eyebrow text-brand">How it works</p>
            <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight sm:text-4xl">
              From an address to a report you can check
            </h2>
          </div>
          <ol className="mt-12 grid gap-8 md:grid-cols-2 lg:grid-cols-4 lg:gap-6">
            {STEPS.map((s, i) => (
              <li key={s.title} className="relative">
                <div className="flex items-center gap-3">
                  <span className="grid h-11 w-11 place-items-center rounded-xl bg-navy text-accent">
                    <s.icon className="h-5 w-5" aria-hidden />
                  </span>
                  <span className="font-mono text-xs text-ink-faint">0{i + 1}</span>
                  {i < STEPS.length - 1 && <span className="hidden h-px flex-1 bg-line lg:block" aria-hidden />}
                </div>
                <h3 className="mt-5 text-lg font-semibold">{s.title}</h3>
                <p className="mt-2 text-[15px] leading-relaxed text-ink-soft">{s.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* Labels */}
      <section id="labels" className="container-site scroll-mt-20 py-20 sm:py-24">
        <div className="grid gap-12 lg:grid-cols-[1fr_1.4fr] lg:gap-16">
          <div>
            <p className="eyebrow text-brand">Evidence, not verdicts</p>
            <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight sm:text-4xl">
              Honest about what the records actually say
            </h2>
            <p className="mt-4 text-lg leading-relaxed text-ink-soft">
              A complaint is not a violation, a summons is not a ruling, and a vacate order from years
              ago is not proof of anything today. Stoop keeps them apart, and never calls a building
              safe or unsafe.
            </p>
            <div className="mt-6 flex items-start gap-3 rounded-2xl border border-line bg-white p-4">
              <FileSearch className="mt-0.5 h-5 w-5 shrink-0 text-brand" aria-hidden />
              <p className="text-sm leading-relaxed text-ink-soft">
                In our evaluation on recorded buildings: <strong className="text-ink">0</strong> records
                attributed to the wrong building and <strong className="text-ink">0 of 6,533</strong>{" "}
                citations broken.
              </p>
            </div>
          </div>
          <ul className="grid gap-3 sm:grid-cols-2">
            {LABELS.map(([name, help, dot, text]) => (
              <li key={name} className="card p-5">
                <p className={`flex items-center gap-2 text-sm font-semibold ${text}`}>
                  <span className={`h-2.5 w-2.5 rounded-full ${dot}`} aria-hidden />
                  {name}
                </p>
                <p className="mt-2 text-sm leading-relaxed text-ink-soft">{help}</p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* Sources */}
      <section id="sources" className="scroll-mt-20 border-t border-line bg-white py-20 sm:py-24">
        <div className="container-site">
          <div className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
            <div className="max-w-2xl">
              <p className="eyebrow text-brand">Data sources</p>
              <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight sm:text-4xl">
                Built only on official NYC data
              </h2>
            </div>
            <p className="max-w-md text-ink-soft">
              Addresses are matched with NYC Planning&apos;s GeoSearch and Building Footprints. Records
              come from these NYC Open Data datasets.
            </p>
          </div>
          <ul className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {SOURCES.map(([agency, name, id, what]) => (
              <li key={id} className="card flex gap-4 p-5">
                <span className="grid h-11 w-16 shrink-0 place-items-center rounded-xl bg-paper px-1 text-center font-mono text-[11px] font-semibold leading-tight text-ink-soft ring-1 ring-line">
                  {agency}
                </span>
                <div className="min-w-0">
                  <p className="font-semibold leading-snug">{name}</p>
                  <p className="mt-1 text-sm text-ink-soft">{what}</p>
                  <p className="mt-2 font-mono text-xs text-ink-faint">{id}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* CTA */}
      <section className="container-site pt-20 sm:pt-24">
        <div className="relative overflow-hidden rounded-3xl bg-navy px-6 py-12 text-white sm:px-12 sm:py-16">
          <div className="absolute -right-24 -top-24 h-72 w-72 rounded-full bg-brand/30 blur-3xl" aria-hidden />
          <div className="relative grid items-center gap-8 lg:grid-cols-[1fr_1.2fr]">
            <div>
              <h2 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">
                Research a building in seconds
              </h2>
              <p className="mt-3 text-white/70">
                Start with an example, or check the report before your next viewing.
              </p>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row lg:justify-end">
              <Link href={investigateHref(EXAMPLES[0].address)} className="btn bg-white px-6 py-3.5 text-base text-navy hover:bg-brand-50">
                <ScanSearch className="h-5 w-5" aria-hidden /> Open a sample report
              </Link>
              <Link href="/#search" className="btn border border-white/25 px-6 py-3.5 text-base text-white hover:bg-white/10">
                Search an address
              </Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
