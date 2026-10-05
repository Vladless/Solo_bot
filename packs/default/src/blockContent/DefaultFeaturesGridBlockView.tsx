"use client";

import { useState, type ReactNode } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, useIsMobile, panelShadow } from "./defaultTheme";
import { panelSurfaceBackground, usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { SurfaceEffectFrame } from "@/components/constructor/SurfaceEffectFrame";
import { buildSurfaceEffectFrameProps } from "@/components/constructor/visualBindings";
import { hexToRgba } from "@/components/constructor/utils";

type FeatureItem = {
  code?: string;
  title?: string;
  desc?: string;
  metricValue?: string;
  metricLabel?: string;
  iconKey?: string;
};

const ICONS: Record<string, ReactNode> = {
  lock: (
    <>
      <rect x="6" y="14" width="20" height="14" rx="3" />
      <path d="M10 14V10a6 6 0 0 1 12 0v4" />
      <circle cx="16" cy="21" r="1.5" />
    </>
  ),
  shield: (
    <>
      <path d="M16 4l10 4v8c0 6-4 10-10 12C10 26 6 22 6 16V8l10-4z" />
      <path d="M11 16l4 4 7-7" />
    </>
  ),
  chart: (
    <>
      <path d="M4 24h24" />
      <path d="M8 20l4-6 4 4 6-10 4 6" />
      <circle cx="22" cy="8" r="1.5" />
    </>
  ),
  cube: (
    <>
      <path d="M16 4l11 6v12l-11 6L5 22V10z" />
      <path d="M5 10l11 6 11-6" />
      <path d="M16 16v12" />
    </>
  ),
  globe: (
    <>
      <circle cx="16" cy="16" r="12" />
      <path d="M4 16h24" />
      <path d="M16 4c4 4 4 20 0 24M16 4c-4 4-4 20 0 24" />
    </>
  ),
  bolt: (
    <>
      <path d="M18 4L8 18h6l-2 10 10-14h-6z" />
    </>
  ),
  key: (
    <>
      <circle cx="10" cy="16" r="6" />
      <path d="M16 16h12M22 12v8M26 12v8" />
    </>
  ),
  signal: (
    <>
      <path d="M5 22v-4M11 22v-9M17 22v-14M23 22v-18" />
    </>
  ),
};

function renderIcon(key: string | undefined, color: string, strokeWidth: number) {
  const k = (key || "lock").toLowerCase();
  const paths = ICONS[k] ?? ICONS.lock;
  return (
    <svg viewBox="0 0 32 32" width={32} height={32} fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      {paths}
    </svg>
  );
}

export function DefaultFeaturesGridBlockView({ block, context }: TypedBlockViewProps<"defaultFeaturesGrid">) {
  const d = block.data as Record<string, unknown>;
  const wrap = context.wrap;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);
  const isMobile = useIsMobile();

  const accent = t.accent;
  const ink = t.ink;
  const inkDim = t.inkDim;
  const lineColor = t.line;
  const cardBg = panelSurfaceBackground(decor, t.panel);
  const cardHoverBg = t.innerBg;
  const padding = Math.max(0, Math.min(80, Number(d.padding) || 28));
  const iconSize = Math.max(40, Math.min(96, Number(d.iconSize) || 56));
  const iconStroke = Math.max(0.5, Math.min(4, Number(d.iconStroke) || 1.6));

  const itemsRaw = Array.isArray(d.items) ? (d.items as FeatureItem[]) : [];
  const items: FeatureItem[] = itemsRaw.length > 0 ? itemsRaw : [
    {
      code: "Защита", title: "Шифрование",
      desc: "AES-256-GCM поверх WireGuard. Новые ключи при каждом подключении — приватность по умолчанию.",
      metricValue: "AES-256", metricLabel: "GCM / X25519", iconKey: "lock",
    },
    {
      code: "Без утечек", title: "Zero Leak",
      desc: "Killswitch на ядре. Утечки DNS, IPv6 и WebRTC заблокированы — упал туннель, встал и трафик.",
      metricValue: "0%", metricLabel: "утечек за 90 дней", iconKey: "shield",
    },
    {
      code: "Скорость", title: "Быстро",
      desc: "Магистрали 10G в каждом регионе. Multi-hop без потери пакетов, реальные 940+ Мбит/с.",
      metricValue: "940", metricLabel: "Мбит/с p50", iconKey: "chart",
    },
  ];

  const hoverEnabled = d.hoverEnabled !== false;
  const transitionMs = Math.max(0, Math.min(2000, Number(d.hoverTransitionMs) || 200));

  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const cardFrame = buildSurfaceEffectFrameProps(context, "card", cardBg);

  const content = (
    <div
      style={{
        width: "100%",
        height: isMobile ? "auto" : "100%",
        display: "grid",
        gridTemplateColumns: isMobile ? "1fr" : `repeat(auto-fit, minmax(min(220px, 100%), 1fr))`,
        gap: isMobile ? 12 : 28,
        boxSizing: "border-box",
        fontFamily: t.monoFont,
        ...decor,
      }}
    >
      {items.map((item, i) => {
        const isHovered = hoverEnabled && hoverIdx === i;
        return (
          <SurfaceEffectFrame
            key={i}
            {...cardFrame}
            targetClassName="grid"
            overflowVisible
            radiusValue={`${t.radius.md}px`}
            className="flex h-full min-w-0"
            targetStyle={{ borderRadius: t.radius.md }}
            style={{ height: "auto", borderRadius: t.radius.md }}
          >
          <article
            onMouseEnter={hoverEnabled ? () => setHoverIdx(i) : undefined}
            onMouseLeave={hoverEnabled ? () => setHoverIdx((cur) => (cur === i ? null : cur)) : undefined}
            style={{
              width: "100%",
              padding: isMobile ? Math.max(16, padding - 8) : padding,
              borderRadius: t.radius.md,
              background: isHovered ? cardHoverBg : cardBg,
              boxShadow: isHovered ? `${panelShadow(t)}, inset 0 0 0 1.5px ${hexToRgba(accent, 0.5)}` : panelShadow(t),
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
              minHeight: isMobile ? 0 : 260,
              minWidth: 0,
              transition: `background ${transitionMs}ms ease, box-shadow ${transitionMs}ms ease, transform ${transitionMs}ms ease`,
              transform: isHovered ? "translateY(-2px)" : undefined,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16 }}>
              {item.code ? (
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "5px 11px 5px 9px",
                    borderRadius: 999,
                    background: hexToRgba(accent, isHovered ? 0.16 : 0.1),
                    color: accent,
                    fontSize: t.font.xs,
                    fontWeight: t.weight.medium,
                    letterSpacing: t.tracking.normal,
                    whiteSpace: "nowrap",
                    transition: `background ${transitionMs}ms ease`,
                  }}
                >
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: accent, flexShrink: 0 }} />
                  {item.code}
                </span>
              ) : <span />}
              <div
                style={{
                  width: iconSize,
                  height: iconSize,
                  borderRadius: t.radius.sm,
                  background: hexToRgba(accent, isHovered ? 0.2 : 0.12),
                  border: `1px solid ${hexToRgba(accent, isHovered ? 0.4 : 0.2)}`,
                  boxSizing: "border-box",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                  transition: `background ${transitionMs}ms ease, border-color ${transitionMs}ms ease, transform ${transitionMs}ms ease`,
                  transform: isHovered ? "scale(1.04)" : undefined,
                }}
              >
                {renderIcon(item.iconKey, accent, iconStroke)}
              </div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
              <div
                style={{
                  fontSize: "clamp(20px, 4.5cqi, 30px)",
                  fontWeight: t.weight.bold,
                  letterSpacing: "-0.01em",
                  lineHeight: 1.1,
                  margin: "28px 0 10px",
                  color: ink,
                }}
              >
                {item.title || ""}
              </div>
              <div
                style={{
                  fontSize: "clamp(12px, 1.6cqi, 13px)",
                  color: inkDim,
                  lineHeight: 1.55,
                  maxWidth: isMobile ? "none" : "34ch",
                  marginBottom: 22,
                  display: "-webkit-box",
                  WebkitBoxOrient: "vertical",
                  WebkitLineClamp: isMobile ? 4 : 6,
                  overflow: "hidden",
                }}
              >
                {item.desc || ""}
              </div>
              <div
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  flexWrap: "nowrap",
                  gap: 8,
                  marginTop: "auto",
                  borderTop: `1px solid ${lineColor}`,
                  paddingTop: 14,
                }}
              >
                <span style={{ width: 8, height: 8, borderRadius: 999, background: accent, alignSelf: "center", flexShrink: 0 }} />
                <span
                  style={{
                    fontSize: "clamp(18px, 3cqi, 24px)",
                    fontWeight: t.weight.bold,
                    color: ink,
                    fontVariantNumeric: "tabular-nums",
                    whiteSpace: "nowrap",
                    minWidth: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {item.metricValue || ""}
                </span>
                {item.metricLabel ? (
                  <span
                    style={{
                      fontSize: "clamp(10px, 1.2cqi, 11px)",
                      color: inkDim,
                      marginLeft: 4,
                      minWidth: 0,
                      maxWidth: "46%",
                      flexShrink: 0,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {item.metricLabel}
                  </span>
                ) : null}
              </div>
            </div>
          </article>
          </SurfaceEffectFrame>
        );
      })}
    </div>
  );

  return wrap ? wrap(content, false, true) : content;
}
