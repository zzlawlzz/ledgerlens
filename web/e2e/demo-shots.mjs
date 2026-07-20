// T-025/T-042: capture the feature screenshots from the live UI.
// Usage: node e2e/demo-shots.mjs (vite dev on :3001, stack up), or
//        SHOTS_BASE=http://localhost:3000 node e2e/demo-shots.mjs (built web container)
// Outputs (used by README EN/RU and site/):
//   self_correction_worker.png — worker-level correction (non-canonical term)
//   citations.png              — narrative answer with sec.gov citation cards
//   self_correction_replan.png — orchestrator replan on an empty narrative step
//   web_search.png             — trust-tiered web search for an off-corpus company
import { mkdirSync } from "node:fs";
import { chromium } from "@playwright/test";

const BASE = process.env.SHOTS_BASE ?? "http://localhost:3001";
const OUT = "../demo/screenshots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
// Dark scheme to match the published screenshots' look on the site/README.
const page = await browser.newPage({
  viewport: { width: 1400, height: 1000 },
  colorScheme: "dark",
});

async function freshSession() {
  await page.goto(BASE);
  await page.evaluate(() => localStorage.removeItem("session_id"));
  await page.reload();
  await page.getByTestId("lang-switch").getByRole("button", { name: "EN" }).click();
}

async function ask(question) {
  await page.getByTestId("question-input").fill(question);
  await page.getByTestId("ask-button").click();
  await page.getByTestId("answer").waitFor({ timeout: 1_500_000 });
  await page.waitForTimeout(1000);
}

// Shot 1: worker-level correction — non-canonical metric term ("profit"),
// resolved via discovery to net_income (demo/self_correction.md, mechanism A).
await freshSession();
await page.locator('button.example[data-kind="self_correction"]').first().click();
await page.getByTestId("answer").waitFor({ timeout: 900_000 });
await page.waitForTimeout(1000);
await page.screenshot({ path: `${OUT}/self_correction_worker.png`, fullPage: true });
console.log("saved: self_correction_worker.png");

// Shot 2: citations — narrative example, scrolled to the sec.gov source cards
// (the chat panel scrolls internally, so fullPage would not reveal them).
await freshSession();
await page.locator('button.example[data-kind="rag"]').first().click();
await page.getByTestId("answer").waitFor({ timeout: 900_000 });
await page.waitForTimeout(1000);
await page.getByTestId("citations").scrollIntoViewIfNeeded();
await page.waitForTimeout(500);
console.log(`citation cards: ${await page.getByTestId("citation-card").count()}`);
await page.screenshot({ path: `${OUT}/citations.png` });
console.log("saved: citations.png");

// Shot 3: orchestrator-level replan (mechanism B). Netflix is outside the
// frozen 10-ticker corpus (T-028 moved Tesla in). A numeric question about a
// real missing company now goes to web_search (T-043), so the replan demo is
// the narrative, filings-only question: rag_search returns nothing, the plan
// is revised in view, and the absence is conceded honestly.
await freshSession();
await ask("What supply chain risks does Netflix disclose in its 10-K, based on the loaded filings?");
const replanned = await page.getByTestId("replanned-note").count();
console.log(`replanned-note visible: ${replanned > 0}`);
await page.screenshot({ path: `${OUT}/self_correction_replan.png`, fullPage: true });
console.log("saved: self_correction_replan.png");

// Shot 4: trust-tiered web search (T-043) — an off-corpus company in a
// comparison; the figure comes from the web with per-source trust badges.
await freshSession();
await ask("Compare the fiscal 2025 revenue of Apple and Netflix using the loaded data.");
await page.screenshot({ path: `${OUT}/web_search.png`, fullPage: true });
console.log("saved: web_search.png");

await browser.close();
