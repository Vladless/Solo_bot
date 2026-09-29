"use client";

import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { useDefaultTheme, panelShadow, pickContrast, blendHex } from "./defaultTheme";
import { hexToRgba } from "@/components/constructor/utils";

type StatItem = {
  label?: string;
  value?: string;
  suffix?: string;
};

export function DefaultStatRowBlockView({ block, context }: TypedBlockViewProps<"defaultStatRow">) {
  const d = block.data as Record<string, unknown>;
  const wrap = context.wrap;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);

  const accent = t.accent;
  const accentStyle = d.accentStyle === true;
  const onAccent = pickContrast(accent);
  const valueColor = accentStyle ? onAccent : t.ink;
  const labelColor = accentStyle ? hexToRgba(onAccent, 0.72) : t.inkDim;
  const borderColor = t.line;
  const bgColor = t.panel;
  const borderWidth = Math.max(0, Math.min(8, Number(d.borderWidth) || 1));
  const radius = d.radius != null ? Math.max(0, Math.min(48, Number(d.radius) || 0)) : t.radius.md;
  const paddingPx = Math.max(0, Math.min(64, Number(d.padding) || 20));
  const gapPx = Math.max(0, Math.min(64, Number(d.gap) || 24));

  const itemsRaw = Array.isArray(d.items) ? (d.items as StatItem[]) : [];
  const items: StatItem[] = itemsRaw.length > 0 ? itemsRaw : [
    { label: "Скорость", value: "10", suffix: "Гбит/с" },
    { label: "Серверов", value: "412" },
    { label: "Логов", value: "0" },
  ];

  const content = (
    <div
      style={{
        width: "100%",
        height: "100%",
        minHeight: 84,
        display: "grid",
        gridTemplateColumns: `repeat(${items.length}, 1fr)`,
        gap: gapPx,
        padding: paddingPx,
        ...(accentStyle
          ? { background: `linear-gradient(135deg, ${blendHex(accent, "#FFFFFF", 0.08)}, ${blendHex(accent, "#000000", 0.16)})` }
          : { backgroundColor: bgColor, border: borderWidth > 0 ? `${borderWidth}px solid ${borderColor}` : undefined }),
        borderRadius: radius,
        boxShadow: accentStyle ? `0 18px 44px -18px ${hexToRgba(accent, 0.55)}` : panelShadow(t),
        fontFamily: t.monoFont,
        boxSizing: "border-box",
        ...decor,
      }}
    >
      {items.map((item, i) => (
        <div
          key={i}
          style={{ display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", textAlign: "center", minWidth: 0 }}
        >
          <div
            style={{
              fontSize: t.font.xs,
              color: labelColor,
              letterSpacing: t.tracking.normal,
              marginBottom: 4,
            }}
          >
            {item.label || ""}
          </div>
          <div
            style={{
              fontSize: "clamp(22px, 4cqi, 30px)",
              fontWeight: t.weight.bold,
              letterSpacing: "-0.01em",
              fontVariantNumeric: "tabular-nums",
              color: valueColor,
              lineHeight: 1,
              display: "inline-flex",
              alignItems: "baseline",
              gap: 4,
            }}
          >
            <span>{item.value || ""}</span>
            {item.suffix ? (
              <span style={{ color: accentStyle ? hexToRgba(onAccent, 0.8) : accent, fontSize: "clamp(12px, 2.2cqi, 16px)", fontWeight: t.weight.medium }}>
                {item.suffix}
              </span>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );

  return wrap ? wrap(content, false, true) : content;
}
