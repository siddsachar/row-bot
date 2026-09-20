import { defineConfig } from "../../../frontend/node_modules/@playwright/test/index.mjs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const baseURL = process.env.ROW_BOT_BROWSER_BASE_URL;
if (
  !baseURL ||
  !["127.0.0.1", "localhost", "[::1]"].includes(new URL(baseURL).hostname)
) {
  throw new Error("Core-surface parity requires the isolated loopback runner");
}
const evidence = path.resolve(process.env.ROW_BOT_BROWSER_EVIDENCE);
const channel = process.env.ROW_BOT_BROWSER_CHANNEL;
const launchOptions = {
  args: [
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
  ],
};

const primary = [
  ["desktop", 1440, 900, false],
  ["laptop", 1280, 720, false],
  ["tablet", 820, 1180, true],
  ["phone", 390, 844, true],
  ["narrow", 360, 800, true],
];
const secondary = [
  ["light-desktop", 1440, 900, false, "light"],
  ["light-phone", 390, 844, true, "light"],
  ["contrast-desktop", 1440, 900, false, "contrast"],
  ["contrast-phone", 390, 844, true, "contrast"],
];

function project([name, width, height, touch, state = "primary"]) {
  return {
    name: `core-${name}`,
    metadata: { captureState: state, viewportName: name },
    use: {
      browserName: "chromium",
      ...(channel ? { channel } : {}),
      viewport: { width, height },
      hasTouch: touch,
      isMobile: touch,
      launchOptions,
      colorScheme: state === "light" ? "light" : "dark",
      reducedMotion: state === "contrast" ? "reduce" : "no-preference",
    },
  };
}

export default defineConfig({
  testDir: here,
  testMatch: /core-surface\.spec\.mjs/,
  outputDir: path.join(evidence, "artifacts"),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [
    ["list"],
    ["json", { outputFile: path.join(evidence, "playwright-results.json") }],
  ],
  use: {
    baseURL,
    locale: "en-GB",
    timezoneId: "UTC",
    deviceScaleFactor: 1,
    serviceWorkers: "block",
    trace: "off",
    video: "off",
    screenshot: "only-on-failure",
  },
  projects: [...primary.map(project), ...secondary.map(project)],
});
