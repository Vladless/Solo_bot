"use client";

import { Children, cloneElement, isValidElement, type CSSProperties, type ReactElement, type ReactNode } from "react";
import { CONTROL_H, btnSecondary, cardEdge, type DefaultTheme } from "../defaultTheme";
import { hexToRgba } from "@/components/constructor/utils";
import { CHIP_H, GROUP_GAP, LIST_ROW_H, LONG_VALUE_CHARS, PAD_BOTTOM, PAD_TOP, PAD_X, ROW_H } from "./contentTokens";

/** Кнопка внутри ряда или карточки: ряд от неё не растёт. */
export function btnChip(t: DefaultTheme, tone?: string): CSSProperties {
  return {
    ...btnSecondary(t),
    minHeight: t.px(CHIP_H),
    padding: `0 ${t.px(12)}px`,
    fontSize: t.font.xs,
    color: tone ?? t.ink,
    cursor: "pointer",
    flexShrink: 0,
  };
}

/** Тело блока: единые поля и шаг между группами. Заменяет ручные padding в блоках. */
export function DefaultBody({
  t,
  children,
  gap,
  scroll = false,
  style,
}: {
  t: DefaultTheme;
  children: ReactNode;
  gap?: number;
  scroll?: boolean;
  style?: CSSProperties;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: gap ?? t.px(GROUP_GAP),
        padding: `${t.px(PAD_TOP)}px ${t.px(PAD_X)}px ${t.px(PAD_BOTTOM)}px`,
        flex: "1 1 auto",
        minHeight: 0,
        minWidth: 0,
        ...(scroll ? { overflowY: "auto" } : {}),
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/** Зона действий: всегда прижата к низу блока, отделена от контента. */
export function DefaultFooter({
  t,
  children,
  divider = false,
  align = "stretch",
  stack = false,
}: {
  t: DefaultTheme;
  children: ReactNode;
  divider?: boolean;
  align?: "stretch" | "end" | "between";
  /** Поле, подпись и кнопка — одна группа: держим их вместе колонкой у низа блока. */
  stack?: boolean;
}) {
  const justify = align === "end" ? "flex-end" : align === "between" ? "space-between" : "stretch";
  return (
    <div
      style={{
        marginTop: "auto",
        paddingTop: t.px(GROUP_GAP),
        borderTop: divider ? `1px solid ${t.line}` : undefined,
        display: "flex",
        ...(stack
          ? { flexDirection: "column" as const, alignItems: "stretch" as const, gap: t.px(GROUP_GAP) }
          : { alignItems: "center" as const, justifyContent: justify, gap: t.px(8) }),
        minHeight: t.px(CONTROL_H),
        flexShrink: 0,
      }}
    >
      {children}
    </div>
  );
}

/** Группа рядов «поле → значение» с автоматическими разделителями. */
export function DefaultRows({
  t,
  children,
  slots,
  flipRef,
  loading,
}: {
  t: DefaultTheme;
  children: ReactNode;
  slots?: number;
  /** Ссылка пейджера: он сам проигрывает анимацию страницы на этом контейнере. */
  flipRef?: (node: HTMLElement | null) => void;
  /** Данных ещё нет: пустые слоты мерцают заглушкой, высота ряда та же. */
  loading?: boolean;
}) {
  return (
    <div ref={flipRef} data-content-stable="true" data-motion-stagger="true" style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
      {childrenWithDividers(t, children, slots, ROW_H, loading)}
    </div>
  );
}

/**
 * Ряды живут в фиксированных слотах: ключ слота — его номер, а не id записи.
 * Тогда листание страниц меняет только текст внутри уже существующих узлов —
 * блок не перерисовывается целиком, не мигает анимацией смены контента и не скачет.
 * `slots` добивает список пустыми слотами, чтобы высота страницы не зависела от числа записей.
 */
/** Полоска в пустом слоте: тот же ряд, только вместо данных мерцающая заглушка. */
function slotPlaceholder(t: DefaultTheme, index: number): ReactNode {
  return (
    <div style={{ display: "flex", alignItems: "center", height: "100%", paddingRight: t.space.lg }}>
      <div
        style={{
          height: t.px(14),
          width: index % 3 === 2 ? "45%" : "68%",
          borderRadius: 999,
          background: `linear-gradient(90deg, ${hexToRgba(t.ink, 0.07)} 0%, ${hexToRgba(t.ink, 0.14)} 50%, ${hexToRgba(t.ink, 0.07)} 100%)`,
          backgroundSize: "200% 100%",
          animation: "skeleton-shimmer-kf 1.4s linear infinite",
        }}
      />
    </div>
  );
}

function childrenWithDividers(
  t: DefaultTheme,
  children: ReactNode,
  slots?: number,
  rowHeight?: number,
  loading?: boolean,
): ReactNode {
  const visible = Children.toArray(children).filter(Boolean);
  const total = slots && slots > visible.length ? slots : visible.length;
  const out: ReactNode[] = [];
  for (let index = 0; index < total; index += 1) {
    const child = visible[index];
    const filler = child === undefined;
    out.push(
      <div
        key={index}
        style={{
          boxShadow: index < visible.length - 1 ? `inset 0 -1px 0 ${t.line}` : undefined,
          minWidth: 0,
          ...(rowHeight ? { minHeight: t.px(rowHeight) } : {}),
        }}
      >
        {filler
          ? loading
            ? slotPlaceholder(t, index)
            : null
          : isValidElement(child)
            ? cloneElement(child as ReactElement, { key: index })
            : child}
      </div>,
    );
  }
  return out;
}

/**
 * Ряд «поле → значение»: метка слева, значение справа, действие — chip после значения.
 * Длинное текстовое значение не сжимает метку и не съезжает: ряд становится двухстрочным
 * (метка сверху, значение под ней), высота при этом остаётся не меньше базовой.
 */
export function DefaultRow({
  t,
  label,
  value,
  valueColor,
  aside,
  mono = true,
  stacked,
}: {
  t: DefaultTheme;
  label?: ReactNode;
  value?: ReactNode;
  valueColor?: string;
  aside?: ReactNode;
  mono?: boolean;
  stacked?: boolean;
}) {
  const hasLabel = label !== undefined && label !== null && label !== "";
  const longValue = typeof value === "string" && value.length > LONG_VALUE_CHARS;
  const isStacked = stacked ?? (hasLabel && longValue);
  const valueNode =
    value !== undefined && value !== null ? (
      <span
        style={{
          fontSize: t.font.smPlus,
          fontWeight: t.weight.bold,
          color: valueColor ?? t.ink,
          ...(mono ? { fontVariantNumeric: "tabular-nums" } : {}),
          ...(isStacked || !hasLabel
            ? { whiteSpace: "normal" as const, textAlign: "left" as const, lineHeight: 1.35 }
            : { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "100%" }),
        }}
      >
        {value}
      </span>
    ) : null;

  if (isStacked) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          justifyContent: "center",
          minHeight: t.px(ROW_H),
          padding: `${t.px(6)}px 0`,
          minWidth: 0,
        }}
      >
        <span style={{ fontSize: t.font.sm, color: t.inkDim, minWidth: 0 }}>{label}</span>
        <span style={{ display: "flex", alignItems: "center", gap: t.px(8), minWidth: 0 }}>
          {valueNode}
          {aside}
        </span>
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: hasLabel ? "space-between" : "flex-start",
        gap: t.px(GROUP_GAP),
        minHeight: t.px(ROW_H),
        minWidth: 0,
      }}
    >
      {hasLabel ? (
        <span style={{ fontSize: t.font.sm, color: t.inkDim, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {label}
        </span>
      ) : null}
      <span style={{ display: "flex", alignItems: "center", gap: t.px(8), minWidth: 0, flexShrink: hasLabel ? 0 : 1 }}>
        {valueNode}
        {aside}
      </span>
    </div>
  );
}

