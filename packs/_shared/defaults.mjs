import { createRequire } from "node:module";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

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

/** Дефолты блоков берём у самого набора: бандл на сайте объявляет их тем же кодом, что и редактор. */
export async function dumpDefaults(metaUrl, argv = process.argv.slice(2)) {
  const root = dirname(fileURLToPath(metaUrl));
  const repo = resolve(root, "..", "..");
  const args = readArgs(argv);
  const manifest = JSON.parse(readFileSync(join(root, "manifest.json"), "utf8"));
  const packId = String(args.pack ?? manifest.id);
  const base = String(args.base ?? "http://localhost:3000");
  const wait = Number(args.wait ?? 6000);

  const bundle = join(root, "dist", "bundle.js");
  if (!existsSync(bundle)) {
    console.error(`Нет сборки набора: ${bundle} — сначала node build.mjs`);
    process.exit(1);
  }

  const require = createRequire(join(repo, "web-app", "package.json"));
  const { chromium } = require("playwright");
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto(`${base}/`, { waitUntil: "load", timeout: 90000 });
  await page.waitForTimeout(wait);
  await page.evaluate((source) => {
    new Function(source)();
  }, readFileSync(bundle, "utf8"));
  const blocks = await page.evaluate((id) => {
    const factory = window.__SOLO_PACK_BUNDLES__?.[id];
    if (!factory) return null;
    return factory().map((item) => ({
      type: item.type,
      defaultData: JSON.parse(JSON.stringify(item.defaultData ?? {})),
    }));
  }, packId);
  await browser.close();

  if (!blocks) {
    console.error(`Бандл набора ${packId} не объявил блоки на ${base}`);
    process.exit(1);
  }

  const map = {};
  for (const item of blocks.sort((a, b) => a.type.localeCompare(b.type))) map[item.type] = item.defaultData;
  const out = join(root, "defaults.json");
  writeFileSync(out, `${JSON.stringify(map, null, 2)}\n`, "utf8");
  console.log(`Дефолты блоков сняты: ${Object.keys(map).length} типов → ${out}`);
  return map;
}
