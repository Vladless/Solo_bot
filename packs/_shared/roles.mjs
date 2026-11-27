import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import ts from "typescript";

/** Роли блоков объявлены в наборе один раз: манифест забирает их из того же файла. */
export function rolesFromSource(src) {
  const roles = {};
  for (const rel of ["blocks/meta.ts", "meta.ts", "blocks.ts"]) {
    const file = join(src, rel);
    if (!existsSync(file)) continue;
    const source = readFileSync(file, "utf8");
    const marker = "const ROLES: Record<string, string[]> = ";
    const at = source.indexOf(marker);
    if (at === -1) continue;
    const literal = source.slice(at + marker.length);
    const raw = literal.slice(0, literal.indexOf(";\n"));
    Object.assign(roles, JSON.parse(raw.replace(/(\w+):/g, '"$1":').replace(/,(\s*[}\]])/g, "$1")));
    break;
  }
  const scan = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const file = join(dir, entry.name);
      if (entry.isDirectory()) { scan(file); continue; }
      if (!/\.tsx?$/.test(entry.name)) continue;
      const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, entry.name.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
      const visit = (node) => {
        if (ts.isObjectLiteralExpression(node)) {
          const props = new Map(node.properties.filter(ts.isPropertyAssignment).map(prop => [prop.name.getText(source).replace(/^['"]|['"]$/g, ""), prop.initializer]));
          const type = props.get("type");
          const declared = props.get("roles");
          if (type && ts.isStringLiteralLike(type) && declared && ts.isArrayLiteralExpression(declared)) {
            const values = declared.elements.filter(ts.isStringLiteralLike).map(role => role.text);
            if (values.length) roles[type.text] = [...new Set([...(roles[type.text] ?? []), ...values])];
          }
        }
        ts.forEachChild(node, visit);
      };
      visit(source);
    }
  };
  scan(src);
  return roles;
}
