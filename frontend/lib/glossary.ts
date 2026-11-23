/** Plain-English explanations of agency terms. Used by <Term> tooltips and the How it works page. */
export const GLOSSARY: Record<string, { term: string; short: string }> = {
  HPD: { term: "HPD", short: "NYC's Department of Housing Preservation and Development, the city's housing maintenance agency." },
  DOB: { term: "DOB", short: "NYC's Department of Buildings, the building safety and construction department." },
  ECB: {
    term: "ECB summons",
    short: "A violation-related charge issued by an agency and heard by the city's tribunal. Not necessarily a final finding.",
  },
  "311": { term: "311", short: "The city's service-request line. A 311 request is a report from the public, not a finding." },
  BIN: { term: "BIN", short: "Building Identification Number: a unique identifier for one building." },
  BBL: {
    term: "BBL",
    short: "Borough-Block-Lot: the identifier for a tax lot, which can contain more than one building.",
  },
  "Class C": { term: "Class C", short: "HPD's 'immediately hazardous' violation category, its most serious class." },
  "Class B": { term: "Class B", short: "HPD's 'hazardous' violation category." },
  "Class A": { term: "Class A", short: "HPD's 'non-hazardous' violation category." },
  "Open violation": {
    term: "Open violation",
    short: "HPD has not marked the violation corrected. It does not prove the condition exists today.",
  },
  Closed: {
    term: "Closed",
    short: "Marked closed in the agency's records. This does not necessarily prove the condition was repaired.",
  },
  Complaint: { term: "Complaint", short: "A report by a resident or someone else. Not proof of a problem." },
  Order: {
    term: "Order (historical)",
    short: "An order such as a vacate or stop-work recorded on a past date. The data does not show whether it applies today.",
  },
  "Tax lot": { term: "Tax lot", short: "A parcel of land identified by its BBL. One lot can hold several buildings." },
};
