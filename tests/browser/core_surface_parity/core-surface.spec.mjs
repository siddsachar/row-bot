import fs from "node:fs/promises";
import path from "node:path";

import AxeBuilder from "../../../frontend/node_modules/@axe-core/playwright/dist/index.mjs";
import {
  expect,
  test,
} from "../../../frontend/node_modules/@playwright/test/index.mjs";

const baseURL = process.env.ROW_BOT_BROWSER_BASE_URL;
const token = process.env.ROW_BOT_BROWSER_CONTROL_TOKEN;
if (!baseURL || !token) throw new Error("Use the isolated core-surface runner");
const origin = new URL(baseURL).origin;
const surfaceNames = [
  "workflows",
  "knowledge",
  "monitor",
  "chat",
  "chat-traces",
];

function httpOrigin(value) {
  const url = new URL(value);
  if (url.protocol === "ws:") url.protocol = "http:";
  if (url.protocol === "wss:") url.protocol = "https:";
  return url.origin;
}

function safeName(value) {
  return value.replace(/[^a-z0-9-]+/gi, "-").toLowerCase();
}

async function writeJson(testInfo, name, value) {
  const target = testInfo.outputPath(`${safeName(name)}.json`);
  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.writeFile(target, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await testInfo.attach(name, {
    path: target,
    contentType: "application/json",
  });
}

async function settle(page) {
  await page.waitForLoadState("domcontentloaded");
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
}

async function openNiceGui(page, surface) {
  await page.goto("/");
  await settle(page);
  if (surface === "chat" || surface === "chat-traces") {
    const title =
      surface === "chat" ? "Phase 1 conversation A" : "Phase 1 conversation B";
    const expected =
      surface === "chat"
        ? "Show the deterministic fixture summary."
        : "Show the deterministic trace matrix.";
    const conversation = page.getByText(title, { exact: true }).last();
    if (!(await conversation.isVisible())) {
      await page
        .getByRole("button", { name: "Toggle navigation", exact: true })
        .click();
    }
    await expect(conversation).toBeVisible();
    await conversation.click();
    await expect(page.getByText(expected)).toBeVisible();
    return;
  }
  const tab = page.locator(`[data-docs-id="home-tab-${surface}"]`);
  await expect(tab).toBeVisible();
  await tab.click();
  await expect(
    page.locator(`[data-docs-id="home-panel-${surface}"]`),
  ).toBeVisible();
  await settle(page);
}

async function openReact(page, surface) {
  if (surface === "chat" || surface === "chat-traces") {
    const conversation = surface === "chat" ? "p1-browser-a" : "p1-browser-b";
    const expected =
      surface === "chat"
        ? "Show the deterministic fixture summary."
        : "Show the deterministic trace matrix.";
    await page.goto(`/app-v2/conversations/${conversation}`);
    await expect(
      page.getByRole("textbox", { name: "Message", exact: true }),
    ).toBeVisible();
    await expect(page.getByText(expected)).toBeVisible();
    return;
  }
  await page.goto(`/app-v2/?tab=${surface}`);
  await expect(page.locator(".home-connection-status")).toContainText(
    "Connected",
  );
  const tabs = page.getByRole("tablist", { name: "Home capabilities" });
  await expect(tabs.getByRole("tab")).toHaveCount(3);
  expect(await tabs.getByRole("tab").allTextContents()).toEqual([
    "Workflows",
    "Knowledge",
    "Monitor",
  ]);
  await expect(
    tabs.getByRole("tab", { name: surface, exact: false }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".home-domain-boundary")).toHaveCount(0);
  await expect(
    page.getByText(/Open Monitor in current application/i),
  ).toHaveCount(0);
  await expect(page.getByText(/Open Knowledge settings/i)).toHaveCount(0);
  await settle(page);
}

async function metrics(page, client, surface) {
  return page.evaluate(
    ({ clientName, surfaceName }) => {
      const selector =
        clientName === "react"
          ? ".workspace-shell, .home-view, .conversation-view"
          : ".row-bot-main-shell, .q-layout";
      const root = document.querySelector(selector) ?? document.body;
      const box = root.getBoundingClientRect();
      const style = getComputedStyle(root);
      const focus = [
        ...document.querySelectorAll(
          'a[href],button,input,select,textarea,[tabindex]:not([tabindex="-1"])',
        ),
      ]
        .filter((element) => {
          const rect = element.getBoundingClientRect();
          const computed = getComputedStyle(element);
          return (
            rect.width > 0 &&
            rect.height > 0 &&
            computed.visibility !== "hidden"
          );
        })
        .slice(0, 100)
        .map((element, index) => ({
          index,
          tag: element.tagName.toLowerCase(),
          role: element.getAttribute("role"),
          label:
            element.getAttribute("aria-label") ||
            element.getAttribute("title") ||
            element.textContent?.trim().slice(0, 100) ||
            "",
        }));
      const anchors = [
        ["sidebar", ".navigation, .q-drawer--left"],
        ["status", '.home-connection-status, [data-docs-id="status-bar"]'],
        ["tabs", '[role="tablist"], [data-docs-id="home-tabs"]'],
        ["content", ".home-tab-content, .q-tab-panels, main"],
        ["composer", ".composer, .row-bot-desktop-composer"],
        ["context", ".conversation-context-rail"],
        ["buddy", ".buddy-companion, [data-buddy-in-app-shell]"],
      ].map(([name, query]) => {
        const element = document.querySelector(query);
        if (!element) return { name, present: false };
        const rect = element.getBoundingClientRect();
        const computed = getComputedStyle(element);
        return {
          name,
          present: true,
          rect: {
            x: rect.x,
            y: rect.y,
            width: rect.width,
            height: rect.height,
            right: rect.right,
            bottom: rect.bottom,
          },
          style: {
            fontFamily: computed.fontFamily,
            fontSize: computed.fontSize,
            lineHeight: computed.lineHeight,
            color: computed.color,
            backgroundColor: computed.backgroundColor,
            borderRadius: computed.borderRadius,
            gap: computed.gap,
          },
        };
      });
      return {
        client: clientName,
        surface: surfaceName,
        url: location.pathname + location.search,
        viewport: {
          width: innerWidth,
          height: innerHeight,
          dpr: devicePixelRatio,
        },
        document: {
          width: document.documentElement.scrollWidth,
          height: document.documentElement.scrollHeight,
          horizontalOverflow:
            document.documentElement.scrollWidth > innerWidth + 1,
        },
        root: {
          x: box.x,
          y: box.y,
          width: box.width,
          height: box.height,
          fontFamily: style.fontFamily,
          fontSize: style.fontSize,
          lineHeight: style.lineHeight,
          color: style.color,
          backgroundColor: style.backgroundColor,
        },
        theme: { ...document.documentElement.dataset },
        fontStatus: document.fonts.status,
        focusOrder: focus,
        anchors,
      };
    },
    { clientName: client, surfaceName: surface },
  );
}

async function capture(page, testInfo, client, surface) {
  const file = testInfo.outputPath(`${surface}--${client}.png`);
  await page.screenshot({
    path: file,
    fullPage: false,
    animations: "disabled",
  });
  const measurement = await metrics(page, client, surface);
  expect(
    measurement.document.horizontalOverflow,
    `${client} ${surface} overflow`,
  ).toBe(false);
  await writeJson(testInfo, `${surface}-${client}-measurements`, measurement);
  const axe = await new AxeBuilder({ page }).exclude("iframe").analyze();
  await writeJson(testInfo, `${surface}-${client}-axe`, {
    url: new URL(page.url()).pathname,
    violations: axe.violations,
    passes: axe.passes.length,
    incomplete: axe.incomplete,
  });
  if (client === "react") {
    expect(
      axe.violations.filter((item) => item.impact === "critical"),
      `React ${surface} must have no critical axe violation`,
    ).toEqual([]);
  }
}

test.beforeEach(async ({ context, page }, testInfo) => {
  const mode = testInfo.project.metadata.captureState ?? "primary";
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === origin || ["data:", "blob:"].includes(url.protocol)) {
      await route.continue();
      return;
    }
    await route.abort("blockedbyclient");
  });
  await page.addInitScript((captureMode) => {
    localStorage.setItem(
      "row-bot.appearance.v1",
      JSON.stringify({
        version: 1,
        appearance: captureMode === "light" ? "light" : "dark",
        accent: "blue",
        density: captureMode === "light" ? "comfortable" : "compact",
        reduce_transparency: captureMode === "contrast",
      }),
    );
  }, mode);
  if (mode === "contrast") {
    await page.emulateMedia({
      colorScheme: "dark",
      reducedMotion: "reduce",
      forcedColors: "active",
    });
  }
});

