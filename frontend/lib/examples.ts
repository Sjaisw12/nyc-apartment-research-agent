// The recorded demo buildings. Record counts are what the demo report returns for each
// (evidence items across all six datasets), checked against the API on 2026-11-23.
export interface Example {
  key: string;
  address: string;
  title: string;
  neighborhood: string;
  borough: string;
  what: string;
  records: number;
  tags: string[];
}

export const EXAMPLES: Example[] = [
  {
    key: "west_farms_1134",
    address: "1134 West Farms Rd, Bronx, NY 10459",
    title: "1134 West Farms Rd",
    neighborhood: "West Farms",
    borough: "Bronx",
    what: "Six-story walk-up with a long HPD history and a second building on the same tax lot.",
    records: 1311,
    tags: ["Two buildings on lot", "Open violations"],
  },
  {
    key: "acp_1845",
    address: "1845 Adam Clayton Powell Jr Blvd, Manhattan",
    title: "1845 Adam Clayton Powell Jr Blvd",
    neighborhood: "Central Harlem",
    borough: "Manhattan",
    what: "Condominium: records are searched across the condo billing lot and its base lot.",
    records: 2821,
    tags: ["Condo lot family", "Partial 311 data"],
  },
  {
    key: "west_farms_1217",
    address: "1217 West Farms Road, Bronx",
    title: "1217 West Farms Rd",
    neighborhood: "West Farms",
    borough: "Bronx",
    what: "Only the tax lot can be verified, so Stoop withholds building-level claims.",
    records: 106,
    tags: ["Tax-lot only", "Abstains on BIN"],
  },
  {
    key: "west_17_220",
    address: "220 West 17th Street, Manhattan",
    title: "220 West 17th St",
    neighborhood: "Chelsea",
    borough: "Manhattan",
    what: "Small converted building that shares its tax lot with other structures.",
    records: 408,
    tags: ["Shared lot", "DOB complaints"],
  },
  {
    key: "fifth_ave_350",
    address: "350 5th Avenue, New York, NY 10118",
    title: "350 Fifth Ave",
    neighborhood: "Midtown",
    borough: "Manhattan",
    what: "Commercial landmark: no residential units on HPD's record, heavy DOB and 311 activity.",
    records: 874,
    tags: ["Commercial", "High 311 volume"],
  },
];

export function investigateHref(address: string) {
  return `/investigate?address=${encodeURIComponent(address)}`;
}
