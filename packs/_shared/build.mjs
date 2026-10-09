import { build } from "esbuild";
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** Файл набора рядом с импортёром: локальные модули не уходят на мост хоста. */
function resolveLocal(dir, spec) {
  const base = join(dir, spec);
  for (const candidate of [`${base}.ts`, `${base}.tsx`, join(base, "index.ts"), join(base, "index.tsx")]) {
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

/** Импорт приложения из набора: собирается в обращение к мосту хоста. */
function hostBridge(src) {
  return {
    name: "solo-host-modules",
    setup(api) {
      const external = /^(@\/|next\/|react$|react\/jsx-runtime$|react-dom$|swr$)/;
      const coreRelative = /^\.\.(\/\.\.)?\//;
      api.onResolve({ filter: external }, (args) => ({ path: args.path, namespace: "solo-host" }));
      api.onResolve({ filter: coreRelative }, (args) => {
        const localFile = resolveLocal(args.resolveDir, args.path);
        if (localFile) return { path: localFile };
        if (args.path.startsWith("../../")) {
          const name = args.path.replace(/^\.\.\/\.\.\//, "");
          return { path: `@/components/constructor/${name}`, namespace: "solo-host" };
        }
        const name = args.path.replace(/^\.\.\//, "");
        return { path: `@/components/constructor/blockContent/${name}`, namespace: "solo-host" };
      });
      api.onLoad({ filter: /.*/, namespace: "solo-host" }, (args) => ({
        contents: `const host = globalThis.__SOLO_PACK_HOST__;
if (!host) throw new Error("[pack] хост приложения недоступен");
module.exports = host.require(${JSON.stringify(args.path)});`,
        loader: "js",
      }));
    },
  };
}

/** Роли блоков объявлены в наборе один раз: манифест забирает их из того же файла. */
function rolesFromSource(src) {
  for (const rel of ["blocks/meta.ts", "meta.ts", "blocks.ts"]) {
    const file = join(src, rel);
    if (!existsSync(file)) continue;
    const source = readFileSync(file, "utf8");
    const marker = "const ROLES: Record<string, string[]> = ";
    const at = source.indexOf(marker);
    if (at === -1) continue;
    const literal = source.slice(at + marker.length);
    const raw = literal.slice(0, literal.indexOf(";\n"));
    return JSON.parse(raw.replace(/(\w+):/g, '"$1":').replace(/,(\s*[}\]])/g, "$1"));
  }
  return {};
}

/** Собирает набор: бандл через мост хоста, манифест с ролями, сид и файлы рядом. */
export async function buildPack(metaUrl) {
  const root = dirname(fileURLToPath(metaUrl));
  const src = join(root, "src");
  const dist = join(root, "dist");
  mkdirSync(dist, { recursive: true });

  await build({
    entryPoints: [join(src, "entry.ts")],
    outfile: join(dist, "bundle.js"),
    bundle: true,
    format: "iife",
    target: "es2020",
    minify: true,
    jsx: "transform",
    jsxFactory: "h",
    jsxFragment: "Fragment",
    inject: [join(src, "jsx-shim.ts")],
    plugins: [hostBridge(src)],
    logLevel: "info",
  });

  const manifest = JSON.parse(readFileSync(join(root, "manifest.json"), "utf8"));
  const roles = rolesFromSource(src);
  manifest.elements = (manifest.elements ?? []).map((item) => (roles[item.type] ? { ...item, roles: roles[item.type] } : item));
  /** Отпечаток сборки: версию между правками не поднимают, а браузер кеширует бандл по адресу. */
  manifest.buildId = createHash("sha256").update(readFileSync(join(dist, "bundle.js"))).digest("hex").slice(0, 12);
  writeFileSync(join(dist, "manifest.json"), JSON.stringify(manifest, null, 2), "utf8");
  if (manifest.seed) copyFileSync(join(root, manifest.seed), join(dist, manifest.seed));
  for (const asset of manifest.assets ?? []) copyFileSync(join(root, asset), join(dist, asset));

  const bundle = readFileSync(join(dist, "bundle.js"), "utf8");
  /** Своей копии React в наборе быть не должно: и ядро, и jsx-runtime приходят через мост хоста. */
  const leaked = ["__SECRET_INTERNALS", 'Symbol.for("react.'].filter((needle) => bundle.includes(needle));
  if (leaked.length > 0) {
    console.error(`\nВ бандл просочился React: ${leaked.join(", ")}`);
    process.exit(1);
  }
  console.log(`\nБандл собран: ${(bundle.length / 1024).toFixed(1)} КБ, приложение приходит через мост хоста`);
}
