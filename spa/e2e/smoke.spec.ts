import { test, expect, Page } from "@playwright/test";

async function walkTabs(page: Page) {
  const consoleErrors: string[] = [];
  const failedReqs: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("requestfailed", (r) => failedReqs.push(`${r.method()} ${r.url()} ${r.failure()?.errorText}`));
  page.on("response", (r) => { if (r.status() >= 500) failedReqs.push(`${r.status()} ${r.url()}`); });
  return { consoleErrors, failedReqs };
}

test("all tabs render against the live backend", async ({ page }) => {
  const errs = await walkTabs(page);
  await page.goto("/");
  for (const tab of ["Health", "Events", "Rules", "Actions", "Executions", "Settings", "Backup"]) {
    await page.getByRole("button", { name: tab, exact: true }).or(page.getByRole("link", { name: tab, exact: true })).click();
    await expect(page.locator("body")).not.toContainText("Cannot read properties");
    await page.waitForTimeout(400);
  }
  expect(errs.consoleErrors.filter((e) => !e.includes("favicon"))).toEqual([]);
  expect(errs.failedReqs).toEqual([]);
});

test("health page shows live status", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Health", exact: true }).or(page.getByRole("link", { name: "Health", exact: true })).click();
  await expect(page.locator("body")).toContainText(/ok|ready|healthy/i, { timeout: 5000 });
});

test("events page lists a published event", async ({ page }) => {
  // self-seed — don't depend on pre-existing rows (other specs may clear events)
  await page.request.post("http://127.0.0.1:8080/api/v1/events/", {
    data: { topic: "demo.cli", data: { via: "playwright" } },
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Events", exact: true }).or(page.getByRole("link", { name: "Events", exact: true })).click();
  await expect(page.locator("body")).toContainText("demo.cli", { timeout: 5000 });
});

test("settings page renders portable settings", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).or(page.getByRole("link", { name: "Settings", exact: true })).click();
  await expect(page.locator("body")).toContainText(/queue_size|max_workers|retention/i, { timeout: 5000 });
});
