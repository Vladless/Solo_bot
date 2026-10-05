"use client";

import type { CSSProperties, ReactNode } from "react";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { panelStyle, panelHeaderStyle, panelBodyStyle, type DefaultTheme } from "./defaultTheme";
import { useLoadedReveal } from "@/components/constructor/blockContent/useLoadedFade";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { panelContent } from "@/components/constructor/blockContent/cabinetKit/panelContent";
import { DefaultEmptyState } from "./layout/DefaultContent";
import { DefaultSkeleton } from "./layout/DefaultSkeleton";

type SkeletonShape = {
  rows?: number;
  cols?: number;
  rowHeight?: number;
  variant?: "rows" | "grid" | "text";
  footer?: boolean;
  twin?: boolean;
};

type DefaultPanelProps = {
  t: DefaultTheme;
  header?: string;
  hint?: string;
  hintColor?: string;
  headerAction?: ReactNode;
  loading?: boolean;
  loadingText?: string;
  skeleton?: SkeletonShape | boolean;
  empty?: boolean;
  emptyText?: string;
  emptyAction?: ReactNode;
  bodyStyle?: CSSProperties;
  bodyGap?: number;
  noBodyPadding?: boolean;
  /** Ссылка на тело панели: по нему считается свободное место и в загрузке, и с контентом. */
  bodyRef?: (node: HTMLElement | null) => void;
  children: ReactNode;
};

export function DefaultPanel({
  t,
  header,
  hint,
  hintColor,
  headerAction,
  loading = false,
  loadingText,
  skeleton = true,
  empty = false,
  emptyText = "Нет данных",
  emptyAction,
  bodyStyle,
  bodyGap,
  noBodyPadding = false,
  bodyRef,
  children,
}: DefaultPanelProps) {
  const { revealRef, fadeClass: introFade } = useLoadedReveal(loading);
  const decor = usePanelDecor(t.panel);

  const innerBodyStyle: CSSProperties = noBodyPadding
    ? { flex: "1 1 auto", minHeight: 0, display: "flex", flexDirection: "column", ...(bodyStyle ?? {}) }
    : { ...panelBodyStyle(t), display: "flex", flexDirection: "column", gap: bodyGap ?? t.space.md, ...(bodyStyle ?? {}) };


  return (
    <div ref={revealRef} className={ELEMENT_FILL_CLASS} style={{ ...panelStyle(t), ...decor }}>
      {header !== undefined ? (
        <div style={panelHeaderStyle(t)}>
          <b style={{ color: t.ink, fontWeight: t.weight.bold }}>{header}</b>
          {headerAction !== undefined
            ? headerAction
            : hint !== undefined
              ? <span style={{ color: hintColor ?? t.inkDim, fontSize: t.font.sm }}>{hint}</span>
              : null}
        </div>
      ) : null}
      {panelContent({
        loading,
        loadingText: skeleton === false ? loadingText ?? "Загрузка..." : loadingText,
        skeleton,
        empty,
        emptyText,
        renderSkeleton: (shape) => (
          <DefaultSkeleton
            bodyRef={bodyRef}
            t={t}
            rows={shape.rows}
            cols={shape.cols}
            rowHeight={(shape as { rowHeight?: number }).rowHeight}
            variant={shape.variant}
            footer={(shape as { footer?: boolean }).footer}
            twin={(shape as { twin?: boolean }).twin}
          />
        ),
        renderNotice: (text) => (
          <div ref={bodyRef} data-skeleton={loading ? "true" : undefined} style={{ ...(noBodyPadding ? panelBodyStyle(t) : innerBodyStyle), display: "flex", flexDirection: "column" }}>
            {loading ? <span style={{ color: t.inkDim, fontSize: t.font.sm }}>{text}</span> : <DefaultEmptyState t={t} text={text} action={emptyAction} />}
          </div>
        ),
        renderBody: () => (
          <div ref={bodyRef} className={introFade} style={innerBodyStyle}>{children}</div>
        ),
      })}
    </div>
  );
}
