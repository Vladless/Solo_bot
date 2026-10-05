"use client";

import React from "react";
import { hexToRgba } from "@/components/constructor/utils";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { SurfaceEffectFrame } from "@/components/constructor/SurfaceEffectFrame";
import { buildSurfaceEffectFrameProps } from "@/components/constructor/visualBindings";
import { useCheckoutFlowContext } from "@/components/constructor/blockContent/checkout/checkoutFlowContext";
import { useDefaultTheme, panelShadow } from "./defaultTheme";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";

type PlanCell = {
  label?: string;
  value?: string;
  accent?: boolean;
};

const DCB_CSS = `
.dcb-anim > * { animation: dcb-up .6s cubic-bezier(.22,.9,.28,1) both; }
.dcb-anim > *:nth-child(2) { animation-delay: 90ms; }
.dcb-anim > *:nth-child(3) { animation-delay: 180ms; }
.dcb-anim > *:nth-child(4) { animation-delay: 270ms; }
.dcb-anim > *:nth-child(5) { animation-delay: 360ms; }
@keyframes dcb-up { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: none; } }
.dcb-orb-a { animation: dcb-float-a 14s ease-in-out infinite alternate; }
.dcb-orb-b { animation: dcb-float-b 18s ease-in-out infinite alternate; }
@keyframes dcb-float-a { from { transform: translate(0, 0); } to { transform: translate(-30px, 24px); } }
@keyframes dcb-float-b { from { transform: translate(0, 0); } to { transform: translate(26px, -20px); } }
.dcb-ring { animation: dcb-spin 70s linear infinite; }
@keyframes dcb-spin { to { transform: rotate(360deg); } }
`;