test("paired core surfaces share one deterministic fixture and remain observable", async ({
  page,
}, testInfo) => {
  testInfo.setTimeout(
    testInfo.project.metadata.captureState === "primary" ? 300_000 : 180_000,
  );
  const consoleErrors = [];
  const pageErrors = [];
  const requestErrors = [];
  const lifecycleAborts = [];
  const external = [];
  const sockets = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) =>
    pageErrors.push(`${error.name}: ${error.message}`),
  );
  page.on("requestfailed", (request) => {
    const url = new URL(request.url());
    if (url.origin !== origin) external.push(url.origin + url.pathname);
    else if (/aborted|cancelled/i.test(request.failure()?.errorText ?? ""))
      lifecycleAborts.push(
        `${request.method()} ${url.pathname}: ${request.failure()?.errorText}`,
      );
    else
      requestErrors.push(
        `${request.method()} ${url.pathname}: ${request.failure()?.errorText}`,
      );
  });
  page.on("websocket", (socket) => {
    const url = new URL(socket.url());
    sockets.push({
      event: "open",
      origin: httpOrigin(url.href),
      path: url.pathname,
    });
    socket.on("close", () =>
      sockets.push({
        event: "close",
        origin: httpOrigin(url.href),
        path: url.pathname,
      }),
    );
    socket.on("socketerror", (error) =>
      sockets.push({
        event: "error",
        origin: httpOrigin(url.href),
        path: url.pathname,
        error,
      }),
    );
  });

  const fixture = await page.request.get("/__core_parity_fixture/state", {
    headers: { "X-Fixture-Token": token, Origin: origin },
  });
  expect(fixture.ok(), await fixture.text()).toBe(true);
  expect(await fixture.json()).toMatchObject({
    revision: "react-default-rich-parity-v3",
    external_calls: 0,
    data_scope: "disposable",
  });

  const mode = testInfo.project.metadata.captureState ?? "primary";
  const targets = mode === "primary" ? surfaceNames : ["workflows", "chat"];
  for (const surface of targets) {
    await openNiceGui(page, surface);
    await capture(page, testInfo, "nicegui", surface);
    await openReact(page, surface);
    await capture(page, testInfo, "react", surface);
  }

  if (testInfo.project.metadata.viewportName === "desktop") {
    for (const client of ["nicegui", "react"]) {
      await (client === "nicegui"
        ? openNiceGui(page, "workflows")
        : openReact(page, "workflows"));
      await page.keyboard.press("Tab");
      await page.keyboard.press("Tab");
      await writeJson(testInfo, `keyboard-${client}`, {
        active: await page.evaluate(() => ({
          tag: document.activeElement?.tagName.toLowerCase(),
          label:
            document.activeElement?.getAttribute("aria-label") ||
            document.activeElement?.textContent?.trim().slice(0, 120) ||
            "",
        })),
      });
      await page.evaluate(() => {
        document.documentElement.style.zoom = "2";
      });
      const zoom = await metrics(page, client, "workflows-200-percent");
      expect(zoom.document.horizontalOverflow).toBe(false);
      await writeJson(testInfo, `workflows-${client}-200-percent`, zoom);
      await page.screenshot({
        path: testInfo.outputPath(`workflows-200-percent--${client}.png`),
        fullPage: false,
        animations: "disabled",
      });
    }
  }

  await writeJson(testInfo, "runtime-observations", {
    consoleErrors,
    pageErrors,
    requestErrors,
    lifecycleAborts,
    external,
    sockets,
  });
  const expectedExpiredResourceErrors = consoleErrors.filter(
    (message) =>
      message ===
      "Failed to load resource: the server responded with a status of 404 (Not Found)",
  );
  expect(
    expectedExpiredResourceErrors,
    "The seeded expired attachment must exercise exactly one handled 404",
  ).toHaveLength(1);
  expect(
    consoleErrors.filter(
      (message) => !expectedExpiredResourceErrors.includes(message),
    ),
    "Unexpected browser console errors",
  ).toEqual([]);
  const expectedSandboxPageErrors = pageErrors.filter((message) =>
    [
      "SecurityError: Failed to read the 'serviceWorker' property from 'Navigator': Service worker is disabled because the context is sandboxed and lacks the 'allow-same-origin' flag.",
      "SecurityError: Failed to read the 'localStorage' property from 'Window': The document is sandboxed and lacks the 'allow-same-origin' flag.",
    ].includes(message),
  );
  expect(expectedSandboxPageErrors.length).toBeLessThanOrEqual(4);
  expect(
    pageErrors.filter(
      (message) => !expectedSandboxPageErrors.includes(message),
    ),
    "Unexpected uncaught page errors",
  ).toEqual([]);
  expect(requestErrors, "Unexpected same-origin request failures").toEqual([]);
  expect(external, "Off-origin requests are forbidden").toEqual([]);
  expect(
    sockets.every((socket) => socket.origin === origin),
    "Only same-origin sockets are allowed",
  ).toBe(true);
  expect(sockets.filter((socket) => socket.event === "error")).toEqual([]);

  const finalFixture = await page.request.get("/__core_parity_fixture/state", {
    headers: { "X-Fixture-Token": token, Origin: origin },
  });
  expect(finalFixture.ok()).toBe(true);
  expect(await finalFixture.json()).toMatchObject({ external_calls: 0 });
});

