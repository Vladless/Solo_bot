"use client";

import { useCallback, useState } from "react";
import { useViewportIsMobile } from "@/lib/responsive";
import useSWR from "swr";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import {
  useDefaultTheme,
  panelShadow,
  panelStyle,
  panelHeaderStyle,
  panelBodyStyle,
  cardEdge,
  linkBtn,
  pickContrast,
  blendHex,
  btnSolid,
  f,
  parseBlockData,
  apiFetchPublic,
  useLoadedFade,
  useIsMobile,
  actionsRow,
  actionsItem,
  CONTROL_H,
  CONTROL_R,
} from ".";
import { useBalancedColumns } from "@/components/constructor/blockContent/useBalancedColumns";
import { COMPACT_LIST_FROM, DefaultOptionRow, OPTION_ROW_HEIGHT, OPTION_ROW_MIN_WIDTH } from "./DefaultOptionRow";
import { DefaultOptionSlots } from "./layout";
import { DefaultPageDots, useAutoPagedItems } from "./DefaultPagination";
import { hexToRgba } from "@/components/constructor/utils";
import { useScreenFlip } from "@/components/constructor/blockContent/pageFlip";
import type { WebTariffPublic } from "@/components/constructor/blockContent/tariffTypes";
import { formatPeriod, formatPricePerDayValue } from "@/components/constructor/blockContent/tariffUtils";
import { useBlockNavigate } from "@/lib/block-navigation";
import { useTariffConfigPrice } from "@/components/constructor/blockContent/cabinetKit/useTariffConfigPrice";

const GIFT_SCREENS = ["cta", "list", "config"] as const;

const GIFT_CTA_SCHEMA = {
  pageLabel: f.str("Страница"),
  panelHint: f.str("Подари подписку"),
  title: f.str("Подарить подписку"),
  titleAccent: f.str(""),
  description: f.str("Выберите тариф — получатель активирует код в один клик. Работает для любого нового аккаунта."),
  ctaLabel: f.str("Выбрать подарок"),
  groupCode: f.str(""),
  checkoutSlug: f.str("checkout"),
  emptyText: f.str("Нет доступных тарифов"),
  loadingText: f.str("Загрузка тарифов..."),
  errorText: f.str("Не удалось загрузить тарифы"),
  pickLabel: f.str("Подарить →"),
  backLabel: f.str("← Назад"),
  perDayLabel: f.str("в день"),
  unlimitedText: f.str("∞"),
  fromPriceLabel: f.str("от"),
  devicesLabel: f.str("Устройств"),
  trafficLabel: f.str("Трафик, ГБ"),
  priceComputingText: f.str("..."),
  compactListFrom: f.num(COMPACT_LIST_FROM),
};