export function DefaultCheckoutBannerBlockView({ block, context }: TypedBlockViewProps<"defaultCheckoutBanner">) {
  const { wrap } = context;
  const d = block.data as Record<string, unknown>;
  const flow = useCheckoutFlowContext();
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);
  const cardRadius = `var(--block-visual-radius, ${t.radius.md}px)`;

  const useFlowData = d.useFlowData !== false;
  const flowAvailable = useFlowData && Boolean(flow?.available);

  const breadcrumbItems = Array.isArray(d.breadcrumb)
    ? (d.breadcrumb as string[])
    : ["Кабинет", "Подписки", "Оплата"];
  const headlineText = typeof d.headlineText === "string" ? d.headlineText : "Оплата";
  const subtitleOverride = typeof d.subtitle === "string" ? d.subtitle : "";
  const subtitle = flowAvailable && flow?.modeSubtitle
    ? flow.modeSubtitle
    : (subtitleOverride || "Доступ откроется в течение минуты после подтверждения платежа.");

  const configuredCells: PlanCell[] = Array.isArray(d.planCells) && (d.planCells as PlanCell[]).length > 0
    ? (d.planCells as PlanCell[])
    : [
      { label: "Тариф", value: "PRO" },
      { label: "Период", value: "12 мес" },
      { label: "Устройств", value: "5" },
      { label: "Трафик", value: "Безлимит", accent: true },
    ];

  const cells: PlanCell[] = (() => {
    if (!flowAvailable || !flow) return configuredCells;
    const plan = flow.planSummary;
    const slotValues = [plan.tariff, plan.period, plan.devices, plan.traffic];
    return configuredCells
      .map((cell, i) => ({ cell, value: slotValues[i] }))
      .filter((entry): entry is { cell: PlanCell; value: string } => Boolean(entry.value))
      .map((entry) => ({ ...entry.cell, value: entry.value }));
  })();

  const renderSubtitle = () => {
    if (!subtitle) return null;
    const parts = subtitle.split(/(\*\*[^*]+\*\*)/g);
    return (
      <p style={{
        fontSize: "clamp(13px, 1.7cqi, 15px)",
        color: t.inkDim,
        lineHeight: 1.55,
        maxWidth: 460,
        margin: 0,
      }}>
        {parts.map((part, i) => {
          if (part.startsWith("**") && part.endsWith("**")) {
            return <b key={i} style={{ color: t.ink, fontWeight: 600 }}>{part.slice(2, -2)}</b>;
          }
          return <React.Fragment key={i}>{part}</React.Fragment>;
        })}
      </p>
    );
  };

  const node = (
    <div
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        overflow: "hidden",
        borderRadius: cardRadius,
        background: `radial-gradient(95% 90% at 100% 0%, ${hexToRgba(t.accent, 0.08)}, transparent 55%), ${t.panel}`,
        boxShadow: panelShadow(t),
        fontFamily: t.monoFont,
        containerType: "inline-size",
        boxSizing: "border-box",
        ...decor,
      }}
    >
      <style>{DCB_CSS}</style>

      <div
        className="dcb-orb-a"
        aria-hidden
        style={{ position: "absolute", top: -120, right: -100, width: 280, height: 280, borderRadius: "50%", background: hexToRgba(t.accent, 0.1), filter: "blur(48px)", pointerEvents: "none" }}
      />
      <div
        className="dcb-orb-b"
        aria-hidden
        style={{ position: "absolute", bottom: -110, left: -80, width: 240, height: 240, borderRadius: "50%", background: hexToRgba(t.accent, 0.07), filter: "blur(44px)", pointerEvents: "none" }}
      />
      <div
        className="dcb-ring"
        aria-hidden
        style={{ position: "absolute", top: -120, right: -110, width: 320, height: 320, borderRadius: "50%", border: `1px dashed ${hexToRgba(t.accent, 0.25)}`, pointerEvents: "none" }}
      />
      <div
        aria-hidden
        style={{ position: "absolute", top: -80, right: -70, width: 230, height: 230, borderRadius: "50%", border: `1px solid ${hexToRgba(t.accent, 0.14)}`, pointerEvents: "none" }}
      />

      <div
        className="dcb-anim"
        style={{
          position: "relative",
          zIndex: 1,
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          gap: "clamp(14px, 2.2cqi, 22px)",
          padding: "clamp(22px, 3.4cqi, 40px)",
          boxSizing: "border-box",
        }}
      >
        {breadcrumbItems.length > 0 ? (
          <div style={{ fontSize: 12, fontWeight: 500, color: t.inkMute }}>
            {breadcrumbItems.map((item, i) => (
              <React.Fragment key={i}>
                {i > 0 ? <span style={{ margin: "0 7px", color: t.inkMute }}>·</span> : null}
                <span style={{ color: i === breadcrumbItems.length - 1 ? t.inkDim : t.inkMute }}>{item}</span>
              </React.Fragment>
            ))}
          </div>
        ) : null}

        <h1 style={{
          fontWeight: t.weight.bold,
          fontSize: "clamp(26px, 4.6cqi, 40px)",
          lineHeight: 1.08,
          letterSpacing: "-0.02em",
          color: t.ink,
          margin: 0,
        }}>
          {headlineText}
        </h1>

        {renderSubtitle()}

        {cells.length > 0 ? (
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(min(130px, 100%), 1fr))",
            gap: 10,
            marginTop: 4,
          }}>
            {cells.map((cell, i) => (
              <div
                key={i}
                style={{
                  padding: "12px 14px",
                  borderRadius: 14,
                  background: t.innerBg,
                  border: `1px solid ${t.line}`,
                  minWidth: 0,
                }}
              >
                <div style={{ fontSize: 11, fontWeight: 500, color: t.inkMute }}>{cell.label ?? ""}</div>
                <div style={{
                  fontSize: t.font.lg,
                  fontWeight: t.weight.bold,
                  letterSpacing: "-0.01em",
                  marginTop: 3,
                  color: cell.accent ? t.accent : t.ink,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}>{cell.value ?? ""}</div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );

  const surfaceFrameProps = {
    ...buildSurfaceEffectFrameProps(context, "surface", "transparent", {
      radiusClass: "",
      radiusValue: cardRadius,
    }),
    surfaceStyle: undefined,
  };
  return wrap(
    <SurfaceEffectFrame {...surfaceFrameProps} overflowVisible targetStyle={{ borderRadius: cardRadius }}>
      {node}
    </SurfaceEffectFrame>,
    false,
    true,
  );
}
