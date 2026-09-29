"use client";

import { useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, panelShadowSm, cardEdge } from "./defaultTheme";
import { panelSurfaceBackground, usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { hexToRgba } from "@/components/constructor/utils";
import { SurfaceEffectFrame } from "@/components/constructor/SurfaceEffectFrame";
import { buildSurfaceEffectFrameProps } from "@/components/constructor/visualBindings";

type FaqItem = {
  title?: string;
  content?: string;
};

export function DefaultFaqBlockView({ block, context }: TypedBlockViewProps<"defaultFaq">) {
  const d = block.data as Record<string, unknown>;
  const wrap = context.wrap;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);
  const cardBg = panelSurfaceBackground(decor, t.panel);
  const [openIdx, setOpenIdx] = useState<number | null>(null);
  const cardFrame = buildSurfaceEffectFrameProps(context, "card", t.panel);

  const itemsRaw = Array.isArray(d.items) ? (d.items as FaqItem[]) : [];
  const items: FaqItem[] = itemsRaw.length > 0 ? itemsRaw : [
    { title: "Что будет после пробного периода?", content: "Ничего. Подписка не продлевается автоматически — вы сами решаете, продолжать или нет." },
    { title: "На скольких устройствах работает?", content: "Зависит от тарифа. Дополнительные устройства можно докупить в любой момент." },
    { title: "Как быстро происходит подключение?", content: "Сразу после оплаты: ключ появляется в кабинете, подключение занимает пару минут." },
  ];

  const gapPx = Math.max(0, Math.min(40, Number(d.gap) || 14));

  const content = (
    <div
      style={{
        width: "100%",
        display: "flex",
        flexDirection: "column",
        gap: gapPx,
        fontFamily: t.monoFont,
        boxSizing: "border-box",
        ...decor,
      }}
    >
      {items.map((item, i) => {
        const open = openIdx === i;
        return (
          <SurfaceEffectFrame
            key={i}
            {...cardFrame}
            targetClassName="grid"
            overflowVisible
            radiusValue={`${t.radius.md}px`}
            className="w-full"
            targetStyle={{ borderRadius: t.radius.md }}
            style={{ height: "auto", borderRadius: t.radius.md }}
          >
          <div
            style={{
              borderRadius: t.radius.md,
              background: cardBg,
              boxShadow: `${panelShadowSm(t)}, ${cardEdge(t)}`,
              overflow: "hidden",
            }}
          >
            <button
              type="button"
              onClick={() => setOpenIdx(open ? null : i)}
              style={{
                width: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 16,
                padding: "24px 28px",
                background: "transparent",
                border: "none",
                cursor: "pointer",
                textAlign: "left",
                fontFamily: "inherit",
              }}
            >
              <span style={{ fontSize: "clamp(16px, 2cqi, 19px)", fontWeight: t.weight.bold, color: t.ink, lineHeight: 1.35 }}>
                {item.title || ""}
              </span>
              <span
                aria-hidden
                style={{
                  flexShrink: 0,
                  width: 38,
                  height: 38,
                  borderRadius: t.radius.sm,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: open ? t.accent : hexToRgba(t.accent, 0.12),
                  color: open ? (t.isLight ? "#fff" : "#0A0A0A") : t.accent,
                  fontSize: 20,
                  fontWeight: t.weight.medium,
                  transition: "transform 200ms ease, background-color 200ms ease, color 200ms ease",
                  transform: open ? "rotate(45deg)" : "none",
                  lineHeight: 1,
                }}
              >
                +
              </span>
            </button>
            <div
              style={{
                display: "grid",
                gridTemplateRows: open ? "1fr" : "0fr",
                transition: "grid-template-rows 240ms cubic-bezier(0.4, 0, 0.2, 1)",
              }}
            >
              <div style={{ overflow: "hidden", minHeight: 0 }}>
                <div style={{ padding: "0 28px 24px", fontSize: t.font.md, maxWidth: 720, lineHeight: 1.6, color: t.inkDim, whiteSpace: "pre-line" }}>
                  {item.content || ""}
                </div>
              </div>
            </div>
          </div>
          </SurfaceEffectFrame>
        );
      })}
    </div>
  );

  return wrap ? wrap(content, false, true) : content;
}
