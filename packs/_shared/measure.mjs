import { createRequire } from "node:module";
import { existsSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";

const FINE_ROW_PX = 12;
const PROBE_PX = 96;

function readArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const item = argv[i];
    if (!item.startsWith("--")) continue;
    const key = item.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) out[key] = true;
    else {
      out[key] = next;
      i += 1;
    }
  }
  return out;
}

function loadPlaywright(root) {
  const require = createRequire(join(root, "web-app", "package.json"));
  return require("playwright");
}

async function measurePage(page, base, url, wait) {
  await page.goto(base + url, { waitUntil: "load", timeout: 90000 }).catch(() => {});
  await page.waitForTimeout(wait);
  const cells = await page.evaluate(() => {
    const out = [];
    for (const cell of document.querySelectorAll("[data-block-type]")) {
      const wrap = cell.querySelector(".block-content-wrap");
      if (!wrap) continue;
      const zoom = parseFloat(getComputedStyle(wrap).zoom || "1") || 1;
      const body = parseFloat(getComputedStyle(document.body).zoom || "1") || 1;
      const rect = cell.getBoundingClientRect();
      out.push({
        type: cell.getAttribute("data-block-type"),
        zoom: Math.round(zoom * 100) / 100,
        top: Math.round((rect.top + scrollY) / body),
        bottom: Math.round((rect.bottom + scrollY) / body),
        height: Math.round(rect.height / body),
      });
    }
    return out.sort((a, b) => a.top - b.top || a.bottom - b.bottom);
  });

  for (const cell of cells) {
    cell.need = await page.evaluate(
      async ([type, probe]) => {
        const node = document.querySelector(`[data-block-type='${type}']`);
        const wrap = node?.querySelector(".block-content-wrap");
        if (!wrap) return null;
        const keepHeight = node.style.height;
        const keepMin = node.style.minHeight;
        node.style.height = `${probe}px`;
        node.style.minHeight = `${probe}px`;
        await new Promise((done) => setTimeout(done, 700));
        const zoom = parseFloat(getComputedStyle(wrap).zoom || "1") || 1;
        const body = parseFloat(getComputedStyle(document.body).zoom || "1") || 1;
        node.style.height = keepHeight;
        node.style.minHeight = keepMin;
        await new Promise((done) => setTimeout(done, 200));
        return Math.round(probe / body / zoom);
      },
      [cell.type, PROBE_PX],
    );
  }
  return cells;
}

function gapsOf(cells) {
  const out = [];
  let bottom = null;
  for (const cell of cells) {
    if (bottom !== null && cell.top - bottom > 60) out.push(`${cell.type}: разрыв ${cell.top - bottom}px`);
    bottom = Math.max(bottom ?? 0, cell.bottom);
  }
  return out;
}

/** Замер набора на живом сайте: сколько строк сетки просит каждый блок и где его жмёт. */
export async function measurePack(metaUrl, argv = process.argv.slice(2)) {
  const root = dirname(fileURLToPath(metaUrl));
  const repo = resolve(root, "..", "..");
  const args = readArgs(argv);
  const config = join(root, "seed.config.mjs");
  const spec = existsSync(config) ? (await import(pathToFileURL(config).href)).default : {};
  const probe = spec.probe ?? {};
  const base = String(args.base ?? probe.base ?? "http://localhost:3000");
  const urls = String(args.pages ?? "")
    .split(",")
    .filter(Boolean);
  const pages = urls.length > 0 ? urls : probe.urls ?? ["/"];
  const width = Number(args.width ?? probe.width ?? 1440);
  const height = Number(args.height ?? probe.height ?? 900);
  const wait = Number(args.wait ?? probe.wait ?? 6000);

  const { chromium } = loadPlaywright(repo);
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width, height }, locale: "ru-RU", colorScheme: String(args.scheme ?? "light") });
  if (args.token) {
    await context.addCookies([
      { name: "auth_token", value: String(args.token), domain: new URL(base).hostname, path: "/" },
      { name: "theme-mode", value: encodeURIComponent("light|light"), domain: new URL(base).hostname, path: "/" },
    ]);
  }
  if (args.identity) {
    await context.addInitScript((id) => {
      try {
        localStorage.setItem("identity_id", id);
      } catch {}
    }, String(args.identity));
  }

  const page = await context.newPage();
  const need = {};
  for (const url of pages) {
    const cells = await measurePage(page, base, url, wait);
    console.log(`\n${url}`);
    for (const cell of cells) {
      const rows = Math.round(cell.height / FINE_ROW_PX);
      const wanted = cell.need == null ? null : Math.ceil(cell.need / FINE_ROW_PX);
      if (wanted != null) need[cell.type] = Math.max(need[cell.type] ?? 0, wanted);
      const slack = wanted == null ? "" : `  запас ${String(rows - wanted).padStart(3)}`;
      const mark = cell.zoom < 0.999 ? `  ← жмёт, зум ${cell.zoom}` : "";
      console.log(`  ${cell.type.padEnd(28)} клетка ${String(rows).padStart(3)}  нужно ${String(wanted ?? "?").padStart(3)}${slack}${mark}`);
    }
    const gaps = gapsOf(cells);
    if (gaps.length > 0) console.log(`  разрывы: ${gaps.join("; ")}`);
  }
  await browser.close();

  const out = args.json === true ? join(root, "measure.json") : args.json ? String(args.json) : null;
  if (out) {
    writeFileSync(out, `${JSON.stringify(need, null, 2)}\n`, "utf8");
    console.log(`\nВысоты записаны: ${out}`);
  }
  return need;
}
