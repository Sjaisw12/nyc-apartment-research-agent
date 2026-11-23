import { defineConfig, devices } from "@playwright/test";

// Starts the FastAPI backend in offline demo mode and the built Next.js app.
// Run `npm run build` first (CI does), then `npm run test:e2e`.
export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "off",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    {
      command: "uv run uvicorn nyc_apartment_agent.api.app:app --port 8000",
      cwd: "../backend",
      url: "http://localhost:8000/health",
      env: { DATA_MODE: "demo", LLM_ENABLED: "0", INVESTIGATE_RATE_PER_MINUTE: "600" },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: "npm run start -- -p 3000",
      url: "http://localhost:3000",
      env: { NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000" },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
