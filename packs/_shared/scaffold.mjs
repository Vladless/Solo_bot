import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const PLACEMENT = new Set(["col", "row", "colSpan", "rowSpan", "fineCol", "fineColSpan", "fineRow", "fineRowSpan"]);
const OWN_GRID = { col: 0, span: 96 };
const FINE_PER_COARSE = 4;

function equal(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function literal(value, indent) {
  const pad = "  ".repeat(indent);
  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    return `[\n${value.map((item) => `${pad}  ${literal(item, indent + 1)},`).join("\n")}\n${pad}]`;
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value);
    if (entries.length === 0) return "{}";
    const lines = entries.map(([key, item]) => {
      const name = /^[A-Za-z_$][\w$]*$/.test(key) ? key : JSON.stringify(key);
      return `${pad}  ${name}: ${literal(item, indent + 1)},`;
    });
    return `{\n${lines.join("\n")}\n${pad}}`;
  }
  return JSON.stringify(value);
}

function themes(seed) {
  const out = [];
  for (const key of ["_theme", "_global_theme"]) {
    if (seed[key] && typeof seed[key] === "object") out.push(seed[key]);
  }
  for (const theme of Object.values(seed._page_themes ?? {})) {
    if (theme && typeof theme === "object") out.push(theme);
  }
  return out;
}

function sharedKeys(list, skip = new Set()) {
  if (list.length === 0) return {};
  const out = {};
  for (const key of Object.keys(list[0]).sort()) {
    if (skip.has(key)) continue;
    if (!list.every((item) => key in item)) continue;
    if (!list.every((item) => equal(item[key], list[0][key]))) continue;
    out[key] = list[0][key];
  }
  return out;
}

/** Тема набора: общее уезжает в tokens/header/footer, особенности страниц — в pageTokens. */
function themeSpec(seed) {
  const list = themes(seed);
  const shared = sharedKeys(list, new Set(["lightModeTokens"]));
  const lights = list.map((theme) => theme.lightModeTokens).filter((item) => item && typeof item === "object");
  const lightShared = lights.length === list.length ? sharedKeys(lights) : {};
  const header = shared.header;
  const footer = shared.footer;
  delete shared.header;
  delete shared.footer;

  const pageTokens = {};
  for (const [slug, theme] of Object.entries(seed._page_themes ?? {})) {
    const delta = {};
    for (const [key, value] of Object.entries(theme)) {
      if (key === "lightModeTokens") {
        const own = Object.entries(value).some(([name, item]) => !equal(lightShared[name], item));
        if (own) delta[key] = value;
        continue;
      }
      if (key === "header" && equal(header, value)) continue;
      if (key === "footer" && equal(footer, value)) continue;
      if (key in shared && equal(shared[key], value)) continue;
      delta[key] = value;
    }
    if (Object.keys(delta).length > 0) pageTokens[slug] = delta;
  }
  return { tokens: shared, lightTokens: lightShared, header, footer, pageTokens };
}

function addressOf(block) {
  const tab = String(block.data.cabinetTabId ?? "");
  const screen = String(block.data.screenId ?? "main");
  const flow = String(block.data.screenGroup ?? "");
  return { tab, screen, flow };
}

/** Содержимое блока: в описание идёт только то, чем набор отличается от своих же дефолтов. */
function pagesSpec(seed, defaults) {
  const out = {};
  for (const [slug, blocks] of Object.entries(seed)) {
    if (slug.startsWith("_") || !Array.isArray(blocks)) continue;
    for (const block of blocks) {
      const { tab } = addressOf(block);
      const target = tab ? `${slug}@${tab}` : slug;
      const base = defaults[block.type] ?? {};
      const data = {};
      for (const [key, value] of Object.entries(block.data)) {
        if (PLACEMENT.has(key) || key === "cabinetTabId" || key === "cabinetTabGroup") continue;
        if (key in base && equal(base[key], value)) continue;
        data[key] = value;
      }
      const entry = Object.keys(data).length > 0 ? { type: block.type, data } : block.type;
      out[target] = [...(out[target] ?? []), entry];
    }
  }
  return out;
}

function cellOf(block) {
  const d = block.data;
  return {
    at: {
      col: Number(d.fineCol ?? Number(d.col ?? 0) * FINE_PER_COARSE),
      span: Number(d.fineColSpan ?? Number(d.colSpan ?? 24) * FINE_PER_COARSE),
    },
    top: Number(d.fineRow ?? Number(d.row ?? 0) * FINE_PER_COARSE),
    rows: Number(d.fineRowSpan ?? Number(d.rowSpan ?? 1) * FINE_PER_COARSE),
  };
}

