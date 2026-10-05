"use client";

import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useAppInfo } from "@/app/AppInfoProvider";
import { useDefaultTheme, panelShadow, pickContrast } from "./defaultTheme";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { hexToRgba } from "@/components/constructor/utils";

type BannerBullet = {
  title?: string;
  desc?: string;
};

const BANNER_CSS = `
.dlb-anim > * { animation: dlb-up .6s cubic-bezier(.22,.9,.28,1) both; }
.dlb-anim > *:nth-child(2) { animation-delay: 90ms; }
.dlb-anim > *:nth-child(3) { animation-delay: 180ms; }
.dlb-anim > *:nth-child(4) { animation-delay: 270ms; }
@keyframes dlb-up { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: none; } }
.dlb-bullets > * { animation: dlb-up .6s cubic-bezier(.22,.9,.28,1) both; }
.dlb-bullets > *:nth-child(1) { animation-delay: 240ms; }
.dlb-bullets > *:nth-child(2) { animation-delay: 330ms; }
.dlb-bullets > *:nth-child(3) { animation-delay: 420ms; }
.dlb-bullets > *:nth-child(4) { animation-delay: 510ms; }
.dlb-orb-a { animation: dlb-float-a 14s ease-in-out infinite alternate; }
.dlb-orb-b { animation: dlb-float-b 18s ease-in-out infinite alternate; }
@keyframes dlb-float-a { from { transform: translate(0, 0); } to { transform: translate(-30px, 24px); } }
@keyframes dlb-float-b { from { transform: translate(0, 0); } to { transform: translate(26px, -20px); } }
.dlb-ring { animation: dlb-spin 70s linear infinite; }
@keyframes dlb-spin { to { transform: rotate(360deg); } }
.dlb-dot { animation: dlb-pulse 2.2s ease-in-out infinite; }
@keyframes dlb-pulse { 0%, 100% { box-shadow: 0 0 0 0 var(--dlb-pulse); } 55% { box-shadow: 0 0 0 7px transparent; } }
`;

export function DefaultLoginBannerBlockView({ block, context }: TypedBlockViewProps<"defaultLoginBanner">) {
  const d = block.data as Record<string, unknown>;
  const wrap = context.wrap;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);
  const { projectName } = useAppInfo();

  const rawLogo = typeof d.logoText === "string" ? d.logoText.trim() : "";
  const logoText = rawLogo !== "" ? rawLogo : projectName;
  const title = typeof d.title === "string" ? d.title : "Рады видеть вас";
  const subtitle = typeof d.subtitle === "string"
    ? d.subtitle
    : "Войдите, чтобы управлять подписками, ключами и устройствами.";
  const footnote = typeof d.footnote === "string" ? d.footnote : "Подключение занимает пару минут";

  const bulletsRaw = Array.isArray(d.bullets) ? (d.bullets as BannerBullet[]) : [];
  const bullets: BannerBullet[] = bulletsRaw.length > 0 ? bulletsRaw : [
    { title: "Без логов", desc: "Мы не храним историю ваших подключений." },
    { title: "Любые устройства", desc: "Телефон, компьютер, роутер и даже телевизор." },
    { title: "Поддержка 24/7", desc: "Отвечаем быстро и по делу." },
  ];

  const onAccent = pickContrast(t.accent);

  const content = (
    <div
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        overflow: "hidden",
        borderRadius: t.radius.md,
        background: `radial-gradient(95% 90% at 100% 0%, ${hexToRgba(t.accent, 0.08)}, transparent 55%), ${t.panel}`,
        boxShadow: panelShadow(t),
        fontFamily: t.monoFont,
        containerType: "inline-size",
        boxSizing: "border-box",
        ...decor,
      }}
    >
      <style>{BANNER_CSS}</style>

      <div
        className="dlb-orb-a"
        aria-hidden
        style={{
          position: "absolute",
          top: -120,
          right: -100,
          width: 280,
          height: 280,
          borderRadius: "50%",
          background: hexToRgba(t.accent, 0.1),
          filter: "blur(48px)",
          pointerEvents: "none",
        }}
      />
      <div
        className="dlb-orb-b"
        aria-hidden
        style={{
          position: "absolute",
          bottom: -110,
          left: -80,
          width: 240,
          height: 240,
          borderRadius: "50%",
          background: hexToRgba(t.accent, 0.07),
          filter: "blur(44px)",
          pointerEvents: "none",
        }}
      />
      <div
        className="dlb-ring"
        aria-hidden
        style={{
          position: "absolute",
          top: -120,
          right: -110,
          width: 320,
          height: 320,
          borderRadius: "50%",
          border: `1px dashed ${hexToRgba(t.accent, 0.25)}`,
          pointerEvents: "none",
        }}
      />
      <div
        aria-hidden
        style={{
          position: "absolute",
          top: -80,
          right: -70,
          width: 230,
          height: 230,
          borderRadius: "50%",
          border: `1px solid ${hexToRgba(t.accent, 0.14)}`,
          pointerEvents: "none",
        }}
      />

      <div
        className="dlb-anim"
        style={{
          position: "relative",
          zIndex: 1,
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          gap: "clamp(14px, 2.2cqi, 24px)",
          padding: "clamp(22px, 3.4cqi, 40px)",
          boxSizing: "border-box",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span
            style={{
              width: t.px(32),
              height: t.px(32),
              borderRadius: 10,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: t.accent,
              color: onAccent,
              fontWeight: t.weight.bold,
              fontSize: t.font.md,
            }}
          >
            {logoText.slice(0, 1).toUpperCase()}
          </span>
          <span style={{ fontSize: t.font.md, fontWeight: t.weight.bold, color: t.ink }}>{logoText}</span>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div
            style={{
              fontSize: "clamp(22px, 4cqi, 34px)",
              lineHeight: 1.1,
              letterSpacing: "-0.02em",
              fontWeight: t.weight.bold,
              color: t.ink,
            }}
          >
            {title}
          </div>
          <div style={{ maxWidth: 420, fontSize: "clamp(13px, 1.7cqi, 15px)", lineHeight: 1.55, color: t.inkDim }}>
            {subtitle}
          </div>
        </div>

        <div className="dlb-bullets" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {bullets.map((b, i) => (
            <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 11 }}>
              <span
                style={{
                  flexShrink: 0,
                  width: 24,
                  height: 24,
                  borderRadius: 8,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: hexToRgba(t.accent, 0.12),
                  color: t.accent,
                  fontSize: 12,
                  fontWeight: t.weight.bold,
                }}
              >
                ✓
              </span>
              <div style={{ minWidth: 0 }}>
                <span style={{ fontSize: t.font.sm, fontWeight: t.weight.bold, color: t.ink }}>{b.title || ""}</span>
                {b.desc ? (
                  <span style={{ fontSize: t.font.sm, lineHeight: 1.5, color: t.inkDim }}>{" — "}{b.desc}</span>
                ) : null}
              </div>
            </div>
          ))}
        </div>

        {footnote ? (
          <div style={{ display: "flex", alignItems: "center", gap: 9, fontSize: t.font.xsPlus, color: t.inkMute }}>
            <span
              className="dlb-dot"
              style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: t.success,
                ["--dlb-pulse" as never]: hexToRgba(t.success, 0.45),
              }}
            />
            {footnote}
          </div>
        ) : null}
      </div>
    </div>
  );

  return wrap ? wrap(content, false, true) : content;
}