export function DefaultGiftCtaBlockView({ block, context }: TypedBlockViewProps<"defaultGiftCta">) {
  const navigate = useBlockNavigate();
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const isMobile = useIsMobile();
  const decor = usePanelDecor(t.panel);
  const cfg = parseBlockData(d, GIFT_CTA_SCHEMA);

  const [mode, setMode] = useState<"cta" | "list" | "config">("cta");
  const screenFlip = useScreenFlip(mode, GIFT_SCREENS);
  const [hoverId, setHoverId] = useState<number | null>(null);
  const [configTariff, setConfigTariff] = useState<WebTariffPublic | null>(null);
  const [sel, setSel] = useState<{ device?: number; traffic?: number }>({});
  const switchMode = (m: "cta" | "list" | "config") => setMode(m);

  const viewportIsMobile = useViewportIsMobile();
  const isPreview = previewMode === true;
  const tariffsUrl = !isPreview
    ? (cfg.groupCode ? `/api/tariffs/public?group_code=${encodeURIComponent(cfg.groupCode)}` : "/api/tariffs/public")
    : null;
  const { data: tariffs, isLoading, error } = useSWR<WebTariffPublic[]>(
    tariffsUrl,
    (url: string) => apiFetchPublic<WebTariffPublic[]>(url),
    { revalidateOnFocus: false, dedupingInterval: 300_000 },
  );
  const loadedFade = useLoadedFade(!isPreview && mode === "list" && isLoading && !tariffs);

  const previewTariffs: WebTariffPublic[] = [
    { id: 1, name: "PROBE", group_code: "main", duration_days: 30, price_rub: 199, traffic_limit: 30, device_limit: 1, subgroup_title: null, sort_order: 0, vless: true },
    { id: 2, name: "CORE", group_code: "main", duration_days: 30, price_rub: 399, traffic_limit: 100, device_limit: 3, subgroup_title: null, sort_order: 1, vless: true },
    { id: 3, name: "PRIME", group_code: "main", duration_days: 30, price_rub: 599, traffic_limit: null, device_limit: 5, subgroup_title: null, sort_order: 2, vless: true },
  ];
  const list: WebTariffPublic[] = isPreview ? previewTariffs : (tariffs ?? []);
  const sorted = [...list].sort((a, b) => {
    if ((a.sort_order ?? 0) !== (b.sort_order ?? 0)) return (a.sort_order ?? 0) - (b.sort_order ?? 0);
    return a.price_rub - b.price_rub;
  });
  const { ref: cardGridRef, columns: cardCols } = useBalancedColumns(sorted.length, { minCardWidth: 158, maxCols: 4 });
  const listKnown = sorted.length > 0;
  const compactList = viewportIsMobile || (listKnown && cardCols === 1) || sorted.length >= Math.max(1, cfg.compactListFrom);
  const paged = useAutoPagedItems(sorted, { rowHeight: t.px(OPTION_ROW_HEIGHT), gap: 6, minRowWidth: OPTION_ROW_MIN_WIDTH });
  const pagedRef = paged.ref;
  const setCardGrid = useCallback(
    (node: HTMLElement | null) => {
      cardGridRef(node);
      pagedRef(node);
    },
    [cardGridRef, pagedRef],
  );
  const goCheckout = (tariffId: number, picked?: { device?: number; traffic?: number }, hasDev?: boolean, hasTr?: boolean) => {
    if (isPreview) return;
    const params = new URLSearchParams();
    params.set("tariff_id", String(tariffId));
    params.set("flow", "gift_buy");
    if (hasDev && picked?.device != null) {
      params.set("include_device", "true");
      params.set("selected_device_limit", String(picked.device));
    }
    if (hasTr && picked?.traffic != null) {
      params.set("include_traffic", "true");
      params.set("selected_traffic_gb", String(picked.traffic));
    }
    const slug = (cfg.checkoutSlug || "checkout").trim();
    navigate(`/${slug}?${params.toString()}`);
  };

  const onPickTariff = (tr: WebTariffPublic) => {
    if (isPreview) return;
    const devOpts = tr.device_options ?? [];
    const trOpts = tr.traffic_options_gb ?? [];
    if (tr.configurable && (devOpts.length > 0 || trOpts.length > 0)) {
      setConfigTariff(tr);
      setSel({
        device: devOpts.length > 0 ? (tr.device_limit != null && devOpts.includes(tr.device_limit) ? tr.device_limit : devOpts[0]) : undefined,
        traffic: trOpts.length > 0 ? (tr.traffic_limit != null && trOpts.includes(tr.traffic_limit) ? tr.traffic_limit : trOpts[0]) : undefined,
      });
      switchMode("config");
      return;
    }
    goCheckout(tr.id, undefined, false, false);
  };

  const configPrice = useTariffConfigPrice({
    tariffId: configTariff?.id,
    deviceLimit: sel.device ?? null,
    trafficGb: sel.traffic ?? null,
    basePriceRub: configTariff?.price_rub ?? null,
    disabled: mode !== "config" || !configTariff || isPreview,
  });

  if (mode === "config" && configTariff) {
    const devOpts = configTariff.device_options ?? [];
    const trOpts = configTariff.traffic_options_gb ?? [];
    const priceText = configPrice.busy ? cfg.priceComputingText : (configPrice.priceRub ?? configTariff.price_rub).toLocaleString("ru-RU");
    const chip = (active: boolean, label: string, onPick: () => void) => (
      <button
        type="button"
        key={label}
        onClick={onPick}
        style={{
          padding: "7px 15px",
          fontSize: t.font.sm,
          fontFamily: t.monoFont,
          borderRadius: 999,
          border: `1px solid ${active ? t.accent : t.line}`,
          background: active ? t.accent : t.innerBg,
          color: active ? pickContrast(t.accent) : t.inkDim,
          cursor: "pointer",
          whiteSpace: "nowrap",
          transition: "background 160ms ease, color 160ms ease, border-color 160ms ease",
        }}
      >
        {label}
      </button>
    );
    return wrap(
      <div key="config" ref={screenFlip} className={ELEMENT_FILL_CLASS} style={{ ...panelStyle(t), ...decor }}>
        <div style={panelHeaderStyle(t)}>
          <button type="button" onClick={() => switchMode("list")} style={{ ...linkBtn(t), color: t.inkDim }}>
            {cfg.backLabel}
          </button>
        </div>
        <div className="scrollbar-hide" style={{ ...panelBodyStyle(t), minHeight: 0, flex: "1 1 auto", overflowY: "auto", display: "flex", flexDirection: "column", gap: t.space.lg }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: t.space.md, flexWrap: "wrap" }}>
            <span style={{ fontSize: t.font.xl, fontWeight: t.weight.bold, color: t.ink }}>{configTariff.name}</span>
            <span style={{ fontSize: t.font.display, fontWeight: t.weight.bold, color: t.ink, fontVariantNumeric: "tabular-nums", lineHeight: 1 }}>
              {priceText} ₽
            </span>
          </div>
          {devOpts.length > 0 ? (
            <div>
              <div style={{ fontSize: t.font.xs, color: t.inkDim, marginBottom: 8 }}>{cfg.devicesLabel}</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {devOpts.map((v) => chip(sel.device === v, String(v), () => setSel((p) => ({ ...p, device: v }))))}
              </div>
            </div>
          ) : null}
          {trOpts.length > 0 ? (
            <div>
              <div style={{ fontSize: t.font.xs, color: t.inkDim, marginBottom: 8 }}>{cfg.trafficLabel}</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {trOpts.map((v) => chip(sel.traffic === v, v <= 0 ? cfg.unlimitedText : String(v), () => setSel((p) => ({ ...p, traffic: v }))))}
              </div>
            </div>
          ) : null}
          <button
            type="button"
            onClick={() => goCheckout(configTariff.id, sel, devOpts.length > 0, trOpts.length > 0)}
            style={{ ...btnSolid(t), width: "100%", justifyContent: "center", marginTop: "auto" }}
          >
            {cfg.pickLabel}
          </button>
        </div>
      </div>,
      false,
      true,
    );
  }

  if (mode === "list") {
    return wrap(
      <div key="list" ref={screenFlip} className={ELEMENT_FILL_CLASS} style={{ ...panelStyle(t), ...decor }}>
        <div style={panelHeaderStyle(t)}>
          <button
            type="button"
            onClick={() => switchMode("cta")}
            style={{
              ...linkBtn(t),
              color: t.ink,
              fontSize: t.font.md,
              fontWeight: t.weight.bold,
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "9px 16px",
              borderRadius: t.radius.sm,
              background: hexToRgba(t.ink, 0.06),
            }}
          >
            {cfg.backLabel}
          </button>
        </div>
        <div
          className={loadedFade ? `scrollbar-hide ${loadedFade}` : "scrollbar-hide"}
          style={{
            ...panelBodyStyle(t),
            minHeight: 0,
            flex: "1 1 auto",
            overflow: "visible",
            display: "flex",
            flexDirection: "column",
          }}
        >
          {!isPreview && isLoading && !tariffs ? (
            <div style={{ color: t.inkDim, fontSize: t.font.sm }}>{cfg.loadingText}</div>
          ) : error ? (
            <div style={{ color: t.error, fontSize: t.font.sm }}>{cfg.errorText}</div>
          ) : sorted.length === 0 ? (
            <div style={{ color: t.inkDim, fontSize: t.font.sm }}>{cfg.emptyText}</div>
          ) : (
            <div ref={setCardGrid} style={{ display: "flex", flexDirection: "column", minHeight: 0, flex: "1 1 auto" }}>
            <div
              ref={compactList ? paged.flipRef : undefined}
              style={
                compactList
                  ? {
                      display: "grid",
                      gridTemplateColumns: `repeat(${paged.cols}, minmax(0, 1fr))`,
                      gridAutoRows: `${t.px(OPTION_ROW_HEIGHT)}px`,
                      gap: 6,
                      alignContent: "start",
                      minHeight: 0,
                      flex: "1 1 auto",
                      overflow: "visible",
                    }
                  : { display: "grid", gridTemplateColumns: `repeat(${cardCols}, minmax(0, 1fr))`, gridAutoRows: "minmax(140px, auto)", gap: 14, alignContent: "start" }
              }
            >
              {(compactList ? paged.pageItems : sorted).map((tr) => {
                const pricePerDay = tr.duration_days > 0 ? tr.price_rub / tr.duration_days : tr.price_rub;
                const devOpts = tr.device_options ?? [];
                const trOptsPos = (tr.traffic_options_gb ?? []).filter((v) => v > 0);
                const isCfg = Boolean(tr.configurable && ((tr.device_options ?? []).length > 0 || (tr.traffic_options_gb ?? []).length > 0));
                const devicesText = devOpts.length > 1
                  ? `${Math.min(...devOpts)}–${Math.max(...devOpts)} устр.`
                  : tr.device_limit && tr.device_limit > 0 ? `${tr.device_limit} устр.` : `${cfg.unlimitedText} устр.`;
                const trafficText = trOptsPos.length > 1
                  ? `${Math.min(...trOptsPos)}–${Math.max(...trOptsPos)} ГБ`
                  : tr.traffic_limit && tr.traffic_limit > 0 ? `${tr.traffic_limit} ГБ` : cfg.unlimitedText;
                const isHovered = hoverId === tr.id;
                const knot = blendHex(t.accent, t.isLight ? "#FFFFFF" : "#000000", 0.25);
                const bow = (size: number) => (
                  <svg width={size} height={Math.round((size * 15.5) / 28)} viewBox="0 0 28 15.5">
                    <path d="M14 7.5 C10 1.5, 2.5 1.5, 2.5 6.5 C2.5 11, 9 12, 14 7.5 Z" fill={t.accent} />
                    <path d="M14 7.5 C18 1.5, 25.5 1.5, 25.5 6.5 C25.5 11, 19 12, 14 7.5 Z" fill={t.accent} />
                    <path d="M11 9 L8 15 L11.5 13.8 Z" fill={knot} />
                    <path d="M17 9 L20 15 L16.5 13.8 Z" fill={knot} />
                    <circle cx="14" cy="7.5" r="2.8" fill={knot} />
                  </svg>
                );
                const priceText = `${isCfg ? `${cfg.fromPriceLabel} ` : ""}${tr.price_rub.toLocaleString("ru-RU")} ₽`;
                const perDayText = `~${formatPricePerDayValue(pricePerDay)} ₽ ${cfg.perDayLabel}`;
                const metaText = `${devicesText} · ${trafficText}${tr.duration_days > 0 ? ` · ${formatPeriod(tr.duration_days)}` : ""}`;
                if (compactList) {
                  return (
                    <DefaultOptionRow
                      key={tr.id}
                      t={t}
                      title={tr.name}
                      meta={metaText}
                      price={priceText}
                      priceNote={paged.cols === 1 ? perDayText : undefined}
                      actionLabel={cfg.pickLabel}
                      leading={bow(30)}
                      onClick={() => onPickTariff(tr)}
                    />
                  );
                }
                return (
                  <button
                    type="button"
                    key={tr.id}
                    onClick={() => onPickTariff(tr)}
                    onMouseEnter={() => setHoverId(tr.id)}
                    onMouseLeave={() => setHoverId((cur) => (cur === tr.id ? null : cur))}
                    style={{
                      position: "relative",
                      overflow: "visible",
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "flex-start",
                      justifyContent: "center",
                      gap: 6,
                      padding: "52px 22px 20px",
                      borderRadius: t.radius.sm,
                      background: t.innerBg,
                      border: "none",
                      boxShadow: isHovered ? `inset 0 0 0 1.5px ${hexToRgba(t.accent, 0.6)}` : cardEdge(t),
                      color: t.ink,
                      fontFamily: t.monoFont,
                      cursor: "pointer",
                      textAlign: "left",
                      minWidth: 0,
                      transition: "box-shadow 160ms ease, transform 160ms ease",
                      transform: isHovered ? "translateY(-1px)" : undefined,
                    }}
                  >
                    <span
                      aria-hidden
                      style={{
                        position: "absolute",
                        top: 0,
                        left: "50%",
                        transform: isHovered ? "translateX(-50%) rotate(-6deg)" : "translateX(-50%)",
                        transformOrigin: "50% 0",
                        transition: "transform 200ms cubic-bezier(0.3, 1.4, 0.5, 1)",
                        lineHeight: 0,
                        pointerEvents: "none",
                      }}
                    >
                      {bow(58)}
                    </span>
                    <span style={{ position: "relative", fontSize: t.font.xxl, fontWeight: t.weight.bold, color: t.ink, maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {tr.name}
                    </span>
                    <span style={{ position: "relative", fontSize: t.font.md, color: t.ink, opacity: 0.82 }}>
                      {devicesText} · {trafficText}{tr.duration_days > 0 ? ` · ${formatPeriod(tr.duration_days)}` : ""}
                    </span>
                    <span style={{ position: "relative", display: "flex", alignItems: "baseline", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
                      <span style={{ fontSize: t.font.hero, fontWeight: t.weight.bold, color: t.ink, fontVariantNumeric: "tabular-nums", lineHeight: 1 }}>
                        {isCfg ? `${cfg.fromPriceLabel} ` : ""}{tr.price_rub.toLocaleString("ru-RU")} ₽
                      </span>
                      <span style={{ fontSize: t.font.sm, color: t.ink, opacity: 0.6 }}>
                        ~{formatPricePerDayValue(pricePerDay)} ₽ {cfg.perDayLabel}
                      </span>
                    </span>
                    <span style={{ position: "relative", fontSize: t.font.lg, color: t.accent, fontWeight: t.weight.bold, marginTop: 16 }}>
                      {cfg.pickLabel}
                    </span>
                  </button>
                );
              })}
              {compactList ? <DefaultOptionSlots count={paged.perPage - paged.pageItems.length} /> : null}
            </div>
            {compactList ? (
              <DefaultPageDots
                t={t}
                page={paged.page}
                totalPages={paged.totalPages}
                onPick={paged.setPage}
                label={cfg.pageLabel}
              />
            ) : null}
            </div>
          )}
        </div>
      </div>,
      false,
      true,
    );
  }

  const onAccent = pickContrast(t.accent);
  const onAccentDim = hexToRgba(onAccent, 0.78);
  const gradient = `linear-gradient(135deg, ${blendHex(t.accent, "#FFFFFF", 0.08)}, ${blendHex(t.accent, "#000000", 0.16)})`;
  return wrap(
    <div
      key="cta"
      ref={screenFlip}
      className={ELEMENT_FILL_CLASS}
      style={{
        position: "relative",
        overflow: "hidden",
        padding: "26px 26px",
        borderRadius: t.radius.md,
        background: gradient,
        boxShadow: panelShadow(t),
        fontFamily: t.monoFont,
        color: onAccent,
        display: "flex",
        flexDirection: "column",
        height: "100%",
        gap: t.space.smPlus,
        ...decor,
      }}
    >
      <div aria-hidden style={{ position: "absolute", bottom: -80, right: -30, width: 220, height: 220, borderRadius: 999, background: hexToRgba(onAccent, 0.08), pointerEvents: "none" }} />
      <div style={{ position: "relative", zIndex: 1, display: "flex", flexDirection: "column", height: "100%", gap: t.space.smPlus }}>
        <div style={{ fontSize: t.font.smPlus, color: onAccentDim }}>{cfg.panelHint}</div>
        <div style={{ fontSize: t.font.hero, fontWeight: t.weight.bold, letterSpacing: "-0.02em", lineHeight: 1.05 }}>
          {cfg.title}{cfg.titleAccent ? ` ${cfg.titleAccent}` : ""}
        </div>
        {cfg.description ? (
          <div style={{ fontSize: t.font.sm, color: onAccentDim, maxWidth: "60ch", lineHeight: 1.5 }}>{cfg.description}</div>
        ) : null}
        <div style={{ marginTop: "auto", display: "flex", gap: t.space.sm, flexWrap: "wrap", paddingTop: t.space.sm, ...actionsRow(isMobile) }}>
          <button
            type="button"
            onClick={() => switchMode("list")}
            disabled={isPreview}
            style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", minHeight: t.px(CONTROL_H), padding: "0 22px", borderRadius: CONTROL_R, border: "none", background: onAccent, color: t.accent, fontFamily: t.monoFont, fontWeight: t.weight.bold, fontSize: t.font.sm, cursor: isPreview ? "default" : "pointer", opacity: isPreview ? 0.8 : 1, ...actionsItem(isMobile) }}
          >
            {cfg.ctaLabel}
          </button>
        </div>
        {isPreview ? (
          <div style={{ marginTop: t.space.sm, display: "flex", gap: t.space.sm, flexWrap: "wrap" }}>
            {previewTariffs.slice(0, 3).map((tr) => (
              <span key={tr.id} style={{ fontSize: t.font.xs, padding: "6px 12px", borderRadius: 999, background: hexToRgba(onAccent, 0.16), color: onAccent, fontFamily: t.monoFont }}>
                {tr.name} · {tr.price_rub} ₽
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </div>,
    false,
    true,
  );
}
