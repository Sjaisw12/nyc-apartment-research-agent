import type { Metadata } from "next";
import Link from "next/link";
import { ProsePage } from "@/components/pages/Prose";
import { GLOSSARY } from "@/lib/glossary";

export const metadata: Metadata = { title: "How it works · Stoop" };

export default function HowItWorks() {
  return (
    <ProsePage
      eyebrow="How it works"
      title="From an address to a summary you can check"
      intro="Stoop reads public NYC records about a building, keeps each kind of record in its own lane, and explains what it found in plain English. Every number links back to the records behind it."
    >
      <h2>1. Find the exact building</h2>
      <p>
        Your address is matched to a <strong>tax lot</strong> (BBL) and a <strong>building number</strong> (BIN) using NYC
        Planning&apos;s GeoSearch and Building Footprints. If two buildings fit, Stoop asks you to choose. If none fits
        reliably, it says so instead of guessing, because records for the wrong building would be misleading.
      </p>
      <h2>2. Check the official records</h2>
      <p>Stoop queries these NYC Open Data datasets directly, read-only:</p>
      <ul>
        <li>HPD housing-code violations, complaints, building records and registrations</li>
        <li>DOB complaints received and DOB/ECB summonses</li>
        <li>311 service requests for other agencies on the tax lot, since 2020</li>
      </ul>
      <p>
        Each source shows whether it was checked completely, partly, or not at all, and the date of its data. A source that
        could not be checked is never treated as &quot;no records&quot;.
      </p>
      <h2>3. Keep different kinds of records apart</h2>
      <p>
        An agency&apos;s own finding (such as an HPD violation), a complaint someone made, a summons, a past order and a gap in the
        data are labelled differently and never merged. Records filed for another building on the same lot are shown
        separately and never counted for yours.
      </p>
      <h2>4. Summarize, then re-check every number</h2>
      <p>
        The <Link href="/methodology">Building Attention Summary</Link> applies fixed, published rules to the records. Before a
        report is shown, every count is recomputed from the cited records and the wording is scanned for claims Stoop never
        makes, such as calling a building safe or dangerous. An optional AI explanation can be switched on, but it is held
        to the same checks; when it is off, every summary is an automated evidence summary written by rules.
      </p>
      <h2>What Stoop is not</h2>
      <p>
        Stoop is an independent project, not affiliated with the City of New York. It is not an inspection, a safety
        rating, legal advice, a tenant-screening service or a prediction of future conditions. Open data can be delayed,
        incomplete or wrong. Confirm anything important with the agency and the landlord.
      </p>
      <h2 id="glossary">Glossary</h2>
      <dl>
        {Object.values(GLOSSARY).map((g) => (
          <div key={g.term}>
            <dt>{g.term}</dt>
            <dd>{g.short}</dd>
          </div>
        ))}
      </dl>
    </ProsePage>
  );
}
