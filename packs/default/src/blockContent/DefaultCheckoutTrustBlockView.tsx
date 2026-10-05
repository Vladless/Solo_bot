"use client";

import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, panelShadowSm } from "./defaultTheme";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { hexToRgba } from "@/components/constructor/utils";

type TrustItem = {
  title?: string;
  desc?: string;
};

const DCT_CSS = `
.dct-anim > * { animation: dct-up .55s cubic-bezier(.22,.9,.28,1) both; }
.dct-anim > *:nth-child(2) { animation-delay: 90ms; }
.dct-anim > *:nth-child(3) { animation-delay: 180ms; }
.dct-anim > *:nth-child(4) { animation-delay: 270ms; }
@keyframes dct-up { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: none; } }
.dct-card { transition: transform .2s cubic-bezier(.22,.9,.28,1), box-shadow .2s; }
.dct-card:hover { transform: translateY(-2px); }
`;

const TRUST_ICONS = [
  <path key="shield" strokeLinecap="round" strokeLinejoin="round" d="M12 3l7 3v5c0 4.6-3 8.6-7 10-4-1.4-7-5.4-7-10V6l7-3zM9.5 12l1.8 1.8 3.2-3.6" />,
  <path key="bolt" strokeLinecap="round" strokeLinejoin="round" d="M13 2L4.5 13.5h6L11 22l8.5-11.5h-6L13 2z" />,
  <path key="chat" strokeLinecap="round" strokeLinejoin="round" d="M21 12a8 8 0 01-8 8c-1.4 0-2.7-.33-3.9-.92L3 20l1.1-5.2A8 8 0 1121 12zM8.5 10.5h7M8.5 13.5h4.5" />,
];

export function DefaultCheckoutTrustBlockView({ block, context }: TypedBlockViewProps<"defaultCheckoutTrust">) {
  const d = block.data as Record<string, unknown>;
  const wrap = context.wrap;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);

  const itemsRaw = Array.isArray(d.items) ? (d.items as TrustItem[]) : [];
  const items: TrustItem[] = itemsRaw.length > 0 ? itemsRaw : [
    { title: "Безопасная оплата", desc: "Платёж проходит по защищённому каналу, данные карты мы не видим и не храним." },
    { title: "Мгновенная активация", desc: "Подписка включается автоматически в течение минуты после оплаты." },
    { title: "Поддержка рядом", desc: "Если что-то пойдёт не так — напишите нам, разберёмся быстро." },
  ];

  const methodsLabel = typeof d.methodsLabel === "string" ? d.methodsLabel : "Принимаем";
  const methodsText = typeof d.methodsText === "string" ? d.methodsText : "Visa · Mastercard · МИР · СБП · USDT";
  const methods = methodsText.split("·").map((m) => m.trim()).filter(Boolean);

  const content = (
    <div
      className="dct-anim"
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 14,
        fontFamily: t.monoFont,
        boxSizing: "border-box",
        ...decor,
      }}
    >
      <style>{DCT_CSS}</style>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(min(240px, 100%), 1fr))",
          gap: 12,
          alignItems: "stretch",
          flex: "1 1 auto",
          minHeight: 0,
        }}
      >
        {items.map((item, i) => (
          <div
            key={i}
            className="dct-card"
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 13,
              padding: "16px 18px",
              borderRadius: t.radius.sm + 4,
              background: t.panel,
              boxShadow: panelShadowSm(t),
              minWidth: 0,
            }}
          >
            <span
              style={{
                flexShrink: 0,
                width: 36,
                height: 36,
                borderRadius: 12,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: hexToRgba(t.accent, 0.12),
                color: t.accent,
              }}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
                {TRUST_ICONS[i % TRUST_ICONS.length]}
              </svg>
            </span>
            <div style={{ minWidth: 0 }}>
              <div
                style={{
                  fontSize: t.font.smPlus,
                  fontWeight: t.weight.bold,
                  color: t.ink,
                  lineHeight: 1.25,
                  minHeight: "2.5em",
                  display: "-webkit-box",
                  WebkitBoxOrient: "vertical",
                  WebkitLineClamp: 2,
                  overflow: "hidden",
                }}
              >
                {item.title || ""}
              </div>
              {item.desc ? (
                <div style={{ marginTop: 3, fontSize: t.font.sm, lineHeight: 1.5, color: t.inkDim }}>{item.desc}</div>
              ) : null}
            </div>
          </div>
        ))}
      </div>

      {methods.length > 0 ? (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", flexWrap: "wrap", gap: 8 }}>
          {methodsLabel ? (
            <span style={{ fontSize: t.font.xsPlus, fontWeight: 500, color: t.inkMute, marginRight: 4 }}>{methodsLabel}</span>
          ) : null}
          {methods.map((m, i) => (
            <span
              key={i}
              style={{
                fontSize: t.font.xsPlus,
                fontWeight: 600,
                color: t.inkDim,
                background: t.innerBg,
                border: `1px solid ${t.line}`,
                borderRadius: 999,
                padding: "5px 12px",
              }}
            >
              {m}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );

  return wrap ? wrap(content, false, true) : content;
}
