"use client";

import { useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { cardEdge, pillStyle, type DefaultTheme } from "./defaultTheme";
import { hexToRgba } from "@/components/constructor/utils";

/** С какого количества вариантов карточки уступают место компактному списку. */
export const COMPACT_LIST_FROM = 2;

/** Высота строки списка в px — по ней считается, сколько строк влезает на страницу. */
export const OPTION_ROW_HEIGHT = 56;

/** Минимальная ширина строки в px: ниже неё две строки в ряд уже не читаются. */
export const OPTION_ROW_MIN_WIDTH = 220;

/**
 * Компактная строка выбора для узкой колонки: слева название и характеристики,
 * справа цена и стрелка. На мобильном вместо карточек-простыней — список,
 * в котором видно сразу несколько вариантов.
 */
export function DefaultOptionRow({
  t,
  title,
  meta,
  price,
  priceNote,
  actionLabel,
  badge,
  leading,
  selected = false,
  disabled = false,
  onClick,
  style,
}: {
  t: DefaultTheme;
  title: string;
  meta?: string;
  price: string;
  priceNote?: string;
  actionLabel: string;
  badge?: string;
  leading?: ReactNode;
  selected?: boolean;
  disabled?: boolean;
  onClick: () => void;
  style?: CSSProperties;
}) {
  const [hovered, setHovered] = useState(false);
  const ring = selected
    ? `inset 0 0 0 1.5px ${hexToRgba(t.accent, 0.4)}`
    : hovered && !disabled
      ? `inset 0 0 0 1.5px ${hexToRgba(t.accent, 0.6)}`
      : cardEdge(t);
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      disabled={disabled}
      aria-label={`${title} — ${actionLabel}`}
      title={actionLabel}
      style={{
        display: "flex",
        alignItems: "center",
        gap: t.px(12),
        width: "100%",
        padding: `${t.px(10)}px ${t.px(14)}px`,
        scrollSnapAlign: "start",
        borderRadius: t.radius.sm,
        background: selected ? hexToRgba(t.accent, 0.08) : t.innerBg,
        border: "none",
        boxShadow: ring,
        color: t.ink,
        fontFamily: t.monoFont,
        textAlign: "left",
        cursor: disabled ? "default" : "pointer",
        minWidth: 0,
        transition: "box-shadow 160ms ease",
        ...style,
      }}
    >
      {leading ? (
        <span aria-hidden style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", flexShrink: 0, lineHeight: 0 }}>
          {leading}
        </span>
      ) : null}

      <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0, flex: "1 1 auto" }}>
        <span
          style={{
            fontSize: t.font.smPlus,
            fontWeight: t.weight.bold,
            color: selected ? t.accent : t.ink,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {title}
        </span>
        {meta ? (
          <span style={{ fontSize: t.font.xs, color: t.inkDim, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {meta}
          </span>
        ) : null}
      </span>

      <span style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2, flexShrink: 0 }}>
        <span style={{ fontSize: t.font.md, fontWeight: t.weight.bold, color: t.ink, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
          {price}
        </span>
        {priceNote ? (
          <span style={{ fontSize: t.font.xxs, color: t.inkMute, whiteSpace: "nowrap" }}>{priceNote}</span>
        ) : null}
      </span>

      {badge ? (
        <span style={{ ...pillStyle(t, t.accent), flexShrink: 0 }}>{badge}</span>
      ) : (
        <svg
          aria-hidden
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke={disabled ? t.inkMute : t.accent}
          strokeWidth={2.4}
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ flexShrink: 0 }}
        >
          <polyline points="9 6 15 12 9 18" />
        </svg>
      )}
    </button>
  );
}