test("React rich transcript, durable media, context and functional graph remain local and recoverable", async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== "core-desktop",
    "The complete rich-state matrix is captured once at the primary desktop viewport.",
  );

  await openReact(page, "workflows");
  await expect(
    page.getByRole("complementary", { name: "Buddy companion" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Buddy settings" }),
  ).toBeVisible();

  await openReact(page, "chat");
  const contextRail = page.getByRole("complementary", {
    name: "Conversation context",
  });
  await expect(contextRail).toBeVisible();
  await expect(
    contextRail.getByRole("heading", { name: "Resources" }),
  ).toBeVisible();
  await expect(
    contextRail.getByRole("heading", { name: "Agents" }),
  ).toBeVisible();
  await expect(
    contextRail.getByRole("heading", { name: "Utilities" }),
  ).toBeVisible();
  await expect(
    page
      .locator(".conversation-heading")
      .getByRole("button", { name: "Add resource" }),
  ).toHaveCount(0);
  await expect(page.locator(".resource-chips")).toHaveCount(0);
  await expect(
    contextRail.getByRole("button", {
      name: "Phase 1 workspace Developer",
    }),
  ).toBeVisible();
  await expect(
    contextRail.getByText(/changed|workspace/).first(),
  ).toBeVisible();
  await expect(
    contextRail.getByRole("button", {
      name: "Community Workshop Deck Design",
    }),
  ).toBeVisible();
  await expect(contextRail.getByText(/deck · 2 pages/)).toBeVisible();
  await expect(
    page.getByText("private model-only fixture context"),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Rich fixture" }),
  ).toBeVisible();
  await expect(page.getByRole("table")).toContainText("Durable blocks");
  await expect(page.getByText("python", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Copy code" }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Download code" }).first(),
  ).toBeVisible();
  await expect(page.locator(".rich-mermaid figcaption")).toHaveText(
    "Mermaid diagram",
  );
  await expect(page.locator(".rich-chart")).toContainText("Synthetic chart");
  await expect(page.locator(".rich-youtube iframe")).toHaveCount(0);
  await expect(page.getByText(/contacts YouTube/)).toBeVisible();

  for (const name of [
    "synthetic.png",
    "synthetic.wav",
    "synthetic.mp4",
    "synthetic.txt",
  ]) {
    await expect(page.getByText(name, { exact: true })).toBeVisible();
    await expect(page.getByRole("group", { name })).toBeVisible();
  }
  await expect(page.getByText("expired.png", { exact: true })).toBeVisible();
  await expect(
    page
      .locator("figure")
      .filter({ hasText: "expired.png" })
      .locator(".media-preview-error"),
  ).toContainText(/not found|unavailable|no longer available/i);
  await expect(page.locator(".context-meter")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("rich-content-matrix--react.png"),
    animations: "disabled",
  });

  await contextRail
    .getByRole("button", { name: "Phase 1 workspace Developer" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Repository & sandbox" }),
  ).toBeVisible();
  const contextLayout = await page.evaluate(() => {
    const transcript = document.querySelector(".chat-content");
    const rail = document.querySelector(".conversation-context-rail");
    const detail = document.querySelector(".panel-dock");
    const width = (element) =>
      element ? Math.round(element.getBoundingClientRect().width) : 0;
    return {
      transcriptWidth: width(transcript),
      railWidth: width(rail),
      detailWidth: width(detail),
      documentHorizontalOverflow:
        document.documentElement.scrollWidth >
        document.documentElement.clientWidth + 1,
    };
  });
  expect(contextLayout.transcriptWidth).toBeGreaterThanOrEqual(400);
  expect(contextLayout.railWidth).toBeGreaterThanOrEqual(288);
  expect(contextLayout.documentHorizontalOverflow).toBe(false);
  await writeJson(testInfo, "conversation-context-layout", contextLayout);
  await page.screenshot({
    path: testInfo.outputPath("context-developer-detail--react.png"),
    animations: "disabled",
  });
  await contextRail
    .getByRole("button", { name: "Community Workshop Deck Design" })
    .click();
  await expect(
    page.getByRole("region", { name: "Design preview" }),
  ).toBeVisible();
  await expect(page.getByText(/Slide 1 of 2/)).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("context-designer-detail--react.png"),
    animations: "disabled",
  });

  await openReact(page, "chat-traces");
  const mediaGroup = page.locator(".trace-group").filter({
    has: page.getByText(/Done fixture_image/),
  });
  await mediaGroup.locator(":scope > summary").click();
  const mediaItems = mediaGroup.locator(".trace-item");
  await expect(mediaItems).toHaveCount(2);
  await mediaItems.nth(0).locator(":scope > summary").click();
  await mediaItems.nth(1).locator(":scope > summary").click();
  await expect(mediaGroup.getByAltText("Generated result")).toBeVisible();
  await expect(
    mediaGroup.getByText(/generated media is unavailable/i),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("durable-media-traces--react.png"),
    animations: "disabled",
  });

  await openReact(page, "knowledge");
  await expect(
    page.getByRole("img", { name: /Interactive knowledge graph/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Zoom in" }).click();
  await page.getByRole("button", { name: "Zoom out" }).click();
  await page.getByRole("button", { name: "Fit" }).click();
  await expect(
    page.getByLabel("Knowledge graph semantic fallback"),
  ).toBeAttached();
  const graphMeasurements = [];
  for (let index = 0; index < 25; index += 1) {
    await page.setViewportSize({
      width: index % 2 ? 1260 : 1280,
      height: index % 3 ? 720 : 740,
    });
    const hideOrphans = page.getByRole("checkbox", { name: "Hide orphans" });
    await hideOrphans.setChecked(index % 2 === 0);
    await page.getByRole("button", { name: "Fit" }).click();
    await settle(page);
    graphMeasurements.push(
      await page.locator(".knowledge-network-shell").evaluate((shell) => {
        const canvas = shell.querySelector(".knowledge-network-canvas");
        return {
          shellClientHeight: shell.clientHeight,
          shellOffsetHeight: shell.offsetHeight,
          shellScrollHeight: shell.scrollHeight,
          canvasClientHeight: canvas?.clientHeight ?? 0,
          canvasCount: shell.querySelectorAll(".knowledge-network-canvas")
            .length,
          rendererCount: shell.querySelectorAll(".vis-network").length,
        };
      }),
    );
  }
  for (const sample of graphMeasurements) {
    expect(sample.shellClientHeight).toBeGreaterThanOrEqual(360);
    expect(sample.shellClientHeight).toBeLessThanOrEqual(620);
    expect(sample.shellScrollHeight).toBeLessThanOrEqual(
      sample.shellClientHeight + 1,
    );
    expect(sample.canvasClientHeight).toBe(sample.shellClientHeight);
    expect(sample.canvasCount).toBe(1);
    expect(sample.rendererCount).toBeLessThanOrEqual(1);
  }
  expect(
    Math.max(...graphMeasurements.map((sample) => sample.shellClientHeight)) -
      Math.min(...graphMeasurements.map((sample) => sample.shellClientHeight)),
  ).toBeLessThanOrEqual(20);
  await writeJson(testInfo, "knowledge-graph-stability", {
    cycles: graphMeasurements.length,
    samples: graphMeasurements,
  });
  await page.screenshot({
    path: testInfo.outputPath("functional-knowledge-graph--react.png"),
    animations: "disabled",
  });
});

test("React context rail and one detail panel retain bounded desktop widths", async ({
  page,
}, testInfo) => {
  test.skip(
    !["core-desktop", "core-laptop"].includes(testInfo.project.name),
    "Desktop context composition is measured at 1440 and 1280 only.",
  );
  await openReact(page, "chat");
  const rail = page.getByRole("complementary", {
    name: "Conversation context",
  });
  await rail
    .getByRole("button", { name: "Phase 1 workspace Developer" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Repository & sandbox" }),
  ).toBeVisible();
  const layout = await page.evaluate(() => {
    const width = (query) => {
      const element = document.querySelector(query);
      return element ? Math.round(element.getBoundingClientRect().width) : 0;
    };
    return {
      viewportWidth: window.innerWidth,
      transcriptWidth: width(".chat-content"),
      railWidth: width(".conversation-context-rail"),
      documentHorizontalOverflow:
        document.documentElement.scrollWidth >
        document.documentElement.clientWidth + 1,
    };
  });
  expect(layout.transcriptWidth).toBeGreaterThanOrEqual(400);
  expect(layout.railWidth).toBeGreaterThanOrEqual(288);
  expect(layout.documentHorizontalOverflow).toBe(false);
  await writeJson(testInfo, "context-detail-widths", layout);
});

test("React compact context affordance opens the same accessible content", async ({
  page,
}, testInfo) => {
  test.skip(
    !["core-tablet", "core-phone", "core-narrow"].includes(
      testInfo.project.name,
    ),
    "The compact context transform is measured on tablet and phone projects.",
  );
  await openReact(page, "chat");
  await expect(page.locator(".conversation-context-rail")).toHaveCount(0);
  const trigger = page.getByRole("button", { name: "Context", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Conversation context" });
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole("complementary", { name: "Conversation context" }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Phase 1 workspace Developer" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("paired chat composer, Buddy, trace, stream, stop and reconnect remain one shared runtime", async ({
  context,
  page: nicegui,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== "core-desktop",
    "One shared deterministic generation sequence is captured by the primary desktop project.",
  );
  const react = await context.newPage();
  const timeline = [];
  const started = Date.now();
  const mark = async (event, details = {}) => {
    timeline.push({
      event,
      elapsed_ms: Date.now() - started,
      ...details,
    });
  };
  const fixtureState = async () => {
    const response = await react.request.get("/__p1_fixture/state", {
      headers: { "X-Fixture-Token": token, Origin: origin },
    });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  const release = async (barrierId) => {
    const response = await react.request.post(
      `/__p1_fixture/release/${barrierId}`,
      { headers: { "X-Fixture-Token": token, Origin: origin } },
    );
    expect(response.ok(), await response.text()).toBe(true);
  };

  await openNiceGui(nicegui, "chat");
  await openReact(react, "chat");
  await expect(nicegui.locator("[data-buddy-in-app-shell]")).toBeVisible();
  await expect(
    react.getByRole("complementary", { name: "Buddy companion" }),
  ).toBeVisible();
  await expect(react.getByRole("button", { name: "Talk" })).toBeVisible();
  await expect(react.getByRole("button", { name: "Dictate" })).toBeVisible();
  await expect(react.getByRole("button", { name: "Send" })).toBeVisible();
  await mark("initial-shared-chat");

  const draft = react.getByRole("textbox", { name: "Message", exact: true });
  await draft.fill("/status");
  const palette = react.getByRole("listbox", { name: "Slash commands" });
  await expect(palette).toBeVisible();
  expect(await palette.getByRole("option").count()).toBeGreaterThanOrEqual(1);
  await draft.press("ArrowDown");
  await draft.press("ArrowUp");
  await draft.press("Enter");
  await expect(react.getByRole("dialog", { name: "Status" })).toBeVisible();
  await react.getByRole("button", { name: "Close", exact: true }).click();
  await mark("canonical-slash-status");

  await react.getByRole("button", { name: /Skills: \d+ active/ }).click();
  const skills = react.getByRole("dialog", { name: "Smart Skills" });
  await expect(skills).toBeVisible();
  await skills.getByRole("button", { name: /Synthetic browser skill/ }).click();
  await expect(
    react.getByRole("button", {
      name: "Remove Synthetic browser skill from this chat",
    }),
  ).toBeVisible();
  await nicegui.reload();
  await openNiceGui(nicegui, "chat");
  await nicegui.getByRole("button", { name: "Skills", exact: true }).click();
  await expect(
    nicegui.getByText("Synthetic browser skill", { exact: false }),
  ).toBeVisible();
  await nicegui.keyboard.press("Escape");
  await react.reload();
  await expect(
    react.getByRole("button", {
      name: "Remove Synthetic browser skill from this chat",
    }),
  ).toBeVisible();
  await mark("skill-activation-reload");

  await draft.fill("rich fixture");
  await react.getByRole("button", { name: "Send" }).click();
  await expect(react.getByRole("button", { name: "Stop" })).toBeVisible();
  await expect(
    react.getByText("Synthetic tools and media are ready.", { exact: false }),
  ).toBeVisible();
  await expect(
    nicegui.getByText("Synthetic tools and media are ready.", { exact: false }),
  ).toBeVisible();
  await expect(react.getByText(/Using fixture_image/)).toBeVisible();
  await mark("tool-stream-pending");
  await nicegui.screenshot({
    path: testInfo.outputPath("chat-stream-pending--nicegui.png"),
    animations: "disabled",
  });
  await react.screenshot({
    path: testInfo.outputPath("chat-stream-pending--react.png"),
    animations: "disabled",
  });
  const pendingState = await fixtureState();
  const richCall = pendingState.calls.findLast(
    (call) => call.case === "tools-media" && !call.quiesced,
  );
  expect(richCall).toBeTruthy();
  await release(richCall.barrier_id);
  await expect(react.getByRole("button", { name: "Send" })).toBeVisible();
  await expect(react.getByText(/Done fixture_image/)).toBeVisible();
  await expect(
    nicegui.getByRole("button", { name: /Done fixture_image · 1 call/ }),
  ).toBeVisible();
  await mark("tool-stream-settled");

  await react.reload();
  await expect(react.getByText(/Done fixture_image/)).toBeVisible();
  await expect(
    react.getByText("Synthetic tools and media are ready.", { exact: false }),
  ).toHaveCount(1);
  await mark("reload-reconciled");

  const reloadedDraft = react.getByRole("textbox", {
    name: "Message",
    exact: true,
  });
  await reloadedDraft.fill("burst fixture");
  await react.getByRole("button", { name: "Send" }).click();
  await expect(
    react.getByText("Burst complete; waiting for Stop.", { exact: false }),
  ).toBeVisible();
  await react.getByRole("button", { name: "Stop" }).click();
  await expect(react.getByRole("button", { name: "Send" })).toBeVisible();
  await mark("stop-quiesced");

  await context.setOffline(true);
  await expect(react.getByText(/Reconnecting|Disconnected/)).toBeVisible();
  await mark("offline");
  await context.setOffline(false);
  await expect(react.locator(".connection-status")).toContainText("Connected");
  await expect(react.getByText("burst fixture", { exact: true })).toHaveCount(
    1,
  );
  await expect(react.getByText("Work stopped.", { exact: true })).toBeVisible();
  await expect(
    react.getByText("Burst complete; waiting for Stop.", { exact: false }),
  ).toHaveCount(0);
  await mark("reconnected");

  await reloadedDraft.fill("approval fixture");
  await react.getByRole("button", { name: "Send" }).click();
  const approval = react.locator(".approval-bar");
  await expect(approval).toContainText("Approve the synthetic fixture action");
  await expect(
    approval.getByRole("button", { name: "Approve", exact: true }),
  ).toBeVisible();
  await expect(
    approval.getByRole("button", { name: "Reject", exact: true }),
  ).toBeVisible();
  await approval.getByRole("button", { name: "Details", exact: true }).click();
  await expect(
    react.getByRole("dialog", { name: "Approval details" }),
  ).toBeVisible();
  await react.getByRole("button", { name: "Close", exact: true }).click();
  await nicegui.reload();
  await openNiceGui(nicegui, "chat");
  await expect(
    nicegui.getByText("approval fixture", { exact: true }),
  ).toBeVisible();
  await nicegui.screenshot({
    path: testInfo.outputPath("approval-pending--nicegui.png"),
    animations: "disabled",
  });
  await react.screenshot({
    path: testInfo.outputPath("approval-pending--react.png"),
    animations: "disabled",
  });
  await approval.getByRole("button", { name: "Reject", exact: true }).click();
  await expect(
    react.getByText("Synthetic approval rejected.", { exact: true }),
  ).toBeVisible();
  await nicegui.reload();
  await openNiceGui(nicegui, "chat");
  await expect(
    nicegui.getByText("Synthetic approval rejected.", { exact: true }),
  ).toBeVisible();
  await mark("approval-rejected-and-converged");

  await openReact(react, "chat-traces");
  await expect(
    react.getByText(/Needs attention fixture_failure/),
  ).toBeVisible();
  await expect(react.getByText(/Needs attention fixture_policy/)).toBeVisible();
  await expect(react.getByText(/Needs attention fixture_cancel/)).toBeVisible();
  await expect(
    react.getByText(/Needs attention fixture_receipt/),
  ).toBeVisible();
  await expect(react.getByText(/Using Computer activity/)).toBeVisible();
  await expect(react.getByText(/Done fixture_repeat · 2/)).toBeVisible();
  await mark("settled-trace-matrix");
  await openNiceGui(nicegui, "chat-traces");
  await capture(nicegui, testInfo, "nicegui", "chat-live-final");
  await capture(react, testInfo, "react", "chat-live-final");
  await writeJson(testInfo, "chat-stream-timeline", timeline);

  const final = await fixtureState();
  expect(final.external_calls).toBe(0);
  expect(
    final.calls.filter((call) => call.case === "tools-media"),
  ).toHaveLength(1);
  expect(final.calls.filter((call) => call.case === "burst")).toHaveLength(1);
  expect(final.calls.filter((call) => call.case === "approval")).toHaveLength(
    2,
  );
  await react.close();
});
