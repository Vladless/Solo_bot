import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** Что уезжает в поставку: манифест, бандл, сид и объявленные наборы файлы — ровно то, что читает хост. */
function filesOf(manifest) {
  const names = ["manifest.json", manifest.entry ?? "bundle.js"];
  if (manifest.seed) names.push(manifest.seed);
  for (const asset of manifest.assets ?? []) names.push(asset);
  return [...new Set(names)];
}

/** Кладёт собранный набор в архив поставки: `packs/_artifacts/<id>.solopack.zip`. */
export function packPack(metaUrl, argv = process.argv.slice(2)) {
  const root = dirname(fileURLToPath(metaUrl));
  const dist = join(root, "dist");
  const manifestFile = join(dist, "manifest.json");
  if (!existsSync(manifestFile)) {
    console.error(`Набор не собран: нет ${manifestFile}. Сначала node build.mjs`);
    process.exit(1);
  }
  const manifest = JSON.parse(readFileSync(manifestFile, "utf8"));
  const files = filesOf(manifest);
  for (const name of files) {
    if (existsSync(join(dist, name))) continue;
    console.error(`Файла нет в сборке: ${name}`);
    process.exit(1);
  }

  const outDir = argv.includes("--out") ? resolve(argv[argv.indexOf("--out") + 1]) : join(root, "..", "_artifacts");
  mkdirSync(outDir, { recursive: true });
  const out = join(outDir, `${manifest.id}.solopack.zip`);
  rmSync(out, { force: true });
  /** `-X` убирает лишние поля файловой системы: одинаковая сборка даёт одинаковый архив. */
  execFileSync("zip", ["-q", "-X", out, ...files], { cwd: dist });

  const size = statSync(out).size;
  console.log(`Архив собран: ${out} (${(size / 1024).toFixed(1)} КБ, файлов ${files.length})`);
  return out;
}
