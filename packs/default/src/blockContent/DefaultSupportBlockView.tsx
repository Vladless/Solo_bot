"use client";

import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { btnSecondary, btnSm, btnSolid, pickContrast, useDefaultTheme, panelStyle } from "./defaultTheme";
import { parseBlockData } from "@/components/constructor/blockContent/cabinetKit/blockSchema";
import { SupportInner, type SupportUI } from "@/components/constructor/blockContent/support/SupportInner";
import { SUPPORT_SCHEMA } from "@/components/constructor/blockContent/support/supportSchema";

export function DefaultSupportBlockView({ block, context }: TypedBlockViewProps<"defaultSupport">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);
  const cfg = parseBlockData(d, SUPPORT_SCHEMA);
  const ui: SupportUI = {
    ink: t.ink,
    dim: t.inkDim,
    accent: t.accent,
    accentText: pickContrast(t.accent),
    innerBg: t.innerBg,
    line: t.line,
    rSm: t.radius.sm,
    rMd: t.radius.md,
    gap: t.space.md,
    fSm: t.font.sm,
    fMd: t.font.md,
    fLg: t.font.lg,
    font: t.monoFont,
    primaryBtn: { ...btnSolid(t), ...btnSm(t) },
    ghostBtn: { ...btnSecondary(t), ...btnSm(t) },
  };
  return wrap(
    <div className={ELEMENT_FILL_CLASS} style={{ ...panelStyle(t), ...decor, padding: t.space.lg }}>
      <SupportInner ui={ui} cfg={cfg} previewMode={previewMode === true} />
    </div>,
    false,
    true,
  );
}
