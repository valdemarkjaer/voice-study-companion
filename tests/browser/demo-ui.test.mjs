import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

const require = createRequire(import.meta.url);
const { chromium, webkit } = require("playwright");
const projectRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

async function startDemoServer() {
  const process = spawn(
    "python3",
    ["-u", "-m", "voice_study_companion.server", "--port", "0"],
    {
      cwd: projectRoot,
      env: {
        ...globalThis.process.env,
        PYTHONDONTWRITEBYTECODE: "1",
        PYTHONPATH: "src",
        VSC_MODE: "demo",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let stderr = "";
  process.stderr.setEncoding("utf8");
  process.stderr.on("data", (chunk) => {
    stderr += chunk;
  });
  const url = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error(`demo server did not start: ${stderr}`));
    }, 5_000);
    process.stdout.setEncoding("utf8");
    process.stdout.on("data", (chunk) => {
      const match = chunk.match(/http:\/\/127\.0\.0\.1:(\d+)/u);
      if (match) {
        clearTimeout(timeout);
        resolve(match[0]);
      }
    });
    process.once("exit", (code) => {
      clearTimeout(timeout);
      reject(new Error(`demo server exited with ${code}: ${stderr}`));
    });
  });
  return { process, url };
}

async function stopDemoServer(server) {
  if (server.process.exitCode !== null) return;
  server.process.kill("SIGINT");
  await new Promise((resolve) => {
    const timeout = setTimeout(() => {
      server.process.kill("SIGKILL");
    }, 2_000);
    server.process.once("exit", () => {
      clearTimeout(timeout);
      resolve();
    });
  });
}

const viewports = {
  phone: { width: 390, height: 844 },
  tablet: { width: 820, height: 1_180 },
  desktop: { width: 1_440, height: 900 },
};

test("credential-free UI walkthrough is responsive in Chromium and WebKit", async (t) => {
  const server = await startDemoServer();
  t.after(() => stopDemoServer(server));

  for (const [browserName, browserType] of Object.entries({ chromium, webkit })) {
    await t.test(browserName, async (browserTest) => {
      const browser = await browserType.launch({ headless: true });
      browserTest.after(() => browser.close());

      for (const [viewportName, viewport] of Object.entries(viewports)) {
        await browserTest.test(viewportName, async () => {
          const page = await browser.newPage({ viewport });
          const errors = [];
          await page.addInitScript(() => {
            globalThis.__voiceStudyPlayedMedia = [];
            HTMLMediaElement.prototype.play = function play() {
              globalThis.__voiceStudyPlayedMedia.push(this.src);
              return Promise.resolve();
            };
          });
          page.on("console", (message) => {
            if (message.type() === "error") errors.push(message.text());
          });
          await page.goto(server.url, { waitUntil: "networkidle" });

          assert.equal(
            await page.locator(".brand strong").textContent(),
            "Voice Study Companion",
          );
          assert.equal(await page.locator("#official-answer").isHidden(), true);
          assert.ok(
            await page.evaluate(() => document.body.scrollWidth <= innerWidth),
            `${browserName}/${viewportName} has horizontal overflow before evaluation`,
          );

          await page.locator("#fake-microphone").click();
          await page.waitForFunction(
            () => document.querySelector("#answer-input").value.trim().length > 0,
          );
          await page.locator("#evaluate-answer").click();
          await page.locator("#evaluation:not([hidden])").waitFor();
          assert.equal(
            await page.locator("#evaluation-title").textContent(),
            "Correto",
          );
          assert.equal(await page.locator("#official-answer").isHidden(), true);
          await page.locator("#request-official-answer").click();
          await page.locator("#official-answer:not([hidden])").waitFor();
          assert.match(
            await page.locator("#official-answer-copy").textContent(),
            /^Latency/u,
          );
          await page.waitForFunction(() =>
            globalThis.__voiceStudyPlayedMedia.some((url) =>
              url.endsWith("/official-answer.wav"),
            ),
          );
          await page.locator("#next-card").click();
          await page.getByRole("heading", { name: "Cartão 2" }).waitFor();
          assert.equal(await page.locator("#official-answer").isHidden(), true);
          await page.locator("#close-session").click();
          await page.waitForFunction(
            () => document.querySelector("#phase-kicker").textContent === "Sessão encerrada",
          );
          assert.ok(
            await page.evaluate(() => document.body.scrollWidth <= innerWidth),
            `${browserName}/${viewportName} has horizontal overflow after evaluation`,
          );
          assert.deepEqual(errors, []);
          await page.close();
        });
      }
    });
  }
});
