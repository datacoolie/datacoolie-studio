import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright";

export function document(revision = "old") {
  return { source: { source_id: 10, environment_id: 7, uri: "metadata/orders.json", format: "json", revision: { checksum: revision } }, sheets: {}, issues: [] };
}
export function workspace(revision = "old") {
  return { schema_version: "metadata-editor-workspace.v1", environment_id: 7, metadata_catalog_version: revision, document: document(revision), draft: null };
}
export const backup = { id: 42, project_id: 3, environment_id: 7, source_id: 10, source_uri: "metadata/orders.json", created_at: "2026-10-08T00:00:00Z" };
export const operation = { operation_key: "write-1", environment_id: 7, state: "prepared", created_at: "2026-10-08", updated_at: "2026-10-08", expires_at: "2026-10-09", completed_at: null, error: null, summary: {}, items: [] };
const server = await createServer({ root: fileURLToPath(new URL("../", import.meta.url)), server: { host: "127.0.0.1", port: 0, hmr: false } });
let browser;
const contexts = [];
const graph = label => ({ schema_version: "lineage.v4", summary: { assets: 1, references: 0, dataflows: 1, dependencies: 0, stitched_assets: 0, declared_assets: 1, automatic_references: 0, manual_references: 0, unresolved_references: 0, automatic_dependencies: 0, manual_dependencies: 0, unresolved_dependencies: 0, diagnostics: 0 }, assets: [{ id: "asset:orders", label, entity_type: "asset", asset_type: "table", declaration_status: "declared", display_name: label }], references: [], dataflows: [{ id: "flow:orders", dataflow_id: "orders", name: "Orders", metadata_source_id: 10, metadata_source_uri: "metadata/orders.json", destination_asset_id: "asset:orders" }], dependencies: [] });
const environmentContext = version => ({ schema_version: "environment-context.v1", checked_at: "2026-10-08T00:00:00Z", project: { id: 3, name: "Fixture" }, environment: { id: 7, project_id: 3, name: "test" }, source_counts: { metadata: 1, sql: 0, logs: 0, code: 0 }, freshness: { metadata: { status: "current" }, logs: { status: "current" } }, versions: { source_registry: "s1", metadata_catalog: version, code_catalog: "c1", operations: "o1", reference_mappings: "r1" } });
let count = 0;
try {
  await server.listen();
  browser = await chromium.launch({ channel: process.env.STUDIO_TEST_BROWSER === "msedge" ? "msedge" : undefined });
  const origin = server.resolvedUrls.local[0];
  async function scenario(name, test) {
    if (process.env.STUDIO_TEST_CASE && !name.includes(process.env.STUDIO_TEST_CASE)) return;
    const context = await browser.newContext();
    contexts.push(context);
    const page = await context.newPage();
    const unexpected = [];
    const pageErrors = [];
    page.on("pageerror", error => { pageErrors.push(error.message); console.error(`BROWSER ${name}: ${error.message}`); });
    await page.route("**/api/v1/**", async route => {
      unexpected.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
      await route.fulfill({ status: 500, body: "Unexpected fixture request" });
    });
    const json = (route, body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    try {
      await test({ page, context, origin, json });
      assert.deepEqual(unexpected, [], "all API requests must have explicit synthetic fixtures");
      assert.deepEqual(pageErrors, [], "browser must not throw");
      console.log(`PASS ${name}`);
      count++;
    } finally {
      await page.evaluate(() => window.lifecycle?.dispose()).catch(() => {});
      await context.close();
    }
  }
  async function mount(page) {
    await page.goto(`${origin}scripts/fixtures/frontend-lifecycle.html`);
    await page.waitForFunction(() => window.lifecycle?.editor);
  }
  await scenario("restore fences a GET started before mutation", async ({ page, json }) => {
    let releaseRead;
    let readStarted;
    const started = new Promise(resolve => { readStarted = resolve; });
    const barrier = new Promise(resolve => { releaseRead = resolve; });
    await page.route("**/metadata-editor-workspace", async route => {
      readStarted();
      await barrier;
      await json(route, workspace("old"));
    });
    await page.route("**/metadata-backups/42/restore", route => json(route, workspace("restored")));
    await mount(page);
    await page.evaluate(value => {
      const driver = window.lifecycle;
      driver.client.setQueryData(driver.workspaceKey, value);
      window.oldRead = driver.readWorkspace().catch(() => {});
    }, workspace());
    await started;
    await page.evaluate(async ({ backup, doc }) => window.lifecycle.editor.restoreBackup(backup, doc), { backup, doc: document() });
    releaseRead();
    await page.evaluate(() => window.oldRead);
    assert.equal(await page.evaluate(() => window.lifecycle.client.getQueryData(window.lifecycle.workspaceKey).document.source.revision.checksum), "restored");
  });
  for (const action of ["saveDraft", "discardDraft", "restoreBackup"]) {
    await scenario(`${action} fences GET started during mutation`, async ({ page, json }) => {
      let releaseMutation, releaseRead, mutationStarted, readStarted;
      const mutationBarrier = new Promise(resolve => { releaseMutation = resolve; });
      const readBarrier = new Promise(resolve => { releaseRead = resolve; });
      const mutationReady = new Promise(resolve => { mutationStarted = resolve; });
      const readReady = new Promise(resolve => { readStarted = resolve; });
      await page.route("**/metadata-editor-workspace", async route => { readStarted(); await readBarrier; await json(route, { ...workspace("old"), draft: document("stale-draft") }); });
      const path = action === "restoreBackup" ? "**/metadata-backups/42/restore" : "**/metadata-editor-document/draft";
      await page.route(path, async route => { mutationStarted(); await mutationBarrier; await json(route, action === "restoreBackup" ? workspace("restored") : action === "saveDraft" ? document("new-draft") : null); });
      await mount(page);
      await page.evaluate(({ action, backup, doc, old }) => {
        const driver = window.lifecycle;
        driver.client.setQueryData(driver.workspaceKey, { ...old, draft: doc });
        window.mutation = action === "restoreBackup" ? driver.editor.restoreBackup(backup, doc) : action === "saveDraft" ? driver.editor.saveDraft(doc) : driver.editor.discardDraft();
      }, { action, backup, doc: document("new-draft"), old: workspace() });
      await mutationReady;
      await page.evaluate(() => { window.oldRead = window.lifecycle.readWorkspace().catch(() => {}); });
      await readReady;
      releaseMutation();
      await page.evaluate(() => window.mutation);
      releaseRead();
      await page.evaluate(() => window.oldRead);
      const cached = await page.evaluate(() => window.lifecycle.client.getQueryData(window.lifecycle.workspaceKey));
      assert.equal(action === "restoreBackup" ? cached.document.source.revision.checksum : cached.draft?.source.revision.checksum ?? null, action === "restoreBackup" ? "restored" : action === "saveDraft" ? "new-draft" : null);
    });
  }
  await scenario("warm apply refreshes workspace and history; busy covers header follow-up", async ({ page, json }) => {
    let reads = 0, applies = 0, lists = 0;
    await page.route("**/metadata-editor-workspace", route => { reads++; return json(route, workspace("published")); });
    await page.route("**/metadata-write-operations/write-1/apply", route => { applies++; return json(route, { ...operation, state: "succeeded" }); });
    await page.route("**/metadata-backups", route => json(route, ++lists === 1 ? [] : [backup]));
    await mount(page);
    await page.evaluate(old => {
      const driver = window.lifecycle;
      driver.client.setQueryData(driver.workspaceKey, old);
      driver.setFollowUp(() => new Promise(resolve => { window.releaseHeader = resolve; }));
    }, workspace());
    await page.evaluate(() => window.lifecycle.editor.listBackups());
    await page.evaluate(op => { window.mutation = window.lifecycle.editor.applyPreparedDocumentWrite(op); }, operation);
    await page.waitForFunction(() => window.releaseHeader);
    assert.equal(await page.getByTestId("busy").textContent(), "true");
    await page.evaluate(() => window.releaseHeader());
    const saved = await page.evaluate(() => window.mutation);
    assert.equal(saved.source.revision.checksum, "published");
    assert.equal((await page.evaluate(() => window.lifecycle.editor.listBackups())).length, 1);
    assert.equal(reads, 1); assert.equal(applies, 1); assert.equal(lists, 2);
  });
  await scenario("published unread invalidates dependents; retry reads without apply", async ({ page, json }) => {
    let fail = true, reads = 0, applies = 0;
    await page.route("**/metadata-editor-workspace", route => { reads++; return json(route, fail ? { detail: "read unavailable" } : workspace("published"), fail ? 422 : 200); });
    await page.route("**/metadata-write-operations/write-1/apply", route => { applies++; return json(route, { ...operation, state: "succeeded" }); });
    await mount(page);
    const caught = await page.evaluate(async ({ op, old }) => {
      const driver = window.lifecycle;
      driver.client.setQueryData(driver.workspaceKey, old);
      for (const branch of ["context", "assets", "lineage", "overview"]) driver.client.setQueryData(["environments", 7, branch], {});
      try { await driver.editor.applyPreparedDocumentWrite(op); } catch (error) { return { phase: error.phase, operation: error.operation }; }
    }, { op: operation, old: workspace() });
    assert.equal(caught.phase, "workspace-refresh");
    assert.deepEqual(await page.evaluate(() => ["context", "assets", "lineage", "overview"].map(branch => window.lifecycle.client.getQueryState(["environments", 7, branch]).isInvalidated)), [true, true, true, true]);
    fail = false;
    assert.equal((await page.evaluate(op => window.lifecycle.editor.applyPreparedDocumentWrite(op), caught.operation)).source.revision.checksum, "published");
    assert.equal(applies, 1); assert.equal(reads, 2);
  });
  await scenario("header error accepts authoritative restore document", async ({ page, json }) => {
    await page.route("**/metadata-backups/42/restore", route => json(route, workspace("restored")));
    await mount(page);
    await page.evaluate(() => window.lifecycle.setFollowUp(async () => { throw new Error("header unavailable"); }));
    assert.equal((await page.evaluate(({ backup, doc }) => window.lifecycle.editor.restoreBackup(backup, doc), { backup, doc: document() })).source.revision.checksum, "restored");
    await page.waitForFunction(() => window.lifecycle.editor.warning?.includes("Header refresh failed"));
  });
  await scenario("Context A completion at B invalidates A; subscription reinstall preserves correctness", async ({ page, json }) => {
    let releaseA;
    const barrier = new Promise(resolve => { releaseA = resolve; });
    const context = (id, version) => ({ environment: { id, project_id: 3 }, versions: { source_registry: "s1", metadata_catalog: version, code_catalog: "c1", operations: "o1", reference_mappings: "r1" } });
    await page.route("**/environments/*/context", async route => {
      const id = Number(new URL(route.request().url()).pathname.split("/")[4]);
      if (id === 7) await barrier;
      await json(route, context(id, id === 7 ? "new" : "old"));
    });
    await mount(page);
    await page.evaluate(({ old, context }) => {
      const driver = window.lifecycle;
      driver.client.setQueryData(driver.workspaceKey, old);
      driver.client.setQueryData(["environments", 7, "context"], context);
      driver.installReconciliation();
      driver.setEnvironment(7);
    }, { old: workspace("old"), context: context(7, "old") });
    await page.waitForFunction(() => window.lifecycle.client.isFetching({ queryKey: ["environments", 7, "context"] }) === 1);
    await page.evaluate(() => window.lifecycle.setEnvironment(8));
    releaseA();
    await page.waitForFunction(() => window.lifecycle.client.getQueryData(["environments", 7, "context"]).versions.metadata_catalog === "new");
    assert.equal(await page.evaluate(() => window.lifecycle.client.getQueryState(window.lifecycle.workspaceKey).isInvalidated), true);
    await page.evaluate(newWorkspace => {
      const driver = window.lifecycle;
      driver.client.setQueryData(driver.workspaceKey, newWorkspace);
      driver.installReconciliation();
    }, workspace("new"));
    assert.equal(await page.evaluate(() => window.lifecycle.client.getQueryState(window.lifecycle.workspaceKey).isInvalidated), false);
  });
  await scenario("Monitoring search survives page navigation, reload, Back/Forward and drawer state", async ({ page, origin }) => {
    const initial = `${origin}scripts/fixtures/frontend-lifecycle.html?fixture=router`;
    await page.goto(initial);
    await page.waitForFunction(() => window.lifecycle?.router);
    await page.evaluate(() => window.lifecycle.router.setStudioRoute({ projectId: 3, environmentId: 7, module: "monitoring", monitoringPage: "jobs", search: "?fixture=router" }));
    await page.evaluate(() => {
      history.replaceState({ drawer: { depth: 2 } }, "", location.href);
      window.lifecycle.replaceFilters({ status: "failed", investigateLogSessionScope: "exact", investigateLogSessionId: "", investigateSourceId: 10 });
    });
    assert.deepEqual(await page.evaluate(() => history.state), { drawer: { depth: 2 } });
    await page.evaluate(() => window.lifecycle.router.navigateMonitoringPage("dataflows"));
    assert.equal(new URL(page.url()).searchParams.get("status"), "failed");
    await page.evaluate(() => window.lifecycle.replaceFilters({ status: "success" }));
    await page.goBack();
    await page.waitForFunction(() => window.lifecycle.router.route.monitoringPage === "jobs");
    assert.equal(await page.getByTestId("status").textContent(), "failed");
    await page.goForward();
    await page.waitForFunction(() => window.lifecycle.router.route.monitoringPage === "dataflows");
    assert.equal(await page.getByTestId("status").textContent(), "success");
    // Vite SPA fallback mounts the real entrypoint; the fixture reload uses the exact saved search.
    await page.goto(`${initial.split("?")[0]}${new URL(page.url()).search}`);
    await page.waitForFunction(() => window.lifecycle?.router);
    assert.equal(await page.getByTestId("status").textContent(), "success");
    const params = new URL(page.url()).searchParams;
    assert.equal(params.get("investigateLogSessionId"), "");
    assert.equal(params.get("investigateSourceId"), "10");
  });
  await scenario("cold editor retains selection through failure and stale-aware retry", async ({ page, json }) => {
    let release, fail = true, reads = 0;
    const barrier = new Promise(resolve => { release = resolve; });
    const current = workspace("new");
    current.document.sheets = { dataflows: { name: "dataflows", columns: [{ key: "name", label: "Name" }], rows: [{ name: "Orders", dataflow_id: "orders", __metadata_source_id: 10 }] } };
    await page.route("**/metadata-editor-workspace", async route => { reads++; await barrier; await json(route, fail ? { detail: "unavailable" } : current, fail ? 422 : 200); });
    await mount(page);
    assert.equal(reads, 0);
    await page.evaluate(() => window.lifecycle.setSelection({ metadataSourceId: 10, dataflowId: "orders", name: "Orders" }));
    await page.getByRole("dialog", { name: "Dataflow details" }).waitFor();
    release();
    await page.getByRole("button", { name: "Retry", exact: true }).waitFor();
    fail = false;
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await page.waitForFunction(() => window.lifecycle.controller.selectedRecord?.row.name === "Orders");
    const beforeReopen = reads;
    await page.evaluate(() => window.lifecycle.setSelection(null));
    await page.waitForFunction(() => !window.lifecycle.controller.selectedRecord);
    await page.evaluate(() => window.lifecycle.setSelection({ metadataSourceId: 10, dataflowId: "orders", name: "Orders" }));
    await page.waitForFunction(() => window.lifecycle.controller.selectedRecord);
    assert.equal(reads, beforeReopen);
  });
  await scenario("registry mapping fences pending GET and follows captured project", async ({ page, origin, json }) => {
    let reads = 0, release;
    const barrier = new Promise(resolve => { release = resolve; });
    const value = (id, label) => ({ project_id: id, mappings: [], rows: [], targets: [], failures: [{ environment_id: 7, environment_name: label, message: label }] });
    await page.route("**/projects/3/reference-registry", async route => { reads++; if (reads === 1) await barrier; await json(route, value(3, reads === 1 ? "old" : "new")); });
    await page.route("**/projects/4/reference-registry", route => json(route, value(4, "B")));
    await page.route("**/projects/3/reference-mappings", route => json(route, { id: 1 }));
    await page.route("**/projects/summary", route => json(route, []));
    await page.goto(`${origin}scripts/fixtures/frontend-lifecycle.html?fixture=registry`);
    await page.waitForFunction(() => window.lifecycle?.registry);
    await page.evaluate(async () => window.lifecycle.registryMutation.createProjectReferenceMapping({ reference_type: "table_reference", reference_value: "orders", target_identifier_kind: "table", target_value: "orders" }));
    release();
    await page.waitForFunction(() => window.lifecycle.registry.data?.failures[0]?.environment_name === "new");
    assert.ok(reads >= 2);
    await page.evaluate(() => window.lifecycle.setProject(4));
    await page.waitForFunction(() => window.lifecycle.registry.data?.project_id === 4);
    await page.evaluate(() => window.lifecycle.setProject(3));
    await page.waitForFunction(() => window.lifecycle.registry.data?.project_id === 3);
    assert.equal(await page.getByTestId("error").textContent(), "");
  });
  await scenario("Context advancement cancels active structural GET transport", async ({ page, origin, json }) => {
    let reads = 0, release, advanced = false;
    const barrier = new Promise(resolve => { release = resolve; });
    await page.route("**/environments/7/lineage", async route => { reads++; const old = !advanced; if (old) await barrier; await json(route, graph(old ? "old" : "new")); });
    await page.route("**/environments/7/context", route => json(route, environmentContext("old")));
    await page.goto(`${origin}scripts/fixtures/frontend-lifecycle.html?fixture=graph`);
    await page.waitForFunction(() => window.lifecycle?.client);
    await page.waitForFunction(() => window.lifecycle.client.getQueryData(["environments", 7, "context"])?.versions.metadata_catalog === "old");
    await page.waitForFunction(() => window.lifecycle?.client.isFetching({ queryKey: ["environments", 7, "lineage"] }) > 0);
    advanced = true;
    await page.evaluate(value => window.lifecycle.client.setQueryData(["environments", 7, "context"], value), environmentContext("new"));
    await page.waitForFunction(() => window.lifecycle.client.getQueryData(["environments", 7, "lineage"])?.assets[0]?.label === "new");
    release();
    assert.ok(reads >= 2);
    assert.equal(await page.getByTestId("graph").textContent(), "new");
  });
  async function appFixtures(page, json) {
    await page.route("**/sources/observe-local", route => json(route, { changed: 0, outcomes: [], checked_at: "2026-10-08T00:00:00Z", environment_id: 7 }));
    await page.route("**/projects/*/environments/*/*", async route => {
      if (route.request().resourceType() === "document") {
        const response = await page.request.get(`${origin}scripts/fixtures/frontend-lifecycle.html`);
        await route.fulfill({ response, body: (await response.text()).replace('src="./frontend-lifecycle.tsx"', 'src="/scripts/fixtures/frontend-lifecycle.tsx"') });
      } else await route.fallback();
    });
    await page.route("**/studio/modules", route => json(route, ["metadata", "assets", "lineage", "monitoring"].map(key => ({ key, name: key, enabled: true, default_enabled: true, togglable: true, status: "available", description: "", group: "workspace", pages: [] }))));
    await page.route("**/studio/settings", route => json(route, { timezone: "UTC", timezone_offset_minutes: 0, timezone_source: "configured", source_check_mode: "fixed", source_check_interval_seconds: 3600, source_check_max_interval_seconds: 3600 }));
    await page.route("**/environments/7/context", route => json(route, environmentContext("old")));
    await page.route("**/environments/7/monitoring/latest-status", route => json(route, { dataflows: {}, assets: {}, summary: {} }));
  }
  for (const module of ["assets", "lineage"]) {
    await scenario(`real App cold ${module} has no metadata bootstrap`, async ({ page, origin, json }) => {
      await appFixtures(page, json);
      let metadataReads = 0;
      await page.route("**/metadata-editor-workspace", route => { metadataReads++; return json(route, workspace()); });
      await page.route("**/api/v1/environments/7/assets*", route => json(route, { catalog_version: "old", summary: {}, items: [], filter_options: {} }));
      await page.route("**/api/v1/environments/7/lineage*", route => json(route, graph("Orders")));
      await page.goto(`${origin}projects/3/environments/7/${module}?fixture=app`);
      await page.waitForSelector(`.content-${module}`);
      if (module === "assets") await page.waitForSelector(".assets-view");
      else await page.waitForSelector(".lineage-layout");
      assert.equal(metadataReads, 0);
      if (module === "lineage") {
        let fail = true;
        const current = workspace("new");
        current.document.sheets = { dataflows: { columns: [{ key: "name", name: "Name" }], rows: [{ name: "Orders", dataflow_id: "orders", __metadata_source_id: 10 }] } };
        await page.route("**/metadata-editor-workspace", route => { metadataReads++; return json(route, fail ? { detail: "unavailable" } : current, fail ? 422 : 200); });
        await page.route("**/api/v1/environments/7/monitoring/dataflows*", route => json(route, { records: [], summary: { total_records: 0, records: 0, limit: 10, offset: 0 }, errors: [] }));
        await page.goto(`${origin}projects/3/environments/7/lineage?fixture=app&focusDataflowSource=10&focusDataflowId=orders`);
        await page.getByRole("button", { name: "Open dataflow", exact: true }).click();
        await page.getByRole("dialog", { name: "Dataflow details" }).getByRole("button", { name: "Retry", exact: true }).waitFor();
        fail = false;
        await page.getByRole("dialog", { name: "Dataflow details" }).getByRole("button", { name: "Retry", exact: true }).click();
        await page.waitForSelector(".dataflow-drawer-body");
        await page.keyboard.press("Escape");
        await page.waitForFunction(() => !document.querySelector(".dataflow-drawer-body"));
      }
    });
  }
  await scenario("real App metadata accepts published document and reports header warning", async ({ page, origin, json }) => {
    await appFixtures(page, json);
    let published = false, applies = 0;
    const before = workspace("old");
    before.document.sheets = { connections: { columns: [], rows: [] }, dataflows: { columns: [{ key: "name", name: "Name" }], rows: [{ name: "Old", __metadata_source_id: 10 }] }, schema_hints: { columns: [], rows: [] } };
    before.draft = structuredClone(before.document);
    before.draft.sheets.dataflows.rows[0].name = "Published";
    const after = structuredClone(before);
    after.document = structuredClone(before.draft); after.document.source.revision = { checksum: "published" }; after.draft = null; after.metadata_catalog_version = "new";
    await page.route("**/metadata-editor-workspace", route => json(route, published ? after : before));
    await page.route("**/metadata-write-operations/prepare", route => json(route, operation));
    await page.route("**/metadata-write-operations/write-1/apply", route => { applies++; published = true; return json(route, { ...operation, state: "succeeded" }); });
    await page.route("**/api/v1/environments/7/context", route => json(route, published ? { detail: "header unavailable" } : environmentContext("old"), published ? 422 : 200));
    await page.goto(`${origin}projects/3/environments/7/metadata?fixture=app`);
    await page.getByRole("button", { name: "Save to source", exact: true }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Save to source", exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('[role="alertdialog"]'));
    await page.waitForFunction(() => Object.values(localStorage).some(value => value.includes("Source saved; refresh needs attention")));
    assert.equal(applies, 1);
    await page.getByRole("button", { name: "Dataflows", exact: true }).click();
    assert.equal(await page.locator(".metadata-grid-wrap input.dsg-input").first().inputValue(), "Published");
    assert.equal(await page.locator('[aria-label="Metadata mode"] button.active').textContent(), "View");
  });
  await scenario("real App Monitoring filter intent survives page switch and Back", async ({ page, origin, json }) => {
    await appFixtures(page, json);
    const requests = [];
    await page.route("**/api/v1/environments/7/monitoring/pages/**", route => { requests.push(new URL(route.request().url()).searchParams.get("status")); return json(route, { summary: {}, operations: {}, diagnostics: {}, errors: [] }); });
    await page.route("**/api/v1/environments/7/monitoring/filter-options*", route => json(route, {}));
    await page.route("**/api/v1/environments/7/monitoring/jobs*", route => json(route, { records: [], summary: { records: 0, total_records: 0, limit: 500, offset: 0 }, errors: [] }));
    await page.route("**/api/v1/environments/7/monitoring/dataflows*", route => json(route, { records: [], summary: { records: 0, total_records: 0, limit: 500, offset: 0 }, errors: [] }));
    await page.goto(`${origin}projects/3/environments/7/monitoring/jobs?fixture=app`);
    const status = page.locator(".monitoring-filter-dropdown").filter({ has: page.locator("span", { hasText: /^Status$/ }) });
    await status.locator(".monitoring-filter-trigger").click();
    await status.getByRole("button", { name: "failed", exact: true }).click();
    await page.waitForFunction(() => new URL(location.href).searchParams.get("status") === "failed");
    await page.getByRole("button", { name: "Dataflows", exact: true }).click();
    await page.waitForFunction(() => location.pathname.endsWith("/dataflows"));
    assert.equal(new URL(page.url()).searchParams.get("status"), "failed");
    await page.goBack();
    await page.waitForFunction(() => location.pathname.endsWith("/jobs"));
    assert.ok(requests.includes("failed"));
  });
  await scenario("real History retries list failure, refreshes after restore, and reuses preview", async ({ page, origin, json }) => {
    await appFixtures(page, json);
    let listFail = true, lists = 0, previews = 0, restored = false;
    const current = workspace(); current.document.sheets = { connections: { columns: [], rows: [] }, dataflows: { columns: [], rows: [] }, schema_hints: { columns: [], rows: [] } };
    await page.route("**/metadata-editor-workspace", route => json(route, current));
    await page.route("**/metadata-backups", route => { lists++; return json(route, listFail ? { detail: "history unavailable" } : restored ? [backup, { ...backup, id: 43 }] : [backup], listFail ? 422 : 200); });
    await page.route("**/metadata-backups/42/editor-document", route => { previews++; return json(route, current.document); });
    await page.route("**/metadata-backups/42/restore", route => { restored = true; return json(route, { ...current, metadata_catalog_version: "restored" }); });
    page.on("dialog", dialog => dialog.accept());
    await page.goto(`${origin}projects/3/environments/7/metadata?fixture=app`);
    await page.getByRole("button", { name: "History", exact: true }).click();
    await page.locator(".metadata-history-error").getByRole("button", { name: "Retry" }).waitFor();
    listFail = false;
    await page.locator(".metadata-history-error").getByRole("button", { name: "Retry" }).click();
    await page.locator(".metadata-history-item").first().click();
    await page.waitForFunction(() => !document.querySelector(".metadata-history-preview")?.textContent?.includes("Loading preview"));
    await page.getByRole("button", { name: "Close history" }).click();
    await page.getByRole("button", { name: "History", exact: true }).click();
    await page.locator(".metadata-history-item").first().click();
    assert.equal(previews, 1);
    await page.getByRole("button", { name: /Restore/ }).click();
    await page.waitForFunction(() => !document.querySelector(".metadata-history-drawer"));
    await page.getByRole("button", { name: "History", exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll(".metadata-history-item").length === 2);
    assert.equal(lists, 3);
  });
  await scenario("partial apply retains server operation", async ({ page, json }) => {
    await page.route("**/metadata-write-operations/write-1/apply", route => json(route, { ...operation, state: "partial", items: [] }));
    await mount(page);
    const error = await page.evaluate(async op => { try { await window.lifecycle.editor.applyPreparedDocumentWrite(op); } catch (error) { return { phase: error.phase, key: error.operation.operation_key, state: error.operation.state }; } }, operation);
    assert.deepEqual(error, { phase: "publication", key: "write-1", state: "partial" });
  });
  await scenario("retry completion after selection change cannot replace the new drawer", async ({ page, json }) => {
    let fail = true, release;
    const barrier = new Promise(resolve => { release = resolve; });
    const current = workspace("new"); current.document.sheets = { dataflows: { columns: [{ key: "name", name: "Name" }], rows: [{ name: "A", dataflow_id: "A" }, { name: "B", dataflow_id: "B" }] } };
    await page.route("**/metadata-editor-workspace", async route => { if (!fail) await barrier; await json(route, fail ? { detail: "unavailable" } : current, fail ? 422 : 200); });
    await mount(page);
    await page.evaluate(() => window.lifecycle.setSelection({ name: "A", dataflowId: "A" }));
    await page.getByRole("button", { name: "Retry", exact: true }).waitFor();
    fail = false;
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await page.evaluate(() => window.lifecycle.setSelection({ name: "B", dataflowId: "B" }));
    release();
    await page.waitForFunction(() => window.lifecycle.controller.selectedRecord?.name === "B");
    assert.equal(await page.getByTestId("record").textContent(), "B");
  });
  await scenario("mapping failure after switching project does not change active UI error", async ({ page, origin, json }) => {
    let release;
    const barrier = new Promise(resolve => { release = resolve; });
    await page.route("**/reference-registry", route => json(route, { project_id: Number(new URL(route.request().url()).pathname.split("/")[4]), rows: [], mappings: [], targets: [], failures: [] }));
    await page.route("**/reference-mappings", async route => { await barrier; await json(route, { detail: "old project failure" }, 422); });
    await page.goto(`${origin}scripts/fixtures/frontend-lifecycle.html?fixture=registry`);
    await page.waitForFunction(() => window.lifecycle?.registry);
    await page.evaluate(() => { window.mapping = window.lifecycle.registryMutation.createProjectReferenceMapping({ reference_type: "table_reference", reference_value: "x", target_identifier_kind: "logical_table", target_value: "x" }).catch(() => {}); });
    await page.evaluate(() => window.lifecycle.setProject(4));
    await page.waitForFunction(() => window.lifecycle.projectId === 4);
    release();
    await page.evaluate(() => window.mapping);
    assert.equal(await page.getByTestId("error").textContent(), "");
  });
  console.log(`${count} lifecycle scenarios passed`);
} finally {
  await Promise.allSettled(contexts.map(context => context.close()));
  await browser?.close();
  await server.close();
}
