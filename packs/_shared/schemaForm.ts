import type { PackField, PackForm, PackFormTab, PackLabel } from "@/components/constructor/packForms";

type SchemaField = {
  kind?: string;
  default?: unknown;
  values?: readonly string[];
  item?: readonly PackField[];
  addLabel?: string;
};

export type SchemaLabel = string | PackLabel;

export type SchemaFormOptions = {
  type?: string;
  labels?: Record<string, SchemaLabel>;
  blockLabels?: Record<string, Record<string, SchemaLabel>>;
  skip?: readonly string[];
  titles?: Partial<Record<PackFormTab, PackLabel>>;
};

/** Раскладку, привязки и прочее хозяйство хоста набор в своей форме не показывает. */
const HOST_KEYS = new Set([
  "col", "row", "colSpan", "rowSpan", "fineCol", "fineRow", "fineColSpan", "fineRowSpan",
  "zIndex", "fontScale", "anchor", "screenGroup", "screenId", "cabinetTabId", "cabinetTabGroup",
  "tabsGroup", "autoHideWhenSatisfied",
]);

const IMAGE_KEYS = /(Url|Image)$/;
const STYLE_KEYS = /(Color|Opacity|Radius|Width|FontFamily|FontScale|Scale)$/;
const LONG_TEXT = /(text|description|desc|note|message|legal|subtitle)$/i;

const DEFAULT_TITLES: Record<PackFormTab, PackLabel> = {
  content: { ru: "Настройки блока", en: "Block settings" },
  style: { ru: "Оформление блока", en: "Block style" },
  data: { ru: "Данные", en: "Data" },
  effects: { ru: "Эффекты", en: "Effects" },
  interaction: { ru: "Поведение", en: "Behavior" },
};

function toLabel(value: SchemaLabel): PackLabel {
  return typeof value === "string" ? { ru: value, en: value } : value;
}

/** Ключ без подписи читается как машинный: разводим camelCase в слова, чтобы форма осталась понятной. */
function humanize(key: string): string {
  return key
    .replace(/([A-Z])/g, " $1")
    .replace(/^./, (letter) => letter.toUpperCase())
    .trim();
}

/** Схема может быть и простой картой значений: тогда вид поля берём у самого значения. */
function specOf(raw: unknown): SchemaField {
  if (raw && typeof raw === "object" && typeof (raw as SchemaField).kind === "string") return raw as SchemaField;
  if (typeof raw === "boolean") return { kind: "bool", default: raw };
  if (typeof raw === "number") return { kind: "num", default: raw };
  if (typeof raw === "string") return { kind: "str", default: raw };
  if (Array.isArray(raw)) return { kind: "array", default: raw };
  return { kind: "raw", default: raw };
}

function tabOf(key: string, field: SchemaField): PackFormTab {
  if (field.kind === "array") return "data";
  if (STYLE_KEYS.test(key)) return "style";
  return "content";
}

function fieldOf(key: string, field: SchemaField, label: PackLabel): PackField | null {
  switch (field.kind) {
    case "num":
      return { kind: "number", key, label, default: typeof field.default === "number" ? field.default : undefined };
    case "bool":
      return { kind: "boolean", key, label, default: field.default === true };
    case "enum": {
      const values = field.values ?? [];
      if (values.length === 0) return null;
      return {
        kind: "select",
        key,
        label,
        options: values.map((value) => ({ value, label: { ru: value, en: value } })),
        default: typeof field.default === "string" ? field.default : undefined,
      };
    }
    case "array": {
      if (field.item && field.item.length > 0) {
        return {
          kind: "list",
          key,
          label,
          item: [...field.item],
          ...(field.addLabel ? { addLabel: toLabel(field.addLabel) } : {}),
        };
      }
      const sample = Array.isArray(field.default) ? field.default : [];
      if (sample.every((item) => typeof item === "string")) return { kind: "strings", key, label };
      return null;
    }
    case "raw":
      return null;
    case "href":
      return { kind: "text", key, label, placeholder: typeof field.default === "string" ? field.default : "https://" };
    default: {
      const preset = typeof field.default === "string" ? field.default : "";
      if (STYLE_KEYS.test(key) && key.endsWith("Color")) return { kind: "color", key, label, placeholder: preset };
      if (IMAGE_KEYS.test(key)) return { kind: "image", key, label };
      const multiline = LONG_TEXT.test(key) || preset.length > 60 || preset.includes("\n");
      return { kind: "text", key, label, placeholder: preset, ...(multiline ? { multiline: true } : {}) };
    }
  }
}

/** Поля формы прямо из схемы блока: схема и редактор не разъезжаются, подписи берутся из словаря. */
export function fieldsFromSchema(schema: Record<string, unknown>, options: SchemaFormOptions = {}): PackField[] {
  const own = options.blockLabels?.[options.type ?? ""] ?? {};
  const skip = new Set(options.skip ?? []);
  const out: PackField[] = [];
  for (const [key, raw] of Object.entries(schema ?? {})) {
    if (HOST_KEYS.has(key) || skip.has(key)) continue;
    const field = specOf(raw);
    const named = own[key] ?? options.labels?.[key];
    const label = named ? toLabel(named) : { ru: humanize(key), en: humanize(key) };
    const made = fieldOf(key, field, label);
    if (made) out.push(made);
  }
  return out;
}

/** Форма набора: те же секции, что и у рукописной, но поля выведены из схемы блока. */
export function formFromSchema(schema: Record<string, unknown>, options: SchemaFormOptions = {}): PackForm {
  const fields = fieldsFromSchema(schema, options);
  const byTab = new Map<PackFormTab, PackField[]>();
  for (const field of fields) {
    if (!("key" in field)) continue;
    const spec = specOf((schema ?? {})[field.key]);
    const tab = tabOf(field.key, spec);
    byTab.set(tab, [...(byTab.get(tab) ?? []), field]);
  }
  const order: PackFormTab[] = ["content", "style", "data"];
  return order
    .filter((tab) => (byTab.get(tab) ?? []).length > 0)
    .map((tab) => ({
      title: options.titles?.[tab] ?? DEFAULT_TITLES[tab],
      tab,
      fields: byTab.get(tab) ?? [],
    }));
}

/** Рукописная форма плюс то, что в ней забыли: недостающие поля схемы дописываются отдельной секцией. */
export function withSchemaGaps(form: PackForm, schema: Record<string, unknown>, options: SchemaFormOptions = {}): PackForm {
  const covered = new Set<string>();
  const walk = (fields: readonly PackField[]) => {
    for (const field of fields) {
      if ("key" in field && typeof field.key === "string") covered.add(field.key);
      if (field.kind === "list") walk(field.item);
    }
  };
  for (const section of form) walk(section.fields);
  const gaps = formFromSchema(schema, options).map((section) => ({
    ...section,
    fields: section.fields.filter((field) => !("key" in field) || !covered.has(field.key)),
  }));
  return [...form, ...gaps.filter((section) => section.fields.length > 0)];
}
