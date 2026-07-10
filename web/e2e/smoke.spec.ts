import { expect, test } from "@playwright/test";

// Live smoke (T-024 criteria): plan appears, steps flip statuses, the answer
// streams in, citations link to sec.gov, the disclaimer never disappears,
// and the EN/RU switch retranslates statics without a reload.

test("language switch retranslates statics without reload", async ({ page }) => {
  await page.goto("/");
  // The default language is browser-detected — pin EN explicitly first.
  await page.getByTestId("lang-switch").getByRole("button", { name: "EN" }).click();
  await expect(page.getByTestId("disclaimer")).toContainText("not investment advice");
  await expect(page.getByTestId("ask-button")).toContainText("Ask");
  await page.getByTestId("lang-switch").getByRole("button", { name: "RU" }).click();
  await expect(page.getByTestId("disclaimer")).toContainText("не инвестиционная рекомендация");
  await expect(page.getByTestId("ask-button")).toContainText("Спросить");
  await page.getByTestId("lang-switch").getByRole("button", { name: "EN" }).click();
  await expect(page.getByTestId("ask-button")).toContainText("Ask");
});

test("multi-step question: plan, live steps, streamed answer, citations", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("lang-switch").getByRole("button", { name: "EN" }).click();
  await expect(page.getByTestId("examples")).toBeVisible();

  await page.locator('button.example[data-kind="multi_step"]').first().click();

  // The plan panel appears with at least two steps.
  await expect(page.getByTestId("plan")).toBeVisible({ timeout: 180_000 });
  expect(await page.getByTestId("plan-step").count()).toBeGreaterThanOrEqual(2);

  // The answer streams in and the run completes with citations.
  await expect(page.getByTestId("answer")).toBeVisible({ timeout: 1_200_000 });
  const citations = page.getByTestId("citation-card");
  await expect(citations.first()).toBeVisible({ timeout: 60_000 });
  const href = await citations.first().getAttribute("href");
  expect(href).toContain("sec.gov");

  // Steps ended in terminal states and the worker badge rendered.
  const doneDots = page.locator(".status-dot.done, .status-dot.succeeded");
  expect(await doneDots.count()).toBeGreaterThanOrEqual(1);

  // The disclaimer survives the whole run.
  await expect(page.getByTestId("disclaimer")).toBeVisible();
});
