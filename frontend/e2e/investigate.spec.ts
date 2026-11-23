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

test("demo mode explains an address outside the demo set instead of reporting an outage", async ({ page }) => {
  await page.goto("/investigate?address=" + encodeURIComponent("542 Senator Street, Brooklyn"));
  await expect(page.getByTestId("not-in-demo-search")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("unavailable")).toHaveCount(0);
  await expect(page.getByTestId("mode-note")).toContainText("Demo mode");
});

test("report fits a phone screen and the export controls stay reachable", async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  await page.goto("/investigate?address=" + encodeURIComponent(WEST_FARMS));
  await expect(page.getByTestId("report")).toBeVisible({ timeout: 30_000 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await expect(page.getByRole("button", { name: "Print or save as PDF" })).toBeVisible();
  await expect(page.getByRole("button", { name: "JSON" })).toBeVisible();
  await ctx.close();
});

test("keyboard users can skip straight to the content", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: /skip to/i });
  await expect(skip).toBeFocused();
});

test("Building Attention Summary leads the report with a label, reasons and coverage", async ({ page }) => {
  await page.goto("/investigate?address=" + encodeURIComponent(WEST_FARMS));
  const summary = page.getByTestId("attention-summary");
  await expect(summary).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("attention-label")).toHaveText("Priority follow-up");
  await expect(summary).toContainText("111 open violations");
  await expect(summary).toContainText("not proof of a problem");
  await expect(page.getByTestId("data-coverage")).toBeVisible();
  const why = page.getByRole("button", { name: "Why am I seeing this?" });
  await why.click();
  await expect(why).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#why-panel")).toContainText("Class C");
  await expect(summary).not.toContainText(/\b(safe|dangerous|should not rent)\b/i);
  // The glossary explains identifiers in plain English.
  await page.getByRole("button", { name: "Building (BIN)" }).hover();
  await expect(page.getByRole("tooltip")).toContainText("unique identifier");
});

test("tax-lot-only report says the information is insufficient", async ({ page }) => {
  await page.goto("/investigate?address=" + encodeURIComponent("1217 West Farms Road, Bronx"));
  await expect(page.getByTestId("attention-label")).toHaveText("Insufficient information", { timeout: 30_000 });
});

test("trust pages and the correction form work without an account", async ({ page }) => {
  await page.goto("/methodology");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Building Attention Summary");
  await page.goto("/corrections?address=" + encodeURIComponent(WEST_FARMS) + "&report=abcdef123456");
  await page.getByLabel("What is wrong?").fill("E2E test: a violation shown as open was corrected.");
  await page.getByRole("button", { name: "Send correction request" }).click();
  await expect(page.getByTestId("correction-done")).toBeVisible();
  for (const path of ["/privacy", "/terms", "/how-it-works"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  }
});
