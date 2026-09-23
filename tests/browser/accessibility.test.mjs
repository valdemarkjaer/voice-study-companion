import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const projectRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

const viewports = {
  phone: { width: 390, height: 844 },
  tablet: { width: 820, height: 1_180 },
  desktop: { width: 1_440, height: 900 },
};

// Release policy: findings analogous to axe-core's critical/serious impacts
// against WCAG A or AA block the candidate. Moderate/minor observations may
// still be reported, but do not fail this gate.
const BLOCKING_IMPACTS = new Set(["critical", "serious"]);
const RELEASE_THRESHOLD = Object.freeze({
  standards: ["WCAG 2.2 A", "WCAG 2.2 AA"],
  blockingImpacts: [...BLOCKING_IMPACTS],
});

async function startDemoServer() {
  const child = spawn(
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
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });
  const url = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error(`demo server did not start: ${stderr}`));
    }, 5_000);
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      const match = chunk.match(/http:\/\/127\.0\.0\.1:(\d+)/u);
      if (match) {
        clearTimeout(timeout);
        resolve(match[0]);
      }
    });
    child.once("exit", (code) => {
      clearTimeout(timeout);
      reject(new Error(`demo server exited with ${code}: ${stderr}`));
    });
  });
  return { child, url };
}

async function stopDemoServer(server) {
  if (server.child.exitCode !== null) return;
  server.child.kill("SIGINT");
  await new Promise((resolve) => {
    const timeout = setTimeout(() => server.child.kill("SIGKILL"), 2_000);
    server.child.once("exit", () => {
      clearTimeout(timeout);
      resolve();
    });
  });
}

function addFinding(findings, rule, impact, wcag, target, message) {
  findings.push({ rule, impact, wcag, target, message });
}

function assertReleaseThreshold(findings, label) {
  const blockers = findings.filter((finding) =>
    BLOCKING_IMPACTS.has(finding.impact),
  );
  assert.deepEqual(
    blockers,
    [],
    `${label} failed accessibility release threshold ${JSON.stringify(RELEASE_THRESHOLD)}:\n${blockers
      .map(
        ({ rule, impact, wcag, target, message }) =>
          `- [${impact}] ${rule} (${wcag}) ${target}: ${message}`,
      )
      .join("\n")}`,
  );
}

test("release threshold blocks serious and critical findings", () => {
  const sample = (impact) => ({
    rule: "threshold-sentinel",
    impact,
    wcag: "4.1.2 A",
    target: "#sentinel",
    message: "synthetic policy assertion",
  });
  assert.doesNotThrow(() => assertReleaseThreshold([sample("moderate")], "sentinel"));
  assert.throws(
    () => assertReleaseThreshold([sample("serious")], "sentinel"),
    /failed accessibility release threshold/u,
  );
  assert.throws(
    () => assertReleaseThreshold([sample("critical")], "sentinel"),
    /failed accessibility release threshold/u,
  );
});

async function expectUniqueRole(
  page,
  findings,
  role,
  name,
  { rule = "name-role-value", wcag = "4.1.2 A" } = {},
) {
  const locator = page.getByRole(role, { name, exact: true });
  const count = await locator.count();
  if (count !== 1) {
    addFinding(
      findings,
      rule,
      "serious",
      wcag,
      `${role}[name=${JSON.stringify(name)}]`,
      `expected exactly one visible semantic target; found ${count}`,
    );
  }
  return locator;
}

async function focusedTarget(page) {
  return page.evaluate(() => {
    const active = document.activeElement;
    if (!active) return "<none>";
    if (active.id) return `#${active.id}`;
    if (active.classList.contains("skip-link")) return ".skip-link";
    if (active.classList.contains("brand")) return ".brand";
    if (active.matches('input[name="study-mode"]')) {
      return `input[value="${active.value}"]`;
    }
    return active.tagName.toLowerCase();
  });
}

async function tabTo(page, findings, expected, context) {
  await page.keyboard.press("Tab");
  const actual = await focusedTarget(page);
  if (actual !== expected) {
    addFinding(
      findings,
      "focus-order",
      "serious",
      "2.4.3 A",
      context,
      `expected ${expected} after Tab; focused ${actual}`,
    );
  }
  return actual;
}

