"use client";

import { useCallback, useLayoutEffect, useMemo, useState } from "react";
import { btnSecondary, btnSm, CONTROL_R, type DefaultTheme } from "./defaultTheme";
import { useUIString } from "@/lib/i18n";
import { hexToRgba } from "@/components/constructor/utils";
import { usePageFlip } from "@/components/constructor/blockContent/pageFlip";

export { usePagedItems } from "@/components/constructor/blockContent/usePagedItems";

export function DefaultPagination({
  t,
  page,
  totalPages,
  onPrev,
  onNext,
  prevLabel = "Назад",
  nextLabel = "Вперёд",
  labelFormat = "{page} / {total}",
}: {
  t: DefaultTheme;
  page: number;
  totalPages: number;
  onPrev: () => void;
  onNext: () => void;
  prevLabel?: string;
  nextLabel?: string;
  labelFormat?: string;
}) {
  const idle = totalPages <= 1;
  const label = labelFormat.replace("{page}", String(page + 1)).replace("{total}", String(Math.max(1, totalPages)));
  const atStart = page === 0;
  const atEnd = page >= totalPages - 1;
  return (
    <div
      {...(idle ? { "aria-hidden": true } : {})}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        width: "100%",
        gap: t.space.sm,
        ...(idle ? { visibility: "hidden" as const, pointerEvents: "none" as const } : {}),
      }}
    >
      <button
        type="button"
        onClick={onPrev}
        disabled={atStart}
        style={{ ...btnSecondary(t), ...btnSm(t), opacity: atStart ? 0.4 : 1, cursor: atStart ? "default" : "pointer" }}
      >
        {prevLabel}
      </button>
      <span style={{ fontVariantNumeric: "tabular-nums", color: t.inkDim, fontSize: t.font.sm }}>{label}</span>
      <button
        type="button"
        onClick={onNext}
        disabled={atEnd}
        style={{ ...btnSecondary(t), ...btnSm(t), opacity: atEnd ? 0.4 : 1, cursor: atEnd ? "default" : "pointer" }}
      >
        {nextLabel}
      </button>
    </div>
  );
}

type AutoPageOpts = {
  /** Высота одной строки списка вместе с внутренними отступами. */
  rowHeight: number;
  gap: number;
  /** Минимальная ширина строки: пока влезает две — раскладываем по две в ряд. */
  minRowWidth: number;
  maxCols?: number;
};

/**
 * Разбивает список на страницы по фактическому месту в блоке: сколько строк влезает
 * по высоте × сколько колонок влезает по ширине. Скролла нет — есть страницы.
 */
function measureBox(node: HTMLElement): { w: number; h: number } {
  const cell = node.closest<HTMLElement>("[data-block-cell]");
  const fit = cell ? parseFloat(cell.style.getPropertyValue("--block-scale")) : 1;
  const scale = Number.isFinite(fit) && fit > 0 ? fit : 1;
  let w = Math.round(node.clientWidth * scale);
  let h = Math.round(node.clientHeight * scale);
  if (!cell) return { w, h };
  const wrap = cell.querySelector<HTMLElement>(".block-content-wrap");
  if (w <= 0) w = cell.clientWidth;
  if (wrap) {
    const above = Math.max(0, (node.getBoundingClientRect().top - wrap.getBoundingClientRect().top) / scale);
    const budget = Math.round(Math.max(0, cell.clientHeight - above));
    if (budget > 0) h = h > 0 ? Math.min(h, budget) : budget;
  }
  return { w, h };
}

