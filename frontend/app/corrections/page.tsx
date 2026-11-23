import type { Metadata } from "next";
import { Suspense } from "react";
import { ProsePage } from "@/components/pages/Prose";
import { CorrectionForm } from "@/components/pages/CorrectionForm";

export const metadata: Metadata = { title: "Report incorrect data · Stoop" };

export default function Corrections() {
  return (
    <ProsePage
      eyebrow="Corrections"
      title="Report incorrect data"
      intro="Spotted a record on the wrong building, an outdated status, or something that should not be shown? Tell us. No account needed."
    >
      <Suspense fallback={null}>
        <CorrectionForm />
      </Suspense>
      <h2>What happens next</h2>
      <ul>
        <li>Your request is stored with a reference number for manual review.</li>
        <li>If Stoop matched or labelled a record wrongly, the matching or labelling rules are fixed and the fix is tested.</li>
        <li>If the city&apos;s own data is wrong, Stoop cannot change it. Contact HPD or DOB directly, or call 311.</li>
      </ul>
      <p>Please do not include names, phone numbers or other personal details. Contact details and apartment numbers are removed automatically.</p>
    </ProsePage>
  );
}
