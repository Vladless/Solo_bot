import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";

const FINE_PER_COARSE = 4;

/** Палитра набора — четырнадцать ролей; из них раскрывается вся тема страницы. */
export function tokensFromPalette(p, shape = {}) {
  const panel = shape.panel ?? "xl";
  const button = shape.button ?? "full";
  return {
    primary: p.accent,
    accentColor: p.accent,
    buttonColor: p.accent,
    buttonHoverColor: p.accentHover,
    buttonActiveColor: p.accentHover,
    buttonHoverTextColor: p.accentInk,
    glowColor: p.accent,
    background: p.bg,
    pageBgColor: p.bg,
    pageBackgroundGradient: p.gradient,
    pageBackgroundMediaColor: p.bg,
    foreground: p.ink,
    inkColor: p.ink,
    textPrimaryColor: p.ink,
    textSecondaryColor: p.dim,
    surfaceColor: p.surface,
    fieldPanelColor: p.surface,
    surfaceMutedColor: p.muted,
    inputFieldColor: p.muted,
    surfaceHoverColor: p.muted,
    buttonInactiveColor: p.muted,
    outlineColor: p.line,
    shadowColor: p.deep,
    successColor: p.success,
    warningColor: p.warn,
    errorColor: p.error,
    deepColor: p.deep,
    elementRadius: panel,
    uiPanelRadius: panel,
    uiButtonRadius: button,
    elementEffectColor: p.surface,
    elementEffectEnabled: false,
    elementShowGlow: false,
    cardHoverEffectColor: p.accent,
    surfaceHoverTextColor: p.ink,
    cabinetTabBarBackgroundColor: p.surface,
    cabinetTabBarBorderColor: p.line,
    cabinetTabBorderColor: p.line,
    cabinetTabActiveBackgroundColor: p.accent,
    cabinetTabActiveTextColor: p.accentInk,
    cabinetTabActiveBorderColor: p.accent,
    cabinetTabInactiveBackgroundColor: "transparent",
    cabinetTabInactiveTextColor: p.dim,
    cabinetTabHoverBackgroundColor: p.muted,
    cabinetTabHoverTextColor: p.ink,
    header: {
      color: p.ink,
      linkColor: p.dim,
      buttonRadius: button,
      outlineColor: p.line,
      linkHoverColor: p.accent,
      separatorColor: p.line,
      backgroundColor: p.bg,
      buttonTextColor: p.accentInk,
      buttonHoverColor: p.accentHover,
      separatorAtSidebar: true,
      buttonHoverTextColor: p.accentInk,
      buttonBackgroundColor: p.accent,
      headerButtonTextColor: p.accentInk,
      headerButtonBackgroundColor: p.accent,
    },
    footer: {
      color: p.dim,
      linkColor: p.ink,
      outlineColor: p.line,
      linkHoverColor: p.accent,
      separatorColor: p.line,
      topBorderColor: p.line,
      backgroundColor: p.bg,
      buttonHoverColor: p.accentHover,
      buttonHoverTextColor: p.accentInk,
    },
  };
}

function assign(target, patch, dropNull = false) {
  for (const [key, value] of Object.entries(patch)) {
    if (dropNull && value === null) delete target[key];
    else target[key] = value;
  }
}

/**
 * Обвязку набор держит в глобальной теме: у страницы она заводится, только если уже была,
 * иначе копия шапки на каждой странице перебивала бы смену темы.
 */
function applySlots(theme, spec, onlyExisting = false) {
  for (const slot of ["header", "footer", "cabinetNav"]) {
    if (!spec[slot]) continue;
    const own = theme[slot] && typeof theme[slot] === "object";
    if (!own && onlyExisting) continue;
    theme[slot] = own ? theme[slot] : {};
    assign(theme[slot], spec[slot]);
  }
}

