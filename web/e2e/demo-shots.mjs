// T-025: capture the two self-correction chains from the live UI.
// Usage: node e2e/demo-shots.mjs (vite dev on :3001, stack up)
import { mkdirSync } from "node:fs";
import { chromium } from "@playwright/test";

const BASE = process.env.SHOTS_BASE ?? "http://localhost:3001";
const OUT = "../demo/screenshots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });

async function freshSession() {
  await page.goto(BASE);
  await page.evaluate(() => localStorage.removeItem("session_id"));
  await page.reload();
  await page.getByTestId("lang-switch").getByRole("button", { name: "EN" }).click();
}

// Scenario A: worker-level correction (non-canonical metric term).
await freshSession();
await page.locator('button.example[data-kind="self_correction"]').first().click();
await page.getByTestId("answer").waitFor({ timeout: 900_000 });
await page.waitForTimeout(1000);
await page.screenshot({ path: `${OUT}/self_correction_worker.png`, fullPage: true });
console.log("saved: self_correction_worker.png");

// Scenario B: orchestrator-level replan (company outside the corpus).
await freshSession();
await page
  .getByTestId("question-input")
  .fill("Compare the fiscal 2025 revenue of Apple and Tesla using the loaded data.");
await page.getByTestId("ask-button").click();
await page.getByTestId("answer").waitFor({ timeout: 1_500_000 });
await page.waitForTimeout(1000);
await page.screenshot({ path: `${OUT}/self_correction_replan.png`, fullPage: true });
console.log("saved: self_correction_replan.png");

await browser.close();
