import { defineConfig } from "@playwright/test";

// The smoke drives a real multi-step run through the live stack (T-024):
// dev server on :3000 proxying to the orchestrator on :8000.
export default defineConfig({
  testDir: "e2e",
  // Dev-box reality: a multi-step run with local-tier classify/extract takes
  // 10-15 minutes. No retries: an aborted attempt keeps running server-side
  // (by design) and would compete with its own retry for the CPU.
  timeout: 1_500_000,
  expect: { timeout: 120_000 },
  retries: 0,
  workers: 1,
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
  },
});
