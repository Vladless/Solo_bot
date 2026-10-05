"use client";

import { useState } from "react";
import useSWR from "swr";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import {
  useDefaultTheme,
  panelStyle,
  panelBodyStyle,
  btnSolid,
  pillStyle,
  useIsMobile,
  f,
  parseBlockData,
  apiFetchPublic,
  useLoadedFade,
  CONTROL_R,
} from ".";
import { hexToRgba } from "@/components/constructor/utils";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import type { CabinetGiftCard } from "@/components/constructor/blockData/blocks";
import type { WebTariffPublic } from "@/components/constructor/blockContent/tariffTypes";
import { useBalancedColumns } from "@/components/constructor/blockContent/useBalancedColumns";

const GIFT_CARDS_SCHEMA = {
  source: f.enum(["static", "tariffs"] as const, "tariffs"),
  cards: f.array<CabinetGiftCard>(),
  applyingLabel: f.str("Активируем..."),
  successLabel: f.str("✓ Применён"),
  errorLabel: f.str("✕ Не применён"),
  ctaHrefFallback: f.href(""),
  tariffsGroupCode: f.str(""),
  tariffCheckoutSlug: f.str("checkout"),
  tariffCtaLabel: f.str("Подарить"),
  tariffTagLabel: f.str("ТАРИФ"),
  tariffLoadingText: f.str("Загрузка тарифов..."),
  tariffEmptyText: f.str("Нет доступных тарифов"),
  tariffPeriodFmt: f.str("{days} дн"),
  tariffDevicesFmt: f.str("{n} устр."),
  tariffTrafficFmt: f.str("{n} ГБ"),
  tariffUnlimitedText: f.str("∞"),
};

type CardStatus = { kind: "idle" | "loading" | "ok" | "err"; text: string };

