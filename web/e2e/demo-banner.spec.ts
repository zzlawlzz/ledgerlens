import { expect, test } from "@playwright/test";

// T-036 §5: the public-demo banner renders only when the backend reports it is
// running under BUDGET_PROFILE=demo (`/api/examples` → `demo: true`). These
// mock that endpoint so both branches are covered without a demo backend.

test("demo banner appears with a repo link when demo=true", async ({ page }) => {
  await page.route("**/api/examples", (route) =>
    route.fulfill({ json: { examples: [], mode: "us", demo: true } }),
  );
  await page.goto("/");
  await page.getByTestId("lang-switch").getByRole("button", { name: "EN" }).click();

  const banner = page.getByTestId("demo-banner");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("Public demo");
  const link = banner.getByRole("link");
  await expect(link).toHaveAttribute("href", "https://github.com/zzlawlzz/ledgerlens");

  // Retranslates with the language switch like the other statics.
  await page.getByTestId("lang-switch").getByRole("button", { name: "RU" }).click();
  await expect(banner).toContainText("Публичное демо");
});

test("demo banner is hidden off the demo profile (demo=false)", async ({ page }) => {
  await page.route("**/api/examples", (route) =>
    route.fulfill({ json: { examples: [], mode: "us", demo: false } }),
  );
  await page.goto("/");
  await expect(page.getByTestId("disclaimer")).toBeVisible();
  await expect(page.getByTestId("demo-banner")).toHaveCount(0);
});