export function useAutoPagedItems<T>(items: T[], opts: AutoPageOpts) {
  const { rowHeight, gap, minRowWidth, maxCols = 2 } = opts;
  const [el, setEl] = useState<HTMLElement | null>(null);
  const [box, setBox] = useState<{ w: number; h: number }>({ w: 0, h: 0 });
  const [page, setPageState] = useState(0);

  const ref = useCallback((node: HTMLElement | null) => {
    setEl(node);
    if (!node) return;
    const next = measureBox(node);
    if (next.w > 0 || next.h > 0) setBox((prev) => (prev.w === next.w && prev.h === next.h ? prev : next));
  }, []);

  useLayoutEffect(() => {
    if (!el) return;
    const measure = () => setBox((prev) => {
      const next = measureBox(el);
      return prev.w === next.w && prev.h === next.h ? prev : next;
    });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);

  const cols = useMemo(() => {
    if (box.w <= 0) return 1;
    const fit = Math.floor((box.w + gap) / (minRowWidth + gap));
    return Math.max(1, Math.min(maxCols, fit, Math.max(1, items.length)));
  }, [box.w, gap, minRowWidth, maxCols, items.length]);

  const measured = box.h > 0;

  const rowsFit = useMemo(() => {
    if (!measured) return 0;
    return Math.floor((box.h + gap) / (rowHeight + gap));
  }, [box.h, gap, measured, rowHeight]);

  const perPage = useMemo(() => {
    if (!measured) return 0;
    if (items.length === 0) return 1;
    return Math.max(1, rowsFit * cols);
  }, [cols, items.length, measured, rowsFit]);

  const setPage = useCallback((next: number) => setPageState(next), []);

  const totalPages = Math.max(1, Math.ceil(items.length / Math.max(1, perPage)));
  const safePage = Math.min(Math.max(0, page), totalPages - 1);
  const pageItems = items.slice(safePage * perPage, safePage * perPage + perPage);
  const flipRef = usePageFlip(safePage);

  return { ref, cols, rowsFit, perPage, pageItems, page: safePage, totalPages, setPage, flipRef };
}

/** Индикатор страниц: стрелки по бокам и линии-сегменты между ними. */
export function DefaultPageDots({
  t,
  page,
  totalPages,
  onPick,
  label = "Страница",
}: {
  t: DefaultTheme;
  page: number;
  totalPages: number;
  onPick: (index: number) => void;
  label?: string;
}) {
  const ui = useUIString();
  const idle = totalPages <= 1;
  const atStart = page === 0;
  const atEnd = page >= totalPages - 1;

  const arrow = (dir: "prev" | "next") => {
    const disabled = dir === "prev" ? atStart : atEnd;
    return (
      <button
        type="button"
        onClick={() => onPick(dir === "prev" ? page - 1 : page + 1)}
        disabled={disabled}
        aria-label={ui(dir === "prev" ? "pagination.prev" : "pagination.next")}
        title={ui(dir === "prev" ? "pagination.prev" : "pagination.next")}
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: t.px(26),
          height: t.px(26),
          borderRadius: CONTROL_R,
          border: "none",
          background: disabled ? "transparent" : hexToRgba(t.ink, 0.06),
          color: disabled ? t.inkMute : t.ink,
          opacity: disabled ? 0.4 : 1,
          cursor: disabled ? "default" : "pointer",
          flexShrink: 0,
          transition: "background 160ms ease, opacity 160ms ease",
        }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <polyline points={dir === "prev" ? "15 6 9 12 15 18" : "9 6 15 12 9 18"} />
        </svg>
      </button>
    );
  };

  return (
    <div
      {...(idle ? { "aria-hidden": true } : {})}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: t.px(8),
        paddingTop: t.px(10),
        ...(idle ? { visibility: "hidden" as const, pointerEvents: "none" as const } : {}),
      }}
    >
      {arrow("prev")}
      <div style={{ display: "flex", alignItems: "center", gap: t.px(6) }}>
        {Array.from({ length: Math.max(1, totalPages) }).map((_, index) => {
          const active = index === page;
          return (
            <button
              key={index}
              type="button"
              onClick={() => onPick(index)}
              aria-label={`${label} ${index + 1}`}
              aria-current={active ? "true" : undefined}
              style={{
                width: active ? t.px(18) : t.px(6),
                height: t.px(6),
                borderRadius: 999,
                border: "none",
                padding: 0,
                background: active ? t.accent : hexToRgba(t.ink, 0.22),
                cursor: "pointer",
                transition: "width 200ms ease, background 200ms ease",
              }}
            />
          );
        })}
      </div>
      {arrow("next")}
    </div>
  );
}
