"use client";

import { useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, btnSolid, btnSecondary, pickContrast } from "./defaultTheme";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { hexToRgba } from "@/components/constructor/utils";
import { resolveButtonAction } from "@/lib/button-action";
import { useAppInfo } from "@/app/AppInfoProvider";
import { useTrialAvailability } from "../../../_shared/trialAvailability";

export function DefaultHeroBlockView({ block, context, editMode }: TypedBlockViewProps<"defaultHero">) {
  const d = block.data as Record<string, unknown>;
  const appInfo = useAppInfo();
  const isPreview = context.previewMode === true || editMode === true;
  const trial = useTrialAvailability(isPreview);
  const wrap = context.wrap;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);

  const pillText = typeof d.pillText === "string" ? d.pillText : "Без логов · Без ограничений";
  const title = typeof d.title === "string" ? d.title : "Быстрый и безопасный интернет";
  const titleAccent = typeof d.titleAccent === "string" ? d.titleAccent : "без границ";
  const subtitle = typeof d.subtitle === "string"
    ? d.subtitle
    : "Подключение за пару минут на любом устройстве. Современные протоколы, стабильная скорость и поддержка, которая отвечает.";
  const trustText = typeof d.trustText === "string" ? d.trustText : "Пробный период — бесплатно. Отмена в один клик.";

  const primaryLabel = typeof d.primaryLabel === "string" ? d.primaryLabel : "Попробовать бесплатно";
  const secondaryLabel = typeof d.secondaryLabel === "string" ? d.secondaryLabel : "Смотреть тарифы";
  const primaryActionType = typeof d.primaryActionType === "string" ? d.primaryActionType : "flow";
  const primaryFlowId = typeof d.primaryFlowId === "string" ? d.primaryFlowId : "trial";
  const primaryTrial = primaryActionType === "flow" && appInfo.features.trialFlowIds.includes(primaryFlowId.trim());
  const primaryHidden = !isPreview && primaryTrial && !trial.available;
  const primaryAction = resolveButtonAction({
    actionType: primaryActionType,
    flowId: primaryFlowId,
    pageSlug: typeof d.primaryPageSlug === "string" ? d.primaryPageSlug : undefined,
    link: typeof d.primaryLink === "string" ? d.primaryLink : "/login",
  });
  const secondaryHref = typeof d.secondaryLink === "string" && d.secondaryLink.trim() !== "" ? d.secondaryLink : "#Тарифы";

  const onAccent = pickContrast(t.accent);
  const centered = d.align === "center";
  const [hovered, setHovered] = useState<"primary" | "secondary" | null>(null);
  const lift = (key: "primary" | "secondary") => ({
    transition: "transform 180ms ease, box-shadow 180ms ease, filter 180ms ease",
    transform: hovered === key ? "translateY(-2px)" : "none",
    boxShadow: hovered === key ? "0 10px 24px -8px rgba(16,24,40,0.35)" : undefined,
    filter: hovered === key ? "brightness(1.05)" : undefined,
  });

  const content = (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: centered ? "center" : "flex-start",
        textAlign: centered ? "center" : "left",
        gap: "clamp(20px, 3.2cqi, 30px)",
        fontFamily: t.monoFont,
        containerType: "inline-size",
        boxSizing: "border-box",
        ...decor,
      }}
    >
      <div
        style={{
          alignSelf: centered ? "center" : "flex-start",
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 14px",
          borderRadius: 999,
          background: hexToRgba(t.accent, 0.12),
          color: t.accent,
          fontSize: t.font.sm,
          fontWeight: t.weight.medium,
        }}
      >
        <span style={{ width: 7, height: 7, borderRadius: "50%", background: t.accent }} />
        {pillText}
      </div>
      <h1
        style={{
          margin: 0,
          fontSize: "clamp(32px, 7.4cqi, 64px)",
          lineHeight: 1.06,
          letterSpacing: "-0.02em",
          fontWeight: t.weight.bold,
          color: t.ink,
        }}
      >
        {title}
        {titleAccent ? (
          <>
            {" "}
            <span style={{ color: t.accent }}>{titleAccent}</span>
          </>
        ) : null}
      </h1>
      <p
        style={{
          margin: 0,
          maxWidth: 560,
          fontSize: "clamp(15px, 2cqi, 18px)",
          lineHeight: 1.55,
          color: t.inkDim,
        }}
      >
        {subtitle}
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: centered ? "center" : "flex-start", gap: 12 }}>
        {!primaryHidden ? (
        <a
          href={primaryAction.href ?? "#"}
          onClick={primaryAction.onClick ? (e) => { e.preventDefault(); if (primaryTrial && (isPreview || !trial.available)) return; primaryAction.onClick?.(); } : undefined}
          onMouseEnter={() => setHovered("primary")}
          onMouseLeave={() => setHovered(null)}
          style={{
            ...btnSolid(t),
            textDecoration: "none",
            minHeight: 52,
            padding: "0 28px",
            fontSize: t.font.md,
            color: onAccent,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            ...lift("primary"),
          }}
        >
          {primaryLabel}
        </a>
        ) : null}
        <a
          href={secondaryHref}
          onMouseEnter={() => setHovered("secondary")}
          onMouseLeave={() => setHovered(null)}
          style={{
            ...(primaryHidden ? btnSolid(t) : btnSecondary(t)),
            ...(primaryHidden ? { color: onAccent } : null),
            textDecoration: "none",
            minHeight: 52,
            padding: "0 24px",
            fontSize: t.font.md,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            ...lift("secondary"),
          }}
        >
          {secondaryLabel}
        </a>
      </div>
      {trustText ? (
        <div style={{ fontSize: t.font.sm, color: t.inkMute }}>{trustText}</div>
      ) : null}
    </div>
  );

  return wrap ? wrap(content, false, true) : content;
}