/** Одна палитра ложится на глобальную тему, тему каждой страницы набора и светлый режим. */
export function applyTheme(seed, spec) {
  const palette = spec.palette ?? {};
  const dark = palette.dark ? tokensFromPalette(palette.dark, spec.shape) : {};
  const light = palette.light ? tokensFromPalette(palette.light, spec.shape) : null;
  const extra = spec.tokens ?? {};

  const paint = (theme, slug) => {
    if (!theme || typeof theme !== "object") return;
    assign(theme, dark);
    assign(theme, extra);
    if (slug) assign(theme, spec.pageTokens?.[slug] ?? {});
    applySlots(theme, spec, Boolean(slug));
    const lightTokens = theme.lightModeTokens;
    if (lightTokens && typeof lightTokens === "object") {
      if (light) assign(lightTokens, light);
      assign(lightTokens, spec.lightTokens ?? {});
    }
  };

  for (const key of ["_theme", "_global_theme"]) paint(seed[key], "");
  const pageThemes = (seed._page_themes = seed._page_themes ?? {});
  for (const [slug, blocks] of Object.entries(seed)) {
    if (slug.startsWith("_") || !Array.isArray(blocks)) continue;
    pageThemes[slug] = pageThemes[slug] ?? (light ? { lightModeTokens: {} } : {});
  }
  for (const [slug, theme] of Object.entries(pageThemes)) paint(theme, slug);
  return seed;
}

/** Данные блока: набор задаёт свои поля и убирает чужие, доставшиеся от сида-донора. */
export function applyData(seed, spec) {
  const strip = spec.strip ?? {};
  for (const [pages, byType] of Object.entries(spec.data ?? {})) {
    for (const slug of pages.split(" ")) {
      const blocks = seed[slug];
      if (!Array.isArray(blocks)) continue;
      for (const block of blocks) {
        const patch = byType[block.type];
        if (!patch) continue;
        for (const key of strip[block.type] ?? []) delete block.data[key];
        assign(block.data, patch, true);
      }
    }
  }
  return seed;
}

const CARRY_KEYS = [
  "zIndex",
  "fontScale",
  "mobileCol",
  "mobileColSpan",
  "mobileRow",
  "mobileOrder",
  "mobileGridV2",
  "mobileHidden",
  "vs_surface",
];

/** Страница набора: каждый объявленный блок либо уже есть, либо создаётся из дефолтов набора. */
export function seedPages(seed, spec, defaults = {}) {
  for (const [target, items] of Object.entries(spec.pages ?? {})) {
    const [slug, tab = ""] = target.split("@");
    const blocks = (seed[slug] = Array.isArray(seed[slug]) ? seed[slug] : []);
    const sample = blocks.find((b) => (b.data.cabinetTabId ?? "") === tab) ?? blocks[0] ?? null;
    const used = new Set();
    let order = blocks.reduce((max, b) => Math.max(max, Number(b.data.mobileOrder) || 0), 0);
    for (const item of items) {
      const type = typeof item === "string" ? item : item.type;
      const patch = typeof item === "string" ? {} : (item.data ?? {});
      const found = blocks.find(
        (b) => b.type === type && !used.has(b) && (!tab || (b.data.cabinetTabId ?? "") === tab),
      );
      if (found) {
        used.add(found);
        assign(found.data, patch);
        continue;
      }
      order += 1;
      const data = { ...(defaults[type] ?? {}) };
      for (const key of CARRY_KEYS) if (sample && key in sample.data) data[key] = sample.data[key];
      assign(data, patch);
      data.mobileOrder = order;
      data.mobileRow = order * 20;
      if (tab) {
        data.cabinetTabId = tab;
        data.cabinetTabGroup = sample?.data.cabinetTabGroup ?? "cabinet";
      }
      blocks.push({ type, data });
    }
  }
  return seed;
}

/** Клетка мелкой сетки; крупная — её зеркало по правилу ядра: место вниз, размер вверх. */
export function place(block, at, row, rows) {
  const d = block.data;
  d.fineCol = at.col;
  d.fineColSpan = at.span;
  d.fineRow = row;
  d.fineRowSpan = rows;
  d.col = Math.max(0, Math.floor(at.col / FINE_PER_COARSE));
  d.colSpan = Math.max(1, Math.ceil(at.span / FINE_PER_COARSE));
  d.row = Math.max(0, Math.floor(row / FINE_PER_COARSE));
  d.rowSpan = Math.max(1, Math.ceil(rows / FINE_PER_COARSE));
}

function entryList(entry) {
  if (Array.isArray(entry.side)) return entry.side;
  return [entry];
}

