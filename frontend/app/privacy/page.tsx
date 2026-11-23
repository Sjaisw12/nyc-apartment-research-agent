import type { Metadata } from "next";
import { Draft, ProsePage } from "@/components/pages/Prose";

export const metadata: Metadata = { title: "Privacy notice · Stoop" };

export default function Privacy() {
  return (
    <ProsePage eyebrow="Privacy" title="Privacy notice" intro="Stoop is designed to work without accounts and to collect as little as possible.">
      <Draft>
        Draft for review. This notice describes how the software behaves today. It has not been reviewed by a lawyer and must be
        reviewed before a public launch.
      </Draft>
      <h2>What you send</h2>
      <ul>
        <li><strong>Addresses you search.</strong> Sent to Stoop&apos;s server and, from there, to NYC Planning GeoSearch and NYC Open Data to look up the building.</li>
        <li><strong>Correction requests.</strong> The address, the type of problem, optional report and record references, and your description. Email addresses, phone numbers and apartment or floor details in the description are removed before it is stored.</li>
      </ul>
      <h2>What Stoop does not collect</h2>
      <ul>
        <li>No accounts, names, emails or payment details.</li>
        <li>No IP addresses are stored with correction requests. The server keeps IP addresses briefly in memory only to rate-limit abuse.</li>
        <li>No advertising or cross-site tracking cookies.</li>
      </ul>
      <h2>Reports</h2>
      <p>
        Generated reports are kept in server memory for a short time so that the Markdown and JSON downloads work, then discarded.
        Reports contain public agency records. Apartment numbers, complainant details and free text that could identify a
        household are withheld or redacted.
      </p>
      <h2>Optional AI explanation</h2>
      <p>
        If the operator enables the optional AI explanation, the building&apos;s findings (not your identity) are sent to the
        configured AI provider. It is off by default and the report states when it was used.
      </p>
      <h2>Contact and corrections</h2>
      <p>
        To report incorrect or sensitive data, use the <a href="/corrections">correction form</a>.
      </p>
    </ProsePage>
  );
}