async function inspectFocusIndicator(page, findings, target) {
  const result = await page.evaluate(() => {
    function parseColor(value) {
      const channels = value.match(/[\d.]+/gu)?.map(Number) || [];
      if (channels.length < 3) return null;
      return [channels[0], channels[1], channels[2], channels[3] ?? 1];
    }

    function composite(foreground, background) {
      const alpha = foreground[3] + background[3] * (1 - foreground[3]);
      if (alpha === 0) return [0, 0, 0, 0];
      return [
        (foreground[0] * foreground[3] +
          background[0] * background[3] * (1 - foreground[3])) /
          alpha,
        (foreground[1] * foreground[3] +
          background[1] * background[3] * (1 - foreground[3])) /
          alpha,
        (foreground[2] * foreground[3] +
          background[2] * background[3] * (1 - foreground[3])) /
          alpha,
        alpha,
      ];
    }

    function backgroundFor(element) {
      let result = [0, 0, 0, 0];
      for (let current = element; current; current = current.parentElement) {
        const color = parseColor(getComputedStyle(current).backgroundColor);
        if (color) result = composite(result, color);
        if (result[3] >= 0.999) break;
      }
      return composite(result, [255, 255, 255, 1]);
    }

    function luminance(color) {
      const linear = color.slice(0, 3).map((channel) => {
        const value = channel / 255;
        return value <= 0.04045
          ? value / 12.92
          : ((value + 0.055) / 1.055) ** 2.4;
      });
      return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
    }

    function contrast(first, second) {
      const firstLuminance = luminance(first);
      const secondLuminance = luminance(second);
      return (
        (Math.max(firstLuminance, secondLuminance) + 0.05) /
        (Math.min(firstLuminance, secondLuminance) + 0.05)
      );
    }

    const active = document.activeElement;
    const style = getComputedStyle(active);
    const outline = parseColor(style.outlineColor);
    const surrounding = backgroundFor(active.parentElement || document.body);
    const rect = active.getBoundingClientRect();
    return {
      outlineStyle: style.outlineStyle,
      outlineWidth: Number.parseFloat(style.outlineWidth),
      contrast: outline ? contrast(outline, surrounding) : 0,
      visible:
        rect.width > 0 &&
        rect.height > 0 &&
        rect.bottom > 0 &&
        rect.right > 0 &&
        rect.top < innerHeight &&
        rect.left < innerWidth,
    };
  });

  if (
    result.outlineStyle === "none" ||
    result.outlineWidth < 2 ||
    result.contrast < 3 ||
    !result.visible
  ) {
    addFinding(
      findings,
      "focus-visible",
      "serious",
      "2.4.7 AA; 2.4.11 AA",
      target,
      `indicator visible=${result.visible}, style=${result.outlineStyle}, width=${result.outlineWidth}px, contrast=${result.contrast.toFixed(2)}:1`,
    );
  }
}

async function inspectNamesAndRoles(page, findings) {
  const expected = [
    ["main", ""],
    ["complementary", "Pense em voz alta."],
    ["region", "Área de estudo"],
    ["navigation", "Controles da sessão"],
    ["status", ""],
    ["link", "Voice Study Companion, início"],
    ["button", "Sobre a demonstração"],
    ["radio", "Completo Texto, imagem e áudio sintéticos"],
    ["radio", "Em trânsito Seleciona apenas cartões de texto"],
    ["textbox", "Sua resposta"],
    ["button", "Simular resposta por voz"],
    ["button", "Avaliar resposta"],
    ["button", "Encerrar e sincronizar"],
    ["button", "Próximo cartão"],
  ];

  for (const [role, name] of expected) {
    const options = name ? { name, exact: true } : {};
    const count = await page.getByRole(role, options).count();
    if (count !== 1) {
      addFinding(
        findings,
        "name-role-value",
        "serious",
        "4.1.2 A",
        name ? `${role}[name=${JSON.stringify(name)}]` : role,
        `expected exactly one semantic target; found ${count}`,
      );
    }
  }

  const unnamed = await page.evaluate(() => {
    function visible(element) {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        !element.hidden &&
        style.display !== "none" &&
        style.visibility !== "hidden" &&
        rect.width > 0 &&
        rect.height > 0
      );
    }

    function labelledBy(element) {
      return (element.getAttribute("aria-labelledby") || "")
        .split(/\s+/u)
        .filter(Boolean)
        .map((id) => document.getElementById(id)?.textContent?.trim() || "")
        .join(" ")
        .trim();
    }

    function name(element) {
      const explicit = element.getAttribute("aria-label")?.trim();
      if (explicit) return explicit;
      const referenced = labelledBy(element);
      if (referenced) return referenced;
      if (element.labels?.length) {
        return [...element.labels]
          .map((label) => label.textContent?.trim() || "")
          .join(" ")
          .trim();
      }
      if (element instanceof HTMLImageElement) return element.alt.trim();
      return element.textContent?.trim() || element.title.trim();
    }

    const selector = [
      "a[href]",
      "button",
      "input:not([type=hidden])",
      "textarea",
      "select",
      "[role=button]",
      "[role=img]",
      "dialog[open]",
    ].join(",");
    return [...document.querySelectorAll(selector)]
      .filter(visible)
      .filter((element) => !name(element))
      .map((element) =>
        element.id ? `#${element.id}` : element.outerHTML.slice(0, 100),
      );
  });

  for (const target of unnamed) {
    addFinding(
      findings,
      "accessible-name",
      "serious",
      "4.1.2 A",
      target,
      "visible interactive or semantic element has no accessible name",
    );
  }
}

