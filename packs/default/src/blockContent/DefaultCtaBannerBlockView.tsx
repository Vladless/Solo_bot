"use client";

import { useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, panelShadow, pickContrast, CONTROL_R } from "./defaultTheme";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { hexToRgba } from "@/components/constructor/utils";
import { resolveButtonAction } from "@/lib/button-action";
import { useAppInfo } from "@/app/AppInfoProvider";
import { useTrialAvailability } from "../../../_shared/trialAvailability";

export function DefaultCtaBannerBlockView({ block, context, editMode }: TypedBlockViewProps<"defaultCtaBanner">) {
  const d = block.data as Record<string, unknown>;
  const appInfo = useAppInfo();
  const isPreview = context.previewMode === true || editMode === true;
  const trial = useTrialAvailability(isPreview);
  const wrap = context.wrap;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);

  const title = typeof d.title === "string" ? d.title : "Готовы попробовать?";
  const subtitle = typeof d.subtitle === "string"
    ? d.subtitle
    : "Пробный период бесплатно. Подключение за пару минут, отмена в один клик.";
  const ctaLabel = typeof d.ctaLabel === "string" ? d.ctaLabel : "Начать бесплатно";
  const noteText = typeof d.noteText === "string" ? d.noteText : "Карта не требуется";
  const ctaActionType = typeof d.ctaActionType === "string" ? d.ctaActionType : "flow";
  const ctaFlowId = typeof d.ctaFlowId === "string" ? d.ctaFlowId : "trial";
  const action = resolveButtonAction({
    actionType: ctaActionType,
    flowId: ctaFlowId,
    pageSlug: typeof d.ctaPageSlug === "string" ? d.ctaPageSlug : undefined,
    link: typeof d.ctaLink === "string" ? d.ctaLink : "/login",
  });

  const onAccent = pickContrast(t.accent);
  const onAccentDim = hexToRgba(onAccent, 0.78);
  const [hovered, setHovered] = useState(false);

  const trialCta = ctaActionType === "flow" && appInfo.features.trialFlowIds.includes(ctaFlowId.trim());
  if (!isPreview && trialCta && !trial.available) {
    return null;
  }

  const content = (
    <div
      style={{
        width: "100%",
        height: "100%",
        minHeight: 200,
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "clamp(16px, 3cqi, 32px)",
        padding: "clamp(26px, 4.5cqi, 48px)",
        borderRadius: t.radius.md,
        background: `radial-gradient(120% 160% at 8% 0%, ${hexToRgba(onAccent, 0.14)}, transparent 55%), ${t.accent}`,
        boxShadow: panelShadow(t),
        fontFamily: t.monoFont,
        containerType: "inline-size",
        boxSizing: "border-box",
        ...decor,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 10, minWidth: 0, flex: "1 1 320px" }}>
        <div
          style={{
            margin: 0,
            fontSize: "clamp(24px, 4.4cqi, 40px)",
            lineHeight: 1.1,
            letterSpacing: "-0.02em",
            fontWeight: t.weight.bold,
            color: onAccent,
          }}
        >
          {title}
        </div>
        <div style={{ margin: 0, maxWidth: 520, fontSize: "clamp(14px, 1.8cqi, 16px)", lineHeight: 1.55, color: onAccentDim }}>
          {subtitle}
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 10, flexShrink: 0 }}>
        <a
          href={action.href ?? "#"}
          onClick={action.onClick ? (e) => { e.preventDefault(); if (trialCta && (isPreview || !trial.available)) return; action.onClick?.(); } : undefined}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            minHeight: 54,
            padding: "0 32px",
            borderRadius: CONTROL_R,
            background: onAccent,
            color: t.accent,
            fontSize: t.font.md,
            fontWeight: t.weight.bold,
            textDecoration: "none",
            transition: "transform 180ms ease, box-shadow 180ms ease",
            transform: hovered ? "translateY(-2px)" : "none",
            boxShadow: hovered ? "0 12px 26px -8px rgba(0,0,0,0.35)" : "0 2px 10px rgba(0,0,0,0.18)",
          }}
        >
          {ctaLabel}
        </a>
        {noteText ? <div style={{ fontSize: t.font.sm, color: onAccentDim }}>{noteText}</div> : null}
      </div>
    </div>
  );

  return wrap ? wrap(content, false, true) : content;
}