export function DefaultGiftCardsGridBlockView({ block, context }: TypedBlockViewProps<"defaultGiftCardsGrid">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);
  const cfg = parseBlockData(d, GIFT_CARDS_SCHEMA);

  const tariffsUrl = !previewMode && cfg.source === "tariffs"
    ? (cfg.tariffsGroupCode ? `/api/tariffs/public?group_code=${encodeURIComponent(cfg.tariffsGroupCode)}` : "/api/tariffs/public")
    : null;
  const { data: tariffs, isLoading: tariffsLoading } = useSWR<WebTariffPublic[]>(
    tariffsUrl,
    (url: string) => apiFetchPublic<WebTariffPublic[]>(url),
  );

  const tariffCards: CabinetGiftCard[] = (() => {
    if (cfg.source !== "tariffs") return [];
    const list = tariffs ?? [];
    const sorted = [...list].sort((a, b) => {
      if ((a.sort_order ?? 0) !== (b.sort_order ?? 0)) return (a.sort_order ?? 0) - (b.sort_order ?? 0);
      return a.price_rub - b.price_rub;
    });
    return sorted.map((tr) => {
      const devicesText = tr.device_limit && tr.device_limit > 0
        ? cfg.tariffDevicesFmt.replace("{n}", String(tr.device_limit))
        : `${cfg.tariffUnlimitedText} устр.`;
      const trafficText = tr.traffic_limit && tr.traffic_limit > 0
        ? cfg.tariffTrafficFmt.replace("{n}", String(tr.traffic_limit))
        : cfg.tariffUnlimitedText;
      const periodText = tr.duration_days > 0
        ? cfg.tariffPeriodFmt.replace("{days}", String(tr.duration_days))
        : "";
      const description = [periodText, devicesText, trafficText].filter(Boolean).join(" · ");
      const ctaHref = `/${(cfg.tariffCheckoutSlug || "checkout").trim()}?tariff_id=${tr.id}&flow=gift_buy`;
      return {
        tag: cfg.tariffTagLabel,
        title: tr.name,
        description,
        expiresLabel: `${tr.price_rub.toLocaleString("ru-RU")} ₽`,
        ctaLabel: cfg.tariffCtaLabel,
        ctaHref,
        code: "",
        highlight: false,
      };
    });
  })();

  const cards = cfg.source === "tariffs" ? tariffCards : cfg.cards;
  const isMobile = useIsMobile(760);
  const minCardWidth = isMobile ? 240 : 260;
  const { ref: cardGridRef, columns: cardCols } = useBalancedColumns(cards.length, { minCardWidth, maxCols: 4 });
  const showLoading = cfg.source === "tariffs" && !previewMode && tariffsLoading && !tariffs;
  const showEmpty = cfg.source === "tariffs" && !previewMode && !tariffsLoading && tariffCards.length === 0;
  const loadedFade = useLoadedFade(showLoading);

  const [statuses, setStatuses] = useState<Record<number, CardStatus>>({});
  const mut = useAccountMutations();

  const apply = async (idx: number, code: string) => {
    if (previewMode) return;
    setStatuses((s) => ({ ...s, [idx]: { kind: "loading", text: cfg.applyingLabel } }));
    try {
      await mut.applyCoupon(code);
      setStatuses((s) => ({ ...s, [idx]: { kind: "ok", text: cfg.successLabel } }));
    } catch {
      setStatuses((s) => ({ ...s, [idx]: { kind: "err", text: cfg.errorLabel } }));
    }
  };

  if (showLoading) {
    return wrap(
      <div className={ELEMENT_FILL_CLASS} style={{ ...panelStyle(t), padding: t.space.xl, color: t.inkDim, fontSize: t.font.sm, display: "flex", alignItems: "center", justifyContent: "center", ...decor }}>
        {cfg.tariffLoadingText}
      </div>,
      false,
      true,
    );
  }
  if (showEmpty) {
    return wrap(
      <div className={loadedFade ? `${ELEMENT_FILL_CLASS} ${loadedFade}` : ELEMENT_FILL_CLASS} style={{ ...panelStyle(t), padding: t.space.xl, color: t.inkDim, fontSize: t.font.sm, display: "flex", alignItems: "center", justifyContent: "center", ...decor }}>
        {cfg.tariffEmptyText}
      </div>,
      false,
      true,
    );
  }

  return wrap(
    <div
      ref={cardGridRef}
      className={loadedFade ? `${ELEMENT_FILL_CLASS} ${loadedFade}` : ELEMENT_FILL_CLASS}
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${cardCols}, minmax(0, 1fr))`,
        gridAutoRows: "minmax(220px, auto)",
        gap: t.space.lg,
        alignContent: "start",
        ...decor,
      }}
    >
      {cards.map((card, i) => {
        const highlight = Boolean(card.highlight);
        const tagColor = highlight ? t.accent : t.success;
        const status = statuses[i];
        const code = (card.code ?? "").trim();
        const hasCode = code.length > 0;
        const isLoading = status?.kind === "loading";
        const isApplied = status?.kind === "ok";
        const ctaHref = card.ctaHref ?? cfg.ctaHrefFallback;
        const solidBtn = {
          ...btnSolid(t),
          width: "100%",
          marginTop: "auto",
          opacity: isLoading || isApplied ? 0.6 : 1,
          cursor: isLoading || isApplied ? "default" : "pointer",
        };
        const codeChip = {
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: "100%",
          minHeight: 44,
          marginTop: "auto",
          borderRadius: CONTROL_R,
          border: `1px dashed ${t.accent}`,
          background: hexToRgba(t.accent, 0.06),
          color: t.accent,
          fontFamily: t.monoFont,
          fontWeight: t.weight.bold,
          fontSize: t.font.smPlus,
          letterSpacing: "0.04em",
          cursor: isLoading || isApplied ? "default" : "pointer",
          opacity: isLoading || isApplied ? 0.6 : 1,
        };
        const buttonLabel = isLoading
          ? cfg.applyingLabel
          : isApplied
            ? cfg.successLabel
            : (card.ctaLabel ?? "") + (hasCode ? "" : " →");
        return (
          <div
            key={i}
            style={{
              ...panelStyle(t),
              background: highlight ? `linear-gradient(135deg, ${hexToRgba(t.accent, 0.12)}, ${hexToRgba(t.accent, 0.03)})` : t.panel,
              boxShadow: highlight ? `inset 0 0 0 1px ${hexToRgba(t.accent, 0.35)}` : panelStyle(t).boxShadow,
            }}
          >
            <div style={{ ...panelBodyStyle(t), display: "flex", flexDirection: "column", gap: t.space.md, minHeight: 200 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: t.space.sm }}>
                {card.tag ? <span style={pillStyle(t, tagColor)}>{card.tag}</span> : <span />}
                {card.expiresLabel ? <span style={{ fontSize: t.font.sm, fontWeight: t.weight.bold, color: t.ink }}>{card.expiresLabel}</span> : null}
              </div>
              <div
                style={{
                  fontSize: t.font.xxl,
                  fontWeight: t.weight.bold,
                  letterSpacing: "-0.01em",
                  lineHeight: 1.1,
                  color: t.ink,
                }}
              >
                {card.title ?? ""}
              </div>
              <div style={{ fontSize: t.font.sm, color: t.inkDim, lineHeight: 1.5 }}>{card.description ?? ""}</div>
              {hasCode ? (
                <button type="button" disabled={isLoading || isApplied} onClick={() => apply(i, code)} style={codeChip}>
                  {isLoading || isApplied ? buttonLabel : code}
                </button>
              ) : ctaHref ? (
                <a href={ctaHref} style={{ ...solidBtn, textDecoration: "none" }}>{buttonLabel}</a>
              ) : (
                <button type="button" disabled style={{ ...solidBtn, opacity: 0.5, cursor: "default" }}>{buttonLabel}</button>
              )}
              {status && status.kind !== "idle" && status.kind !== "loading" ? (
                <div style={{ fontSize: t.font.xs, color: status.kind === "ok" ? t.success : t.error }}>{status.text}</div>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>,
    false,
    true,
  );
}
