import type { Metadata } from "next";
import { Draft, ProsePage } from "@/components/pages/Prose";

export const metadata: Metadata = { title: "Terms and disclaimer · Stoop" };

export default function Terms() {
  return (
    <ProsePage eyebrow="Terms" title="Terms of use and disclaimer">
      <Draft>
        Draft for review. These terms are a starting point written for a portfolio project. They must be reviewed by a licensed
        attorney before Stoop is offered to the public. A disclaimer does not remove legal responsibility.
      </Draft>
      <h2>What Stoop is</h2>
      <p>
        Stoop is an independent research tool that summarizes public records published by the City of New York through NYC Open
        Data and NYC Planning. It is not affiliated with, endorsed by or operated by the City of New York or any of its
        agencies.
      </p>
      <h2>What Stoop is not</h2>
      <ul>
        <li>Not a building inspection, safety assessment or certification of habitability.</li>
        <li>Not legal advice.</li>
        <li>Not a prediction of future building conditions.</li>
        <li>Not a tenant-screening or consumer-reporting service. Do not use Stoop to make decisions about any individual, including tenants, applicants or landlords as people.</li>
        <li>Not a substitute for checking with the agencies (HPD Online, DOB&apos;s Building Information System) and the landlord.</li>
      </ul>
      <h2>Accuracy</h2>
      <p>
        Public data can be delayed, incomplete or wrong, and datasets change without notice. Stoop reports what the records say,
        labels uncertainty, and shows the date and completeness of each source, but does not guarantee accuracy or completeness.
        &quot;Open&quot; and &quot;closed&quot; describe an agency&apos;s record, not the physical condition of a building today.
      </p>
      <h2>Acceptable use</h2>
      <ul>
        <li>Use Stoop for researching buildings, for example before renting.</li>
        <li>Do not use it to harass, profile or discriminate against anyone, or to make housing decisions based on protected characteristics.</li>
        <li>Do not overload the service; requests are rate-limited.</li>
      </ul>
      <h2>Data licence</h2>
      <p>
        City data is used under the NYC Open Data Terms of Use. Stoop&apos;s source code is MIT-licensed. The City of New York
        provides its data as-is and is not responsible for Stoop&apos;s use of it.
      </p>
      <h2>Corrections</h2>
      <p>
        If something looks wrong, please <a href="/corrections">report it</a>. Stoop can fix how it reads or labels records, but it
        cannot change the city&apos;s data; for that, contact the agency.
      </p>
    </ProsePage>
  );
}
