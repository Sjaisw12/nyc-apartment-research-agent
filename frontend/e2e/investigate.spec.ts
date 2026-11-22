import { expect, test } from "@playwright/test";

const WEST_FARMS = "1134 West Farms Rd, Bronx, NY 10459";

test("landing page links to the demo examples", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Know the building");
  await expect(page.getByTestId("example-link")).toHaveCount(3);
});

test("search produces a cited report that keeps the other building apart", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("NYC street address").fill(WEST_FARMS);
  await page.getByRole("button", { name: "Research building" }).click();

  const report = page.getByTestId("report");
  await expect(report).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("demo-notice")).toBeVisible();
  await expect(page.getByTestId("identity-card")).toContainText("2006185");
  await expect(page.getByTestId("confidence")).toContainText("High");
  await expect(page.getByTestId("stats")).toContainText("Open HPD violations");

  // Ground truth: DOB complaint 2411142 belongs to BIN 2117584 (1135 Bryant Ave) and is
  // shown only under "other buildings on this lot", labelled as such.
  const other = page.locator("[data-finding-id]:has([data-scope=other_building_on_lot])", { hasText: "2411142" });
  await expect(other.first()).toContainText("BIN 2117584");
  for (const f of await page.locator("[data-finding-id]:has([data-scope=this_building])").all()) {
    await expect(f).not.toContainText("2411142");
  }

  // Evidence expands to a source link.
  const open = page.locator("[data-finding-id=F07]");
  await open.getByTestId("toggle-evidence").click();
  const row = open.getByTestId("evidence-row").first();
  await row.locator("button").click();
  await expect(row.getByRole("link", { name: /Source record/ })).toHaveAttribute("href", /data\.cityofnewyork\.us/);

  // Timeline, sources and export controls render.
  await expect(page.getByTestId("timeline")).toBeVisible();
  await expect(page.locator("[data-source=sr311]")).toHaveAttribute("data-status", "incomplete_pagination");
  const md = page.getByRole("link", { name: "Markdown" });
  const res = await page.request.get((await md.getAttribute("href"))!);
  expect(res.ok()).toBeTruthy();
  expect(await res.text()).toContain("2006185");
  await expect(page.getByTestId("verification")).toContainText("Passed");
});

test("ambiguous address asks the user to choose", async ({ page }) => {
  await page.goto("/investigate?address=100%20Broadway");
  await expect(page.getByTestId("ambiguous")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("candidate")).toHaveCount(2);
});

test("an address outside NYC gets no report", async ({ page }) => {
  await page.goto("/investigate?address=" + encodeURIComponent("1600 Pennsylvania Avenue NW, Washington, DC 20500"));
  await expect(page.getByTestId("no-match")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("report")).toHaveCount(0);
});

test("tax-lot-only address withholds building claims", async ({ page }) => {
  await page.goto("/investigate?address=" + encodeURIComponent("1217 West Farms Road, Bronx"));
  await expect(page.getByTestId("report")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("identity-card")).toContainText("Tax lot identified");
  await expect(page.getByTestId("identity-card")).toContainText("Not verified");
});
