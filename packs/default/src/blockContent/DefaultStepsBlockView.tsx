"use client";

import { useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, useIsMobile, pickContrast, panelShadow, panelShadowSm, cardEdge, type DefaultTheme } from "./defaultTheme";
import { panelSurfaceBackground, usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { hexToRgba } from "@/components/constructor/utils";
import { SurfaceEffectFrame } from "@/components/constructor/SurfaceEffectFrame";
import { buildSurfaceEffectFrameProps } from "@/components/constructor/visualBindings";

type StepItem = {
  num?: string;
  title?: string;
  desc?: string;
};

const STEPS_FLOW_CSS = `
@keyframes d-steps-flow { 0% { top: 100%; opacity: 0; } 12% { opacity: 1; } 86% { opacity: 1; } 100% { top: -4%; opacity: 0; } }
@keyframes d-steps-ping { 0% { transform: scale(1); opacity: 0.4; } 80% { transform: scale(1.85); opacity: 0; } 100% { transform: scale(1.85); opacity: 0; } }
@keyframes d-steps-float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-5px); } }
`;

function DeviceGlyph({ color, size }: { color: string; size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="7" y="3" width="10" height="18" rx="2.5" />
      <path d="M11 18h2" />
    </svg>
  );
}

function ShieldGlyph({ color, size }: { color: string; size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3l7 3v5c0 4.5-3 8.2-7 9.5C8 19.2 5 15.5 5 11V6l7-3Z" />
      <path d="M9.2 11.8l2 2 3.6-4" />
    </svg>
  );
}

function StepsConnectAnimation({ t, compact }: { t: DefaultTheme; compact: boolean }) {
  const onAccent = pickContrast(t.accent);
  const packets = [0, 1, 2, 3];
  const deviceSize = compact ? 60 : 80;
  const shieldSize = compact ? 88 : 116;
  const trackH = compact ? 100 : 140;
  const pingRadius = Math.round(shieldSize * 0.28);
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 0,
        fontFamily: t.monoFont,
        position: "relative",
      }}
    >
      <style dangerouslySetInnerHTML={{ __html: STEPS_FLOW_CSS }} />
      <div
        className="d-steps-anim"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: deviceSize,
          height: deviceSize,
          borderRadius: Math.round(deviceSize * 0.3),
          background: t.panel,
          boxShadow: `${panelShadowSm(t)}, ${cardEdge(t)}`,
          color: t.ink,
          flexShrink: 0,
          animation: "d-steps-float 4s ease-in-out infinite",
        }}
      >
        <DeviceGlyph color={t.ink} size={Math.round(deviceSize * 0.42)} />
      </div>

      <div style={{ position: "relative", width: "100%", height: trackH, flexShrink: 0, display: "flex", justifyContent: "center" }}>
        <div
          aria-hidden
          style={{
            position: "absolute",
            top: 4,
            bottom: 4,
            width: 2.5,
            borderRadius: 3,
            background: `linear-gradient(${hexToRgba(t.ink, 0.05)}, ${hexToRgba(t.accent, 0.34)}, ${hexToRgba(t.ink, 0.05)})`,
          }}
        />
        {packets.map((p) => (
          <span
            key={p}
            className="d-steps-anim"
            aria-hidden
            style={{
              position: "absolute",
              left: "50%",
              top: "100%",
              width: 10,
              height: 10,
              borderRadius: 999,
              background: t.accent,
              boxShadow: `0 0 0 5px ${hexToRgba(t.accent, 0.12)}`,
              transform: "translateX(-50%)",
              animation: `d-steps-flow 2.8s linear ${p * 0.7}s infinite`,
            }}
          />
        ))}
      </div>

      <span
        style={{
          position: "relative",
          display: "grid",
          placeItems: "center",
          width: shieldSize,
          height: shieldSize,
          borderRadius: pingRadius,
          background: t.accent,
          color: onAccent,
          flexShrink: 0,
        }}
      >
        <ShieldGlyph color={onAccent} size={Math.round(shieldSize * 0.42)} />
        <span className="d-steps-anim" aria-hidden style={{ position: "absolute", inset: 0, borderRadius: pingRadius, background: hexToRgba(t.accent, 0.32), animation: "d-steps-ping 3.4s cubic-bezier(0.2,0.6,0.4,1) infinite", zIndex: -1 }} />
        <span className="d-steps-anim" aria-hidden style={{ position: "absolute", inset: 0, borderRadius: pingRadius, background: hexToRgba(t.accent, 0.32), animation: "d-steps-ping 3.4s cubic-bezier(0.2,0.6,0.4,1) 1.7s infinite", zIndex: -1 }} />
      </span>
    </div>
  );
}