/** Ряд списка: слева иконка и две строки, справа значение и действие. */
export function DefaultListItem({
  t,
  leading,
  title,
  meta,
  value,
  valueColor,
  action,
}: {
  t: DefaultTheme;
  leading?: ReactNode;
  title: ReactNode;
  meta?: ReactNode;
  value?: ReactNode;
  valueColor?: string;
  action?: ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: t.px(GROUP_GAP),
        minHeight: t.px(LIST_ROW_H),
        minWidth: 0,
      }}
    >
      {leading ? <span style={{ display: "inline-flex", flexShrink: 0, alignItems: "center" }}>{leading}</span> : null}
      <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0, flex: "1 1 auto" }}>
        <span style={{ fontSize: t.font.smPlus, fontWeight: t.weight.bold, color: t.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {title}
        </span>
        {meta ? (
          <span style={{ fontSize: t.font.xs, color: t.inkDim, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {meta}
          </span>
        ) : null}
      </span>
      {value !== undefined && value !== null ? (
        <span
          style={{
            fontSize: t.font.sm,
            fontWeight: t.weight.medium,
            color: valueColor ?? t.inkDim,
            whiteSpace: "nowrap",
            flexShrink: 0,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {value}
        </span>
      ) : null}
      {action ? <span style={{ display: "inline-flex", flexShrink: 0 }}>{action}</span> : null}
    </div>
  );
}

/** Список с разделителями между рядами и фиксированными слотами страницы. */
export function DefaultListRows({
  t,
  children,
  slots,
  flipRef,
  loading,
}: {
  t: DefaultTheme;
  children: ReactNode;
  slots?: number;
  /** Ссылка пейджера: он сам проигрывает анимацию страницы на этом контейнере. */
  flipRef?: (node: HTMLElement | null) => void;
  /** Данных ещё нет: пустые слоты мерцают заглушкой, высота ряда та же. */
  loading?: boolean;
}) {
  return (
    <div
      ref={flipRef}
      data-content-stable="true"
      data-motion-stagger="true"
      style={{ display: "flex", flexDirection: "column", minWidth: 0 }}
    >
      {childrenWithDividers(t, children, slots, LIST_ROW_H, loading)}
    </div>
  );
}

/** Пустое состояние — по центру свободного места, одна строка и не больше одного действия. */
export function DefaultEmptyState({ t, text, action }: { t: DefaultTheme; text: string; action?: ReactNode }) {
  return (
    <div
      style={{
        flex: "1 1 auto",
        minHeight: t.px(LIST_ROW_H),
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: t.px(GROUP_GAP),
        textAlign: "center",
      }}
    >
      <span style={{ fontSize: t.font.sm, color: t.inkDim }}>{text}</span>
      {action}
    </div>
  );
}

/**
 * Экран-фокус внутри блока (привязка, подтверждение, один вопрос): контент по центру
 * свободного места, кнопка возврата — в футере. Зона помечена стабильной, чтобы
 * обновление данных внутри неё не запускало анимацию смены контента.
 */
export function DefaultFocusArea({ t, children }: { t: DefaultTheme; children: ReactNode }) {
  return (
    <div
      data-content-stable="true"
      style={{
        flex: "1 1 auto",
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: t.px(GROUP_GAP),
      }}
    >
      {children}
    </div>
  );
}

/** Пустые слоты до конца страницы: неполная страница по высоте равна полной. */
export function DefaultOptionSlots({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <>
      {Array.from({ length: count }).map((_, index) => (
        <div key={`slot-${index}`} aria-hidden style={{ minWidth: 0 }} />
      ))}
    </>
  );
}

/** Подпись группы внутри тела блока. */
/** Оболочка карточки-опции: одинаковая высота, действие всегда внизу карточки. */
export function DefaultOptionCard({
  t,
  children,
  footer,
  selected = false,
  hovered = false,
  onClick,
  onHoverChange,
  disabled = false,
  ariaLabel,
}: {
  t: DefaultTheme;
  children: ReactNode;
  footer?: ReactNode;
  selected?: boolean;
  hovered?: boolean;
  onClick?: () => void;
  onHoverChange?: (value: boolean) => void;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      onMouseEnter={onHoverChange ? () => onHoverChange(true) : undefined}
      onMouseLeave={onHoverChange ? () => onHoverChange(false) : undefined}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: t.px(8),
        height: "100%",
        minWidth: 0,
        padding: `${t.px(14)}px ${t.px(16)}px`,
        textAlign: "left",
        border: "none",
        borderRadius: t.radius.sm,
        boxShadow: selected
          ? `inset 0 0 0 1.5px ${hexToRgba(t.accent, 0.4)}`
          : hovered
            ? `inset 0 0 0 1.5px ${hexToRgba(t.accent, 0.6)}`
            : cardEdge(t),
        background: selected ? hexToRgba(t.accent, 0.08) : t.innerBg,
        transition: "box-shadow 160ms ease, transform 160ms ease",
        transform: hovered && !selected ? "translateY(-1px)" : undefined,
        color: t.ink,
        fontFamily: t.monoFont,
        cursor: disabled || !onClick ? "default" : "pointer",
      }}
    >
      {children}
      {footer ? <span style={{ marginTop: "auto", display: "inline-flex", minHeight: t.px(CHIP_H), alignItems: "center" }}>{footer}</span> : null}
    </button>
  );
}