function commonAt(cells) {
  const counts = new Map();
  for (const cell of cells) {
    const key = `${cell.at.col}:${cell.at.span}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const [key] = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
  const [col, span] = key.split(":").map(Number);
  return { col, span };
}

function bandsOf(cells) {
  const tops = [...new Set(cells.map((cell) => cell.top))].sort((a, b) => a - b);
  return tops.map((top) => ({ top, items: cells.filter((cell) => cell.top === top) }));
}

function gapOf(bands) {
  const counts = new Map();
  for (let i = 1; i < bands.length; i += 1) {
    const bottom = Math.max(...bands[i - 1].items.map((cell) => cell.top + cell.rows));
    const gap = bands[i].top - bottom;
    counts.set(gap, (counts.get(gap) ?? 0) + 1);
  }
  if (counts.size === 0) return 1;
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0];
}

/** Раскладка группы: ряды по фактическим клеткам, несовпадающий ряд пришпиливается номером. */
function groupSpec(blocks) {
  const cells = blocks
    .map((block) => ({ type: block.type, ...cellOf(block) }))
    .sort((a, b) => a.top - b.top || a.at.col - b.at.col);
  const at = commonAt(cells);
  const bands = bandsOf(cells);
  const gap = gapOf(bands);
  const start = bands[0].top;

  const rows = [];
  let cursor = start;
  for (const band of bands) {
    const entry = {};
    if (cursor !== band.top) {
      entry.row = band.top;
      cursor = band.top;
    }
    if (band.items.length === 1) {
      const cell = band.items[0];
      entry.type = cell.type;
      entry.rows = cell.rows;
      if (!equal(cell.at, at)) entry.at = cell.at;
    } else {
      entry.side = band.items.map((cell) => {
        const item = { type: cell.type, rows: cell.rows };
        if (!equal(cell.at, at)) item.at = cell.at;
        return item;
      });
      if (new Set(band.items.map((cell) => cell.rows)).size > 1) entry.align = false;
    }
    rows.push(entry);
    cursor += Math.max(...band.items.map((cell) => cell.rows)) + gap;
  }

  const group = {};
  if (!equal(at, OWN_GRID)) group.at = at;
  if (gap !== 1) group.gap = gap;
  if (start !== 4) group.start = start;
  group.rows = rows;
  return group;
}

/** Зеркальная страница вкладки повторяет её раскладку — сводим оба адреса в один. */
function collapse(groups) {
  const out = {};
  const merged = new Set();
  for (const [target, group] of Object.entries(groups)) {
    if (merged.has(target)) continue;
    const [head, screen] = target.split("#");
    const [slug, tab] = head.split("@");
    if (slug === "dashboard" && tab) {
      const mirror = `dashboard-${tab}${screen ? `#${screen}` : ""}`;
      if (groups[mirror] && equal(groups[mirror], group)) {
        merged.add(mirror);
        out[`dashboard*@${tab}${screen ? `#${screen}` : ""}`] = group;
        continue;
      }
    }
    out[target] = group;
  }
  return out;
}

function layoutSpec(seed) {
  const groups = {};
  for (const [slug, blocks] of Object.entries(seed)) {
    if (slug.startsWith("_") || !Array.isArray(blocks) || blocks.length === 0) continue;
    const buckets = new Map();
    for (const block of blocks) {
      const { tab, screen, flow } = addressOf(block);
      const place = flow ? `#${flow}/${screen}` : screen === "main" ? "" : `#${screen}`;
      const target = `${slug}${tab ? `@${tab}` : ""}${place}`;
      buckets.set(target, [...(buckets.get(target) ?? []), block]);
    }
    for (const [target, items] of buckets) groups[target] = groupSpec(items);
  }
  return { content: OWN_GRID, groups: collapse(groups) };
}

function probeSpec(seed) {
  const urls = [];
  for (const slug of ["landing", "tariffs", "faq", "login", "checkout"]) {
    if (Array.isArray(seed[slug]) && seed[slug].length > 0) urls.push(slug === "landing" ? "/" : `/${slug}`);
  }
  const tabs = [];
  for (const block of seed.dashboard ?? []) {
    const tab = String(block.data.cabinetTabId ?? "");
    if (tab && !tabs.includes(tab)) tabs.push(tab);
  }
  if (tabs.length > 0) urls.push(...tabs.map((tab) => `/dashboard?tab=${tab}`));
  else if (Array.isArray(seed.dashboard) && seed.dashboard.length > 0) urls.push("/dashboard");
  return { urls, width: 1440, height: 900 };
}

/** Делает описание дизайна из готового seed.json: снимок сайта превращается в рабочую конфигурацию. */
export async function scaffoldSeedConfig(metaUrl) {
  const root = dirname(fileURLToPath(metaUrl));
  const file = join(root, "seed.json");
  if (!existsSync(file)) {
    console.error(`Нет сида набора: ${file}`);
    process.exit(1);
  }
  const seed = JSON.parse(readFileSync(file, "utf8"));
  const defaultsFile = join(root, "defaults.json");
  const defaults = existsSync(defaultsFile) ? JSON.parse(readFileSync(defaultsFile, "utf8")) : {};

  const { tokens, lightTokens, header, footer, pageTokens } = themeSpec(seed);
  const spec = {};
  if (Object.keys(tokens).length > 0) spec.tokens = tokens;
  if (Object.keys(lightTokens).length > 0) spec.lightTokens = lightTokens;
  if (header) spec.header = header;
  if (footer) spec.footer = footer;
  if (Object.keys(pageTokens).length > 0) spec.pageTokens = pageTokens;
  spec.probe = probeSpec(seed);
  spec.pages = pagesSpec(seed, defaults);
  spec.layout = layoutSpec(seed);

  const out = join(root, "seed.config.mjs");
  writeFileSync(out, `export default ${literal(spec, 0)};\n`, "utf8");
  console.log(`Описание дизайна собрано: ${out}`);
  return spec;
}
