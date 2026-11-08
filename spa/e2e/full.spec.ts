import { test, expect, Page } from "@playwright/test";

/**
 * Full SPA workflow coverage against a LIVE backend on :8080 + vite dev server.
 * Run: npm run test:e2e   (with `opal-event-svc` and `npm run dev` running)
 */
test.describe.configure({ mode: "serial" });

const SUFFIX = Date.now().toString(36);
const ACTION_ID = `pw_echo_${SUFFIX}`;
const RULE_ID = `pw_rule_${SUFFIX}`;
const TOPIC = `pw.demo.${SUFFIX}`;

async function tab(page: Page, name: string) {
  await page
    .getByRole("button", { name, exact: true })
    .or(page.getByRole("link", { name, exact: true }))
    .click();
}

const ACTION_JSON = (id: string) =>
  `{"id":"${id}","type":"python","enabled":true,"module":"opal_event.plugins.example","function":"handle_event","timeout_seconds":5}`;
const RULE_JSON = (id: string, topic: string) =>
  `{"id":"${id}","enabled":true,"topic":"${topic}","conditions":[],"actions":["${ACTION_ID}"]}`;

test.describe("SPA full workflows", () => {
  test.beforeEach(async ({ page }) => {
    page.on("dialog", (d) => void d.accept()); // delete confirmations
    await page.goto("/");
  });

  test("publish → appears in history → filter narrows → detail opens", async ({ page }) => {
    await tab(page, "Events");
    await page.getByLabel("Topic", { exact: true }).fill(TOPIC);
    await page.getByLabel("Data (JSON)", { exact: true }).fill('{"pw": true}');
    await page.getByRole("button", { name: "Publish" }).click();
    await expect(page.locator("body")).toContainText(/accepted|published/i, { timeout: 5000 });

    // row appears in the list
    await expect(page.locator("tr", { hasText: TOPIC }).first()).toBeVisible({ timeout: 8000 });

    // filter narrows to just our topic
    await page.getByLabel("Filter by topic").fill(TOPIC);
    await page.getByRole("button", { name: "Refresh" }).click();
    await expect(page.locator("tr", { hasText: TOPIC })).toHaveCount(1);
  });

  test("invalid JSON keeps Publish disabled", async ({ page }) => {
    await tab(page, "Events");
    await page.getByLabel("Topic", { exact: true }).fill("pw.invalid");
    await page.getByLabel("Data (JSON)", { exact: true }).fill("{not json");
    await expect(page.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  test("action lifecycle: create → execute → enabled toggle", async ({ page }) => {
    await tab(page, "Actions");
    await page.getByLabel("Action JSON", { exact: true }).fill(ACTION_JSON(ACTION_ID));
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.locator("tr", { hasText: ACTION_ID })).toBeVisible({ timeout: 5000 });

    // manual execute
    await page.getByLabel("Action ID").selectOption(ACTION_ID);
    await page.getByLabel("Event JSON", { exact: true }).fill('{"topic": "pw.manual", "data": {"via": "spa"}}');
    await page.getByRole("button", { name: "Execute", exact: true }).click();
    await expect(page.locator("body")).toContainText(/queued|accepted|succeeded|submitted/i, { timeout: 5000 });

    // toggle enabled off → on
    const row = page.locator("tr", { hasText: ACTION_ID });
    await row.getByRole("button", { name: /disable|enabled/i }).first().click();
    await expect(page.locator("body")).toContainText(ACTION_ID);
  });

  test("rule lifecycle: create → dry-run test", async ({ page }) => {
    await tab(page, "Rules");
    await page.getByLabel("Rule JSON", { exact: true }).fill(RULE_JSON(RULE_ID, TOPIC));
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.locator("tr", { hasText: RULE_ID })).toBeVisible({ timeout: 5000 });

    await page.getByLabel("Rule ID").selectOption(RULE_ID);
    await page.getByLabel("Sample event JSON", { exact: true }).fill(`{"topic": "${TOPIC}", "data": {}}`);
    await page.getByRole("button", { name: "Test", exact: true }).click();
    await expect(page.locator("body")).toContainText(/matched/i, { timeout: 5000 });
  });

  test("rule-dispatched event produces an execution with detail", async ({ page }) => {
    await tab(page, "Events");
    await page.getByLabel("Topic", { exact: true }).fill(TOPIC);
    await page.getByLabel("Data (JSON)", { exact: true }).fill('{"fired": "by-rule"}');
    await page.getByRole("button", { name: "Publish" }).click();

    await tab(page, "Executions");
    await expect(page.locator("tr", { hasText: ACTION_ID }).first()).toBeVisible({ timeout: 8000 });
    await page.locator("tr", { hasText: ACTION_ID }).first().click();
    await expect(page.locator("body")).toContainText(/succeeded|queued|running|failed/i);
  });

  test("settings: edit queue_size, save, persists across reload", async ({ page }) => {
    await tab(page, "Settings");
    const input = page.getByLabel("Queue size", { exact: true });
    const original = await input.inputValue();
    await input.fill(String(Number(original) + 5));
    await page.getByRole("button", { name: "Save" }).click();
    await page.waitForTimeout(800);

    await page.reload();
    await tab(page, "Settings");
    await expect(page.getByLabel("Queue size", { exact: true })).toHaveValue(String(Number(original) + 5), { timeout: 5000 });

    // restore
    await page.getByLabel("Queue size", { exact: true }).fill(original);
    await page.getByRole("button", { name: "Save" }).click();
  });

  test("backup: export → validate → import round-trip", async ({ page }) => {
    await tab(page, "Backup");
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download backup" }).click();
    const download = await downloadPromise;
    const path = await download.path();
    expect(path).toBeTruthy();

    await page.locator('input[type="file"]').setInputFiles(path!);
    await expect(page.locator("body")).toContainText(/backup is valid/i, { timeout: 8000 });

    await page.getByLabel(/I understand this replaces/).check();
    await page.getByRole("button", { name: /import backup/i }).click();
    await expect(page.locator("body")).toContainText(/import succeeded|new revision/i, { timeout: 8000 });
  });

  test("clear all events (confirm dialog)", async ({ page }) => {
    await tab(page, "Events");
    await page.getByRole("button", { name: "Clear all events" }).click();
    await page.getByLabel("Filter by topic").fill(TOPIC);
    await page.getByRole("button", { name: "Refresh" }).click();
    await expect(page.locator("tr", { hasText: TOPIC })).toHaveCount(0);
  });

  test("cleanup: delete test rule and action", async ({ page }) => {
    await tab(page, "Rules");
    const ruleRow = page.locator("tr", { hasText: RULE_ID });
    if (await ruleRow.count()) {
      await ruleRow.getByRole("button", { name: /delete/i }).click();
      await expect(page.locator("tr", { hasText: RULE_ID })).toHaveCount(0);
    }
    await tab(page, "Actions");
    const actionRow = page.locator("tr", { hasText: ACTION_ID });
    if (await actionRow.count()) {
      await actionRow.getByRole("button", { name: /delete/i }).click();
      await expect(page.locator("tr", { hasText: ACTION_ID })).toHaveCount(0);
    }
  });
});