export function DefaultStepsBlockView({ block, context }: TypedBlockViewProps<"defaultSteps">) {
  const d = block.data as Record<string, unknown>;
  const wrap = context.wrap;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);
  const cardBg = panelSurfaceBackground(decor, t.panel);
  const isMobile = useIsMobile();

  const itemsRaw = Array.isArray(d.items) ? (d.items as StepItem[]) : [];
  const items: StepItem[] = itemsRaw.length > 0 ? itemsRaw : [
    { num: "01", title: "Выберите тариф", desc: "Пробный период бесплатно — карта не нужна." },
    { num: "02", title: "Получите ключ", desc: "Ключ доступа появится в личном кабинете сразу после оплаты." },
    { num: "03", title: "Подключитесь", desc: "Отсканируйте QR или вставьте ключ в приложение — готово." },
  ];

  const paddingPx = Math.max(0, Math.min(64, Number(d.padding) || 26));
  const gapPx = Math.max(0, Math.min(64, Number(d.gap) || 18));
  const hoverEnabled = d.hoverEnabled !== false;
  const transitionMs = Math.max(0, Math.min(2000, Number(d.hoverTransitionMs) || 200));
  const sideAnimation = d.sideAnimation === true;
  const animationSide = d.animationSide === "left" ? "left" : "right";
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const cardFrame = buildSurfaceEffectFrameProps(context, "card", t.panel);

  if (sideAnimation) {
    const timeline = (
      <div style={{ position: "relative", display: "flex", flexDirection: "column", gap: Math.max(gapPx, 18) }}>
        <div
          aria-hidden
          style={{
            position: "absolute",
            left: 22,
            top: 24,
            bottom: 24,
            width: 2,
            borderRadius: 2,
            background: `linear-gradient(${hexToRgba(t.accent, 0.4)}, ${hexToRgba(t.accent, 0.1)})`,
          }}
        />
        {items.map((item, i) => {
          const isHovered = hoverEnabled && hoverIdx === i;
          return (
            <div
              key={i}
              onMouseEnter={hoverEnabled ? () => setHoverIdx(i) : undefined}
              onMouseLeave={hoverEnabled ? () => setHoverIdx((cur) => (cur === i ? null : cur)) : undefined}
              style={{ position: "relative", display: "flex", alignItems: "flex-start", gap: t.px(16), minHeight: t.px(58) }}
            >
              <div
                style={{
                  position: "relative",
                  zIndex: 1,
                  flexShrink: 0,
                  width: t.px(46),
                  height: t.px(46),
                  borderRadius: t.radius.sm,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: t.panel,
                  boxShadow: isHovered
                    ? `${panelShadowSm(t)}, inset 0 0 0 1.6px ${hexToRgba(t.accent, 0.6)}`
                    : `${panelShadowSm(t)}, inset 0 0 0 1.5px ${hexToRgba(t.accent, 0.28)}`,
                  color: t.accent,
                  fontSize: t.font.md,
                  fontWeight: t.weight.bold,
                  fontVariantNumeric: "tabular-nums",
                  whiteSpace: "nowrap",
                  transition: `box-shadow ${transitionMs}ms ease`,
                }}
              >
                {item.num || String(i + 1).padStart(2, "0")}
              </div>
              <div style={{ paddingTop: 2, minWidth: 0 }}>
                <div style={{ fontSize: t.font.lg, fontWeight: t.weight.bold, color: t.ink, lineHeight: 1.25 }}>
                  {item.title || ""}
                </div>
                <div
                  style={{
                    fontSize: t.font.smPlus,
                    lineHeight: 1.55,
                    color: t.inkDim,
                    marginTop: 6,
                    display: "-webkit-box",
                    WebkitBoxOrient: "vertical",
                    WebkitLineClamp: 2,
                    overflow: "hidden",
                  }}
                >
                  {item.desc || ""}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    );

    const anim = (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: "100%", minHeight: isMobile ? 280 : 340 }}>
        <StepsConnectAnimation t={t} compact={isMobile} />
      </div>
    );

    const content = (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: isMobile ? "column" : "row",
          alignItems: "center",
          justifyContent: "space-between",
          gap: isMobile ? t.px(28) : t.px(48),
          fontFamily: t.monoFont,
          containerType: "inline-size",
          boxSizing: "border-box",
          ...decor,
        }}
      >
        <div
          style={{
            flex: isMobile ? "0 0 auto" : `1 1 ${t.px(460)}px`,
            maxWidth: isMobile ? "100%" : t.px(560),
            minWidth: 0,
            width: isMobile ? "100%" : undefined,
            order: !isMobile && animationSide === "left" ? 2 : 1,
          }}
        >
          {timeline}
        </div>
        <div
          style={{
            flex: isMobile ? "0 0 auto" : "0 1 360px",
            maxWidth: isMobile ? "100%" : 440,
            minWidth: 0,
            width: isMobile ? "100%" : undefined,
            alignSelf: "stretch",
            display: "flex",
            order: !isMobile && animationSide === "left" ? 1 : 2,
          }}
        >
          {anim}
        </div>
      </div>
    );

    return wrap ? wrap(content, false, true) : content;
  }

  const content = (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "grid",
        gridTemplateColumns: `repeat(auto-fit, minmax(220px, 1fr))`,
        gap: gapPx,
        fontFamily: t.monoFont,
        containerType: "inline-size",
        boxSizing: "border-box",
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
        <div
          onMouseEnter={hoverEnabled ? () => setHoverIdx(i) : undefined}
          onMouseLeave={hoverEnabled ? () => setHoverIdx((cur) => (cur === i ? null : cur)) : undefined}
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 12,
            padding: paddingPx,
            borderRadius: t.radius.md,
            background: cardBg,
            boxShadow: isHovered
              ? `${panelShadow(t)}, inset 0 0 0 1.5px ${hexToRgba(t.accent, 0.5)}`
              : `${panelShadowSm(t)}, ${cardEdge(t)}`,
            transform: isHovered ? "translateY(-2px)" : undefined,
            transition: `transform ${transitionMs}ms ease, box-shadow ${transitionMs}ms ease`,
            minWidth: 0,
            boxSizing: "border-box",
          }}
        >
          <div
            style={{
              width: t.px(46),
              height: t.px(46),
              borderRadius: t.radius.sm,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: hexToRgba(t.accent, isHovered ? 0.2 : 0.14),
              color: t.accent,
              fontSize: t.font.md,
              fontWeight: t.weight.bold,
              fontVariantNumeric: "tabular-nums",
              whiteSpace: "nowrap",
              transition: `background-color ${transitionMs}ms ease`,
            }}
          >
            {item.num || String(i + 1).padStart(2, "0")}
          </div>
          <div style={{ fontSize: t.font.lg, fontWeight: t.weight.bold, color: t.ink, lineHeight: 1.25 }}>
            {item.title || ""}
          </div>
          <div style={{ fontSize: t.font.smPlus, lineHeight: 1.55, color: t.inkDim }}>
            {item.desc || ""}
          </div>
        </div>
        </SurfaceEffectFrame>
        );
      })}
    </div>
  );

  return wrap ? wrap(content, false, true) : content;
}
