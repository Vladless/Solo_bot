"use client";

import { useEffect, useRef, type CSSProperties } from "react";
import { hexToRgba } from "@/components/constructor/utils";
import type { DefaultTheme } from "../defaultTheme";
import { CHIP_H, GROUP_GAP, LIST_ROW_H, PAD_BOTTOM, PAD_TOP, PAD_X } from "./contentTokens";

type DefaultSkeletonVariant = "rows" | "grid" | "text";

/** Помечает клетку: заглушка занимает столько же места, сколько займёт контент — по ней можно мерить масштаб. */
function useTwinMark(twin: boolean) {
  const nodeRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const cell = nodeRef.current?.closest<HTMLElement>("[data-block-cell]");
    if (!cell) return;
    if (twin) cell.dataset.fitTwin = "1";
    else delete cell.dataset.fitTwin;
    return () => {
      delete cell.dataset.fitTwin;
    };
  }, [twin]);
  return nodeRef;
}

function barStyle(t: DefaultTheme, height: number, width: string): CSSProperties {
  return {
    height: t.px(height),
    width,
    borderRadius: Math.min(t.radius.sm, Math.round(t.px(height) / 2)),
    background: `linear-gradient(90deg, ${hexToRgba(t.ink, 0.07)} 0%, ${hexToRgba(t.ink, 0.14)} 50%, ${hexToRgba(t.ink, 0.07)} 100%)`,
    backgroundSize: "200% 100%",
    animation: "skeleton-shimmer-kf 1.4s linear infinite",
  };
}

/** Заглушка загрузки по сетке пака: ряды той же высоты, что и настоящие. */
export function DefaultSkeleton({
  t,
  rows = 3,
  cols = 2,
  rowHeight = LIST_ROW_H,
  variant = "rows",
  footer = false,
  twin = false,
  bodyRef,
}: {
  t: DefaultTheme;
  rows?: number;
  cols?: number;
  rowHeight?: number;
  variant?: DefaultSkeletonVariant;
  footer?: boolean;
  /** Заглушка повторяет разметку контента: по ней считается итоговый масштаб блока. */
  twin?: boolean;
  bodyRef?: (node: HTMLElement | null) => void;
}) {
  const twinRef = useTwinMark(twin);
  const setNode = (node: HTMLDivElement | null) => {
    twinRef.current = node;
    bodyRef?.(node);
  };
  const pad = `${t.px(PAD_TOP)}px ${t.px(PAD_X)}px ${t.px(PAD_BOTTOM)}px`;
  const footerSlot = footer ? <div style={{ marginTop: "auto", minHeight: t.px(CHIP_H + GROUP_GAP) }} /> : null;

  if (variant === "text") {
    return (
      <div ref={setNode} data-skeleton="true" style={{ padding: pad }}>
        <div style={barStyle(t, 14, "60%")} />
      </div>
    );
  }

  if (variant === "grid") {
    return (
      <div
        ref={setNode}
        data-skeleton="true"
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${Math.max(1, cols)}, minmax(0, 1fr))`,
          gridAutoRows: `${t.px(rowHeight)}px`,
          gap: t.px(GROUP_GAP),
          padding: pad,
          alignContent: "start",
        }}
      >
        {Array.from({ length: Math.max(1, rows) * Math.max(1, cols) }).map((_, index) => (
          <div key={index} style={barStyle(t, rowHeight - 8, "100%")} />
        ))}
      </div>
    );
  }

  return (
    <div ref={setNode} data-skeleton="true" style={{ display: "flex", flexDirection: "column", padding: pad, flex: "1 1 auto", minHeight: 0 }}>
      {Array.from({ length: Math.max(1, rows) }).map((_, index) => (
        <div key={index} style={{ minHeight: t.px(rowHeight), display: "flex", alignItems: "center", gap: t.px(GROUP_GAP) }}>
          <div style={barStyle(t, 16, index % 3 === 2 ? "45%" : "70%")} />
        </div>
      ))}
      {footerSlot}
    </div>
  );
}