/** Ряд за рядом: блок на полосу, пара одной высоты или стопки по колонкам с общим низом. */
export function layoutRows(blocks, group, fallback) {
  const gap = group.gap ?? 1;
  const at = group.at ?? fallback;
  const used = new Set();
  let row = group.start ?? 4;
  const take = (type) => {
    const found = blocks.find((b) => b.type === type && !used.has(b)) ?? null;
    if (found) used.add(found);
    return found;
  };

  for (const entry of group.rows ?? []) {
    if (typeof entry.row === "number") row = entry.row;
    if (Array.isArray(entry.columns)) {
      const placed = [];
      let bottom = row;
      for (const column of entry.columns) {
        let cursor = row;
        let last = null;
        for (const item of column.stack) {
          const found = take(item.type);
          if (found) {
            place(found, column.at ?? at, cursor, item.rows);
            last = { block: found, at: column.at ?? at, top: cursor };
          }
          cursor += item.rows + gap;
        }
        bottom = Math.max(bottom, cursor);
        if (last) placed.push(last);
      }
      if (entry.align !== false) {
        for (const { block, at: columnAt, top } of placed) place(block, columnAt, top, bottom - gap - top);
      }
      row = bottom;
      continue;
    }
    const items = entryList(entry);
    const height = Math.max(...items.map((item) => item.rows));
    for (const item of items) {
      const found = take(item.type);
      if (found) place(found, item.at ?? at, row, entry.align === false ? item.rows : height);
    }
    row += height + gap;
  }
  return row;
}

/** Адрес группы: `slug`, `slug*`, `slug@вкладка`, `slug#экран`, `slug#поток/экран`. */
function groupBlocks(seed, target) {
  const [head, screenPart] = target.split("#");
  const [slugPart, tab = ""] = head.split("@");
  const wildcard = slugPart.endsWith("*");
  const prefix = wildcard ? slugPart.slice(0, -1) : slugPart;
  const [flow, screenName] = (screenPart ?? "").includes("/") ? screenPart.split("/") : ["", screenPart];
  const screen = screenName || "main";
  const out = [];
  for (const [slug, blocks] of Object.entries(seed)) {
    if (slug.startsWith("_") || !Array.isArray(blocks)) continue;
    if (wildcard ? !slug.startsWith(prefix) : slug !== prefix) continue;
    const onTab = tab ? blocks.filter((b) => (b.data.cabinetTabId ?? "") === tab) : blocks;
    const mirror = tab ? slug === tab || slug.endsWith(`-${tab}`) : false;
    const pool = tab && onTab.length === 0 && mirror ? blocks.filter((b) => !b.data.cabinetTabId) : onTab;
    const picked = pool.filter(
      (b) => (b.data.screenId ?? "main") === screen && (!flow || (b.data.screenGroup ?? "") === flow),
    );
    if (picked.length > 0) out.push(picked);
  }
  return out;
}

/** Раскладка: каждая группа блоков — страница, вкладка кабинета или её отдельный экран. */
export function applyLayout(seed, spec) {
  const layout = spec.layout ?? {};
  const fallback = layout.content ?? { col: 0, span: 96 };
  for (const [target, group] of Object.entries(layout.groups ?? {})) {
    for (const blocks of groupBlocks(seed, target)) layoutRows(blocks, group, fallback);
  }
  return seed;
}

export function generateSeed(seed, spec, defaults = {}) {
  seedPages(seed, spec, defaults);
  applyTheme(seed, spec);
  applyData(seed, spec);
  applyLayout(seed, spec);
  return seed;
}

/** Собирает seed.json набора из его seed.config.mjs: палитра, данные блоков и раскладка. */
export async function buildSeed(metaUrl) {
  const root = dirname(fileURLToPath(metaUrl));
  const config = join(root, "seed.config.mjs");
  const file = join(root, "seed.json");
  if (!existsSync(config)) {
    console.error(`Нет описания дизайна: ${config}`);
    process.exit(1);
  }
  const spec = (await import(pathToFileURL(config).href)).default;
  const seed = existsSync(file)
    ? JSON.parse(readFileSync(file, "utf8"))
    : { _theme: {}, _global_theme: {}, _page_themes: {}, _flows: [] };
  const known = join(root, "defaults.json");
  const defaults = existsSync(known) ? JSON.parse(readFileSync(known, "utf8")) : {};
  generateSeed(seed, spec, defaults);
  writeFileSync(file, `${JSON.stringify(seed, null, 2)}\n`, "utf8");

  const pages = Object.entries(seed).filter(([slug, value]) => !slug.startsWith("_") && Array.isArray(value));
  const total = pages.reduce((sum, [, value]) => sum + value.length, 0);
  console.log(`Сид собран: страниц ${pages.filter(([, v]) => v.length > 0).length}, блоков ${total}`);
  return seed;
}
