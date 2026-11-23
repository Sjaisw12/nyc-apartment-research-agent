import type { Metadata } from "next";
import { ProsePage } from "@/components/pages/Prose";

export const metadata: Metadata = { title: "Attention methodology · Stoop" };

const LABELS: [string, string, string][] = [
  [
    "Priority follow-up",
    "At least one priority trigger in checked data: an open HPD Class C violation (HPD's 'immediately hazardous' class), or a DOB/ECB summons that DOB still lists as unresolved and marks hazardous or Class 1.",
    "Shown even if another source could not be checked, together with a 'Limited' coverage note.",
  ],
  [
    "Some items to check",
    "No priority trigger, but at least one of: open Class A or B HPD violations, HPD complaints in the last 12 months, DOB complaints in the last 3 years, unresolved summonses, or order events in DOB's data (always described as historical).",
    "Only used when every key source could be checked.",
  ],
  [
    "No priority items found in checked data",
    "None of the triggers above, and every key source was checked completely.",
    "Not a safety statement. Older or closed records may still exist, and the data can lag or be wrong.",
  ],
  [
    "Insufficient information",
    "Only the tax lot (not the building) was verified; or a key source could not be checked and nothing else was found; or a key source was only partly retrieved and nothing else was found.",
    "Says nothing good or bad about the building.",
  ],
];

export default function Methodology() {
  return (
    <ProsePage
      eyebrow="Methodology"
      title="How the Building Attention Summary is decided"
      intro="The label is computed by fixed, documented rules over the cited records. It is not a score, a probability, a safety grade or a recommendation to rent or not to rent."
    >
      <h2>The four labels</h2>
      <p>Rules are applied in this order; the first that matches wins.</p>
      <dl>
        {LABELS.map(([l, rule, note]) => (
          <div key={l}>
            <dt>{l}</dt>
            <dd>
              {rule} <span className="mt-1 block text-sm text-ink-faint">{note}</span>
            </dd>
          </div>
        ))}
      </dl>
      <p>
        <strong>Key sources</strong> are HPD violations, HPD complaints, DOB complaints and DOB/ECB summonses. 311 requests and HPD
        building records are shown in the report but do not drive the label.
      </p>

      <h2>Why these triggers</h2>
      <ul>
        <li>
          Priority triggers use only the agencies&apos; own hazard classes (HPD Class C, DOB hazardous / Class 1). Stoop does not
          invent weights, scores or thresholds, and a high number of records alone never raises the label.
        </li>
        <li>Complaints are reports from residents or others. They can raise &quot;Some items to check&quot;, never &quot;Priority&quot;.</li>
        <li>
          Orders (vacate, stop-work and similar) appear in the data as past events. Stoop never presents them as currently in
          force; it asks you to confirm their status with the agency.
        </li>
        <li>
          Missing or partial data never produces reassurance. If a key source could not be checked, Stoop says the information is
          insufficient rather than &quot;nothing found&quot;.
        </li>
      </ul>

      <h2>Which records count</h2>
      <ul>
        <li>Only records attributed to the requested building (by its BIN) count. Records for other buildings on the same tax lot are listed separately.</li>
        <li>For a tax-lot-only match, records cannot be tied to one building, so the label is always &quot;Insufficient information&quot;.</li>
        <li>&quot;Open&quot; means the agency has not marked a record closed or corrected. It does not prove the condition exists today.</li>
      </ul>

      <h2>Data coverage</h2>
      <p>
        Next to the label, a separate <strong>Data coverage</strong> indicator says whether each key source was checked completely,
        partly (counts are lower bounds) or not at all, and how recent the data is. This keeps &quot;documented issues&quot; apart from
        &quot;missing evidence&quot;.
      </p>

      <h2>How numbers are verified</h2>
      <p>
        Every number in the summary is a count over the report&apos;s cited records. Before the report is shown, the verification
        step recounts each one from the evidence, checks that cited records belong to the right building, rejects any number in
        the text that is not backed by a count, and scans the wording for prohibited claims (for example &quot;safe&quot;,
        &quot;dangerous&quot;, &quot;you should not rent&quot;, or an order described as currently in effect).
      </p>

      <h2>What is never used</h2>
      <p>
        The label uses building records only. It does not use neighbourhood demographics, race, ethnicity, religion,
        disability, family status, income or any other characteristic of residents, owners or applicants, or any proxy for
        them. Stoop does not rank landlords or tenants and is not a tenant-screening or consumer-reporting service.
      </p>

      <h2>Known limitations</h2>
      <ul>
        <li>NYC Open Data can lag the agencies&apos; own systems, and datasets can change. In November 2026 the DOB complaints dataset was reloaded without recent complaints; Stoop flags this as partial coverage.</li>
        <li>Unit and apartment numbers are not used; reports are building-level.</li>
        <li>Records the city has not published (for example court cases or DOB NOW filings) are not covered.</li>
      </ul>
    </ProsePage>
  );
}