async function inspectTextContrast(page, findings, stateLabel) {
  const failures = await page.evaluate(() => {
    function parseColor(value) {
      const channels = value.match(/[\d.]+/gu)?.map(Number) || [];
      if (channels.length < 3) return null;
      return [channels[0], channels[1], channels[2], channels[3] ?? 1];
    }

    function composite(foreground, background) {
      const alpha = foreground[3] + background[3] * (1 - foreground[3]);
      if (alpha === 0) return [0, 0, 0, 0];
      return [
        (foreground[0] * foreground[3] +
          background[0] * background[3] * (1 - foreground[3])) /
          alpha,
        (foreground[1] * foreground[3] +
          background[1] * background[3] * (1 - foreground[3])) /
          alpha,
        (foreground[2] * foreground[3] +
          background[2] * background[3] * (1 - foreground[3])) /
          alpha,
        alpha,
      ];
    }

    function effectiveBackground(element) {
      let result = [0, 0, 0, 0];
      for (let current = element; current; current = current.parentElement) {
        const color = parseColor(getComputedStyle(current).backgroundColor);
        if (color) result = composite(result, color);
        if (result[3] >= 0.999) break;
      }
      return composite(result, [255, 255, 255, 1]);
    }

    function luminance(color) {
      const linear = color.slice(0, 3).map((channel) => {
        const value = channel / 255;
        return value <= 0.04045
          ? value / 12.92
          : ((value + 0.055) / 1.055) ** 2.4;
      });
      return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
    }

    function contrast(first, second) {
      const firstLuminance = luminance(first);
      const secondLuminance = luminance(second);
      return (
        (Math.max(firstLuminance, secondLuminance) + 0.05) /
        (Math.min(firstLuminance, secondLuminance) + 0.05)
      );
    }

    function visible(element) {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return (
        !element.hidden &&
        style.display !== "none" &&
        style.visibility !== "hidden" &&
        Number(style.opacity) > 0 &&
        rect.width > 0 &&
        rect.height > 0 &&
        !element.closest("[hidden],[aria-hidden=true]")
      );
    }

    function target(element, suffix = "") {
      if (element.id) return `#${element.id}${suffix}`;
      const classes = [...element.classList].slice(0, 2).join(".");
      return `${element.tagName.toLowerCase()}${classes ? `.${classes}` : ""}${suffix}`;
    }

    function measurement(element, style, suffix = "") {
      const foreground = parseColor(style.color);
      if (!foreground) return null;
      const background = effectiveBackground(element);
      const fontSize = Number.parseFloat(style.fontSize);
      const fontWeight = Number.parseInt(style.fontWeight, 10) || 400;
      const large = fontSize >= 24 || (fontSize >= 18.66 && fontWeight >= 700);
      const required = large ? 3 : 4.5;
      const actual = contrast(composite(foreground, background), background);
      if (actual + 0.001 >= required) return null;
      return {
        target: target(element, suffix),
        actual,
        required,
        fontSize,
        fontWeight,
      };
    }

    const results = [];
    for (const element of document.querySelectorAll("body *")) {
      if (!visible(element) || element.matches(":disabled")) continue;
      const hasDirectText = [...element.childNodes].some(
        (node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim(),
      );
      if (hasDirectText) {
        const result = measurement(element, getComputedStyle(element));
        if (result) results.push(result);
      }
    }
    const answer = document.querySelector("#answer-input");
    if (visible(answer) && !answer.value) {
      const result = measurement(
        answer,
        getComputedStyle(answer, "::placeholder"),
        "::placeholder",
      );
      if (result) results.push(result);
    }
    return results;
  });

  for (const failure of failures) {
    addFinding(
      findings,
      "color-contrast",
      "serious",
      "1.4.3 AA",
      `${stateLabel} ${failure.target}`,
      `${failure.actual.toFixed(2)}:1 is below ${failure.required}:1 at ${failure.fontSize}px/${failure.fontWeight}`,
    );
  }
}

async function inspectReducedMotion(page, findings) {
  const motion = await page.evaluate(() => {
    const wave = getComputedStyle(document.querySelector(".phase-icon span"));
    const progress = getComputedStyle(document.querySelector("#progress-bar"));
    return {
      mediaMatches: matchMedia("(prefers-reduced-motion: reduce)").matches,
      animationDuration: Number.parseFloat(wave.animationDuration) * 1_000,
      animationIterations: wave.animationIterationCount,
      transitionDuration: Number.parseFloat(progress.transitionDuration) * 1_000,
    };
  });
  if (
    !motion.mediaMatches ||
    motion.animationDuration > 10 ||
    motion.animationIterations !== "1" ||
    motion.transitionDuration > 10
  ) {
    addFinding(
      findings,
      "reduced-motion",
      "serious",
      "2.3.3 AAA / release policy",
      "prefers-reduced-motion",
      `matches=${motion.mediaMatches}, animation=${motion.animationDuration}ms x ${motion.animationIterations}, transition=${motion.transitionDuration}ms`,
    );
  }
}

async function installObservers(page) {
  await page.addInitScript(() => {
    globalThis.__voiceStudyPlayedMedia = [];
    globalThis.__voiceStudyScrollBehaviors = [];
    HTMLMediaElement.prototype.play = function play() {
      globalThis.__voiceStudyPlayedMedia.push(this.src);
      return Promise.resolve();
    };
    const originalScrollIntoView = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function scrollIntoView(options) {
      globalThis.__voiceStudyScrollBehaviors.push(
        typeof options === "object" ? options.behavior || "auto" : "auto",
      );
      return originalScrollIntoView.call(this, options);
    };
    addEventListener("DOMContentLoaded", () => {
      globalThis.__voiceStudyAnnouncements = [];
      const status = document.querySelector('[role="status"]');
      new MutationObserver(() => {
        globalThis.__voiceStudyAnnouncements.push(
          status.textContent.replace(/\s+/gu, " ").trim(),
        );
      }).observe(status, { subtree: true, childList: true, characterData: true });
    });
  });
}

async function exerciseKeyboardWalkthrough(page, findings, viewportName) {
  await page.evaluate(() => document.activeElement?.blur());
  if ((await tabTo(page, findings, ".skip-link", "first focus target")) !== ".skip-link") {
    await page.locator(".skip-link").focus();
  }
  await inspectFocusIndicator(page, findings, `${viewportName} skip link`);
  await page.keyboard.press("Enter");
  if ((await focusedTarget(page)) !== "#study-card") {
    addFinding(
      findings,
      "bypass-blocks",
      "serious",
      "2.4.1 A",
      ".skip-link",
      "activating the skip link did not move focus to the study card",
    );
  }

  // Reload so the browser's sequential-focus starting point is reset after the
  // skip-link target; blurring alone intentionally preserves that position.
  const baseUrl = await page.evaluate(() => location.origin);
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.evaluate(() => scrollTo(0, 0));
  await tabTo(page, findings, ".skip-link", "restart tab sequence");
  await tabTo(page, findings, ".brand", "header brand");
  if ((await tabTo(page, findings, "#help-button", "help action")) !== "#help-button") {
    await page.locator("#help-button").focus();
  }
  await inspectFocusIndicator(page, findings, `${viewportName} help button`);
  await page.keyboard.press("Enter");
  await page.locator("#about-dialog[open]").waitFor();
  const dialog = await expectUniqueRole(page, findings, "dialog", "Feito para ser inspecionável.");
  if ((await focusedTarget(page)) !== "#close-help") {
    addFinding(
      findings,
      "dialog-focus",
      "serious",
      "2.4.3 A",
      "#about-dialog",
      "opening the modal did not place focus on its close control",
    );
  }
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  if ((await focusedTarget(page)) !== "#help-button") {
    addFinding(
      findings,
      "dialog-focus-return",
      "serious",
      "2.4.3 A",
      "#about-dialog",
      "closing the modal did not restore focus to its trigger",
    );
  }

  await tabTo(page, findings, 'input[value="full"]', "session mode group");
  await tabTo(page, findings, "#answer-input", "answer control");
  await inspectFocusIndicator(page, findings, `${viewportName} answer field`);
  if (
    (await tabTo(page, findings, "#fake-microphone", "voice action")) !==
    "#fake-microphone"
  ) {
    await page.locator("#fake-microphone").focus();
  }
  await inspectFocusIndicator(page, findings, `${viewportName} voice action`);
  await page.keyboard.press("Enter");
  await page.waitForFunction(
    () => document.querySelector("#answer-input").value.trim().length > 0,
  );
  await page.waitForFunction(
    () => document.querySelector("#phase-kicker").textContent === "Transcrição pronta",
  );

  const liveRegion = await page.locator('[role="status"]').evaluate((element) => ({
    live: element.getAttribute("aria-live"),
    atomic: element.getAttribute("aria-atomic"),
  }));
  const announcements = await page.evaluate(
    () => globalThis.__voiceStudyAnnouncements,
  );
  if (
    liveRegion.live !== "polite" ||
    liveRegion.atomic !== "true" ||
    !announcements.some((value) => value.startsWith("Voz simulada")) ||
    !announcements.some((value) => value.startsWith("Transcrição pronta"))
  ) {
    addFinding(
      findings,
      "voice-status-announcement",
      "serious",
      "4.1.3 AA",
      '[role="status"]',
      `live=${liveRegion.live}, atomic=${liveRegion.atomic}, announcements=${JSON.stringify(announcements)}`,
    );
  }

  await tabTo(page, findings, "#fake-microphone", "focus returns after simulated transcription");
  if (
    (await tabTo(page, findings, "#evaluate-answer", "evaluation action")) !==
    "#evaluate-answer"
  ) {
    await page.locator("#evaluate-answer").focus();
  }
  await page.keyboard.press("Enter");
  await page.locator("#evaluation:not([hidden])").waitFor();
  if (
    (await tabTo(
      page,
      findings,
      "#request-official-answer",
      "protected answer action",
    )) !== "#request-official-answer"
  ) {
    await page.locator("#request-official-answer").focus();
  }
  await page.keyboard.press("Enter");
  await page.locator("#official-answer:not([hidden])").waitFor();

  const scrollBehaviors = await page.evaluate(
    () => globalThis.__voiceStudyScrollBehaviors,
  );
  if (scrollBehaviors.includes("smooth")) {
    addFinding(
      findings,
      "reduced-motion-script",
      "serious",
      "2.3.3 AAA / release policy",
      "#evaluation",
      `script requested smooth scrolling under reduced motion: ${JSON.stringify(scrollBehaviors)}`,
    );
  }

  const visualOrder = await page.evaluate(() =>
    ["#close-session", "#next-card"]
      .map((selector) => {
        const rect = document.querySelector(selector).getBoundingClientRect();
        return { selector, top: rect.top, left: rect.left };
      })
      .sort((first, second) => first.top - second.top || first.left - second.left)
      .map(({ selector }) => selector),
  );
  await tabTo(page, findings, "#close-session", "session controls");
  await tabTo(page, findings, "#next-card", "session controls");
  if (visualOrder.join(",") !== "#close-session,#next-card") {
    addFinding(
      findings,
      "focus-order-visual",
      "serious",
      "2.4.3 A",
      ".session-actions",
      `visual order ${visualOrder.join(" -> ")} differs from Tab order #close-session -> #next-card`,
    );
  }

  await page.keyboard.press("Enter");
  await page.getByRole("heading", { name: "Cartão 2" }).waitFor();
  await page.locator("#fake-microphone").focus();
  await page.keyboard.press("Enter");
  await page.waitForFunction(
    () => document.querySelector("#answer-input").value.trim().length > 0,
  );
  await page.locator("#evaluate-answer").focus();
  await page.keyboard.press("Enter");
  await page.locator("#evaluation:not([hidden])").waitFor();
  await page.locator("#next-card").focus();
  await page.keyboard.press("Enter");
  await page.getByRole("heading", { name: "Cartão 3" }).waitFor();
  await expectUniqueRole(
    page,
    findings,
    "img",
    "An original diagram with a blue square, green triangle, and orange circle from left to right.",
    { rule: "image-alt", wcag: "1.1.1 A" },
  );
}

test("WCAG A/AA critical and serious findings block the public walkthrough", async (t) => {
  const server = await startDemoServer();
  t.after(() => stopDemoServer(server));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());

  for (const [viewportName, viewport] of Object.entries(viewports)) {
    await t.test(viewportName, async () => {
      const findings = [];
      const page = await browser.newPage({ viewport, reducedMotion: "reduce" });
      await installObservers(page);
      await page.goto(server.url, { waitUntil: "networkidle" });

      await inspectNamesAndRoles(page, findings);
      await inspectReducedMotion(page, findings);
      await inspectTextContrast(page, findings, "initial");
      await exerciseKeyboardWalkthrough(page, findings, viewportName);
      await inspectTextContrast(page, findings, "evaluated");

      assertReleaseThreshold(findings, viewportName);
      await page.close();
    });
  }
});
