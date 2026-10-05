"use client";


import { pickInitialDevice, pickInitialTraffic } from "@/components/constructor/blockContent/cabinetKit/blockHelpers";
import { useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent as ReactMouseEvent } from "react";
import { buildCheckoutHref, type CardSelection } from "@/components/constructor/blockContent/checkoutHref";
import { pluralize } from "@/lib/plural";
import useSWR from "swr";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, pickContrast, useIsMobile, panelShadow } from "./defaultTheme";
import { panelSurfaceBackground, usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { apiFetchPublic } from "@/lib/api";
import { useFlowPage, flowSelectTariffOverride } from "@/lib/flow-page-provider";
import { hexToRgba } from "@/components/constructor/utils";
import type { WebTariffPublic } from "@/components/constructor/blockContent/tariffTypes";
import type { TariffItem } from "./tariffGridTypes";
import { DEFAULT_DEMO_TARIFFS } from "../blocks/demoTariffs";
import { useTariffConfigPrices } from "@/components/constructor/blockContent/cabinetKit/useTariffConfigPrice";
import { useTariffSubgroups } from "@/components/constructor/blockContent/tariffSubgroups";
import { TariffSubgroupTabs } from "@/components/constructor/blockContent/TariffSubgroupTabs";
import { SurfaceEffectFrame } from "@/components/constructor/SurfaceEffectFrame";
import { buildSurfaceEffectFrameProps } from "@/components/constructor/visualBindings";





/** Бейджи тарифов из формы: список строк «тариф → надпись», старые блоки хранили тот же набор объектом. */
function readTariffBadges(raw: unknown): Record<string, { badge?: string; highlighted?: boolean }> {
  if (Array.isArray(raw)) {
    const out: Record<string, { badge?: string; highlighted?: boolean }> = {};
    for (const row of raw) {
      const item = (row ?? {}) as Record<string, unknown>;
      const id = String(item.tariff ?? "").trim();
      if (id) out[id] = { badge: String(item.badge ?? "") || undefined, highlighted: item.highlighted === true };
    }
    return out;
  }
  return raw && typeof raw === "object" ? (raw as Record<string, { badge?: string; highlighted?: boolean }>) : {};
}

export function DefaultTariffsGridBlockView({ block, context }: TypedBlockViewProps<"defaultTariffsGrid">) {
  const d = block.data as Record<string, unknown>;
  const wrap = context.wrap;
  const isPreview = context.previewMode === true;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);
  const isMobile = useIsMobile();

  const accent = t.accent;
  const ink = t.ink;
  const inkDim = t.inkDim;
  const inkMute = t.inkMute;
  const lineColor = t.line;
  const cardBg = panelSurfaceBackground(decor, t.panel);
  const cardFeaturedBg = t.panel;
  const cardHoverBg = t.innerBg;
  const ctaBg = t.innerBg;
  const ctaText = ink;
  const chipBg = t.innerBg;
  const chipText = inkDim;
  const chipActiveBg = accent;
  const chipActiveText = pickContrast(chipActiveBg);
  const badgeText = pickContrast(accent);

  const showConfigOptions = d.showConfigOptions !== false;
  const devicesLabel = String(d.devicesLabel ?? "Устройств");
  const periodYearForms = String(d.periodYearForms ?? "год/года/лет");
  const periodMonthForms = String(d.periodMonthForms ?? "месяц/месяца/месяцев");
  const periodDayForms = String(d.periodDayForms ?? "день/дня/дней");
  const trafficLabel = String(d.trafficLabel ?? "Трафик, ГБ");
  const unlimitedTrafficLabel = String(d.unlimitedTrafficLabel ?? "Безлимит");
  const trafficUnit = String(d.trafficUnit ?? "ГБ");
  const durationFeatureLabel = String(d.durationFeatureLabel ?? "Срок");
  const autoCtaLabel = String(d.autoCtaLabel ?? "Выбрать");
  const devicesUnit = String(d.devicesUnit ?? "");
  const priceComputingText = String(d.priceComputingText ?? "...");
  const configChooseLabel = String(d.configChooseLabel ?? "Выбрать");
  const configDoneLabel = String(d.configDoneLabel ?? "Готово");
  const configDevicesTitle = String(d.configDevicesTitle ?? "Выберите количество устройств");
  const configTrafficTitle = String(d.configTrafficTitle ?? "Выберите трафик");
  const ctaFlowId = String(d.ctaFlowId ?? "").trim();
  const checkoutSlug = (String(d.checkoutSlug ?? "checkout") || "checkout").trim();

  const padding = Math.max(16, Math.min(80, Number(d.padding) || 28));
  const hoverEnabled = d.hoverEnabled !== false;
  const transitionMs = Math.max(0, Math.min(2000, Number(d.hoverTransitionMs) || 200));

  const flowPage = useFlowPage();
  const flowTariff = flowSelectTariffOverride(flowPage);
  const itemsRaw = Array.isArray(d.items) ? (d.items as TariffItem[]) : [];
  const groupCode = flowTariff.groupCode ?? (String(d.groupCode ?? "").trim() || "basic");
  const autoMode = itemsRaw.length === 0;
  const groupTariffsKey = !isPreview && autoMode
    ? `/api/tariffs/public?group_code=${encodeURIComponent(groupCode)}`
    : null;
  const { data: groupTariffs } = useSWR<WebTariffPublic[]>(
    groupTariffsKey,
    (url: string) => apiFetchPublic<WebTariffPublic[]>(url),
    { revalidateOnFocus: false, dedupingInterval: 300_000 },
  );
  const durationWords = (days: number): string => {
    if (!Number.isFinite(days) || days <= 0) return "";
    if (days % 365 === 0) { const y = days / 365; return `${y} ${pluralize(y, periodYearForms)}`; }
    if (days % 30 === 0) { const m = days / 30; return `${m} ${pluralize(m, periodMonthForms)}`; }
    return `${days} ${pluralize(days, periodDayForms)}`;
  };
  const tariffBadges = readTariffBadges(d.tariffBadges);
  const scopedGroupTariffs = flowTariff.tariffIds
    ? (groupTariffs ?? []).filter((tr) => flowTariff.tariffIds!.includes(tr.id))
    : (groupTariffs ?? []);
  const subgroups = useTariffSubgroups(scopedGroupTariffs, {
    mode: d.subgroupMode,
    subgroup: d.subgroup,
    otherLabel: String(d.subgroupOtherLabel ?? "Остальные"),
  });
  const autoItems: TariffItem[] = useMemo(() => {
    const list = subgroups.tariffs.slice().sort((a, b) => {
      if ((a.sort_order ?? 0) !== (b.sort_order ?? 0)) return (a.sort_order ?? 0) - (b.sort_order ?? 0);
      return a.price_rub - b.price_rub;
    });
    return list.map((tr) => ({
      badge: tariffBadges[String(tr.id)]?.badge || undefined,
      highlighted: tariffBadges[String(tr.id)]?.highlighted === true,
      name: tr.name,
      desc: tr.subgroup_title || "",
      price: tr.price_rub.toLocaleString("ru-RU"),
      currency: "₽",
      period: tr.duration_days > 0 ? `/ ${durationWords(tr.duration_days)}` : "",
      features: [
        {
          label: devicesLabel,
          value: (tr.device_options?.length ?? 0) > 1
            ? `${Math.min(...tr.device_options!)}–${Math.max(...tr.device_options!)}`
            : tr.device_limit && tr.device_limit > 0 ? String(tr.device_limit) : "∞",
        },
        {
          label: trafficLabel,
          value: (() => {
            const pos = (tr.traffic_options_gb ?? []).filter((v) => v > 0);
            if (pos.length > 1) return `${Math.min(...pos)}–${Math.max(...pos)} ${trafficUnit}`;
            return tr.traffic_limit && tr.traffic_limit > 0 ? `${tr.traffic_limit} ${trafficUnit}` : unlimitedTrafficLabel;
          })(),
        },
        { label: durationFeatureLabel, value: durationWords(tr.duration_days) },
      ],
      ctaLabel: autoCtaLabel,
      tariffId: tr.id,
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subgroups.tariffs, JSON.stringify(tariffBadges)]);
  const items: TariffItem[] = itemsRaw.length > 0
    ? itemsRaw.slice(0, 12)
    : !isPreview
      ? autoItems
      : DEFAULT_DEMO_TARIFFS;

  const visible = useMemo(
    () => items.slice(0, 12),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [JSON.stringify(items)],
  );

  const tariffIds = useMemo(() => {
    const set = new Set<number>();
    for (const it of visible) {
      if (typeof it.tariffId === "number" && Number.isInteger(it.tariffId) && it.tariffId > 0) set.add(it.tariffId);
    }
    return Array.from(set);
  }, [visible]);

  const tariffsKey = !isPreview && !autoMode && tariffIds.length > 0
    ? `/api/tariffs/public?tariff_ids=${tariffIds.join(",")}`
    : null;
  const { data: tariffsList } = useSWR<WebTariffPublic[]>(
    tariffsKey,
    (url: string) => apiFetchPublic<WebTariffPublic[]>(url),
    { revalidateOnFocus: false, dedupingInterval: 300_000 },
  );

  const tariffById = useMemo(() => {
    const map = new Map<number, WebTariffPublic>();
    for (const tt of (autoMode ? scopedGroupTariffs : (tariffsList ?? []))) map.set(tt.id, tt);
    return map;
  }, [autoMode, scopedGroupTariffs, tariffsList]);

  const [selections, setSelections] = useState<Record<number, CardSelection>>({});

  useEffect(() => {
    if (!showConfigOptions || isPreview) return;
    setSelections((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const it of visible) {
        const tId = it.tariffId;
        if (typeof tId !== "number") continue;
        const tariff = tariffById.get(tId);
        if (!tariff?.configurable) continue;
        if (next[tId]) continue;
        next[tId] = { device: pickInitialDevice(tariff), traffic: pickInitialTraffic(tariff) };
        changed = true;
      }
      return changed ? next : prev;
    });
  }, [tariffById, visible, showConfigOptions, isPreview]);

  const livePrices = useTariffConfigPrices(
    visible.flatMap((item) => {
      const tId = item.tariffId;
      if (typeof tId !== "number" || !tariffById.get(tId)?.configurable) return [];
      const sel = selections[tId];
      if (!sel) return [];
      return [{ tariffId: tId, deviceLimit: sel.device ?? null, trafficGb: sel.traffic ?? null }];
    }),
    !showConfigOptions || isPreview,
  );

  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const cardFrame = buildSurfaceEffectFrameProps(context, "card", cardBg);
  const [flipOpen, setFlipOpen] = useState<Record<number, boolean>>({});
  const [flipSideState, setFlipSideState] = useState<Record<number, "device" | "traffic">>({});
  const [chosen, setChosen] = useState<Record<number, { device?: boolean; traffic?: boolean }>>({});

  const updateSel = (tId: number, patch: Partial<CardSelection>) => {
    setSelections((prev) => ({ ...prev, [tId]: { ...(prev[tId] ?? {}), ...patch } }));
  };

  const renderChips = (
    options: number[],
    active: number | undefined,
    formatLabel: (v: number) => string,
    onPick: (v: number) => void,
  ) => (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
      {options.map((opt) => {
        const isActive = active === opt;
        return (
          <button
            key={opt}
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onPick(opt);
            }}
            style={{
              padding: "5px 11px",
              fontSize: t.font.xs,
              fontFamily: t.monoFont,
              borderRadius: 999,
              border: `1px solid ${isActive ? chipActiveBg : lineColor}`,
              background: isActive ? chipActiveBg : chipBg,
              color: isActive ? chipActiveText : chipText,
              cursor: "pointer",
              whiteSpace: "nowrap",
              flexShrink: 0,
              transition: `background ${transitionMs}ms ease, color ${transitionMs}ms ease, border-color ${transitionMs}ms ease`,
            }}
          >
            {formatLabel(opt)}
          </button>
        );
      })}
    </div>
  );

  const featureRowStyle = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    minHeight: 42,
    borderBottom: `1px solid ${lineColor}`,
    color: inkDim,
    boxSizing: "border-box" as const,
  };

  const scrollMode = !isMobile && visible.length > 4;
  const scrollWrapRef = useRef<HTMLDivElement | null>(null);
  const [scrollRowW, setScrollRowW] = useState<number | null>(null);
  useEffect(() => {
    if (!scrollMode) return;
    const el = scrollWrapRef.current;
    if (!el) return;
    const cell = (el.closest("[data-block-cell]") as HTMLElement | null) ?? el.parentElement;
    if (!cell) return;
    const measure = () => setScrollRowW(cell.clientWidth);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(cell);
    return () => ro.disconnect();
  }, [scrollMode]);

  const cardsScrollRef = useRef<HTMLDivElement | null>(null);
  const [arrowState, setArrowState] = useState({ left: false, right: false });
  useEffect(() => {
    if (!scrollMode) {
      setArrowState({ left: false, right: false });
      return;
    }
    const el = cardsScrollRef.current;
    if (!el) return;
    const update = () =>
      setArrowState({
        left: el.scrollLeft > 4,
        right: el.scrollLeft + el.clientWidth < el.scrollWidth - 4,
      });
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
    ro?.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      ro?.disconnect();
    };
  }, [scrollMode, scrollRowW, visible.length]);

  const scrollCards = (dir: -1 | 1) => {
    const el = cardsScrollRef.current;
    if (!el) return;
    el.scrollBy({ left: dir * Math.max(280, Math.round(el.clientWidth * 0.85)), behavior: "smooth" });
  };

  const arrowBtnStyle = (side: "left" | "right"): CSSProperties => {
    const s: CSSProperties = {
      position: "absolute",
      top: "50%",
      transform: "translateY(-50%)",
      width: 40,
      height: 40,
      borderRadius: "50%",
      border: "none",
      background: accent,
      color: pickContrast(accent),
      boxShadow: panelShadow(t),
      cursor: "pointer",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      fontSize: 24,
      lineHeight: 1,
      paddingBottom: 3,
      zIndex: 5,
    };
    if (side === "left") s.left = 2;
    else s.right = 2;
    return s;
  };

  const cardsRow = (
    <div
      ref={cardsScrollRef}
      className={scrollMode ? "scrollbar-hide" : undefined}
      style={{
        width: "100%",
        ...(scrollMode
          ? { display: "flex", overflowX: "auto", scrollSnapType: "x mandatory", paddingBottom: 8, minWidth: 0, alignItems: "stretch" }
          : { display: "grid", gridTemplateColumns: isMobile ? "1fr" : `repeat(auto-fit, minmax(min(260px, 100%), 1fr))`, alignItems: "stretch" }),
        gap: 12,
        boxSizing: "border-box",
        fontFamily: t.monoFont,
        ...decor,
      }}
    >
      {visible.map((item, i) => {
        const isFeatured = item.highlighted === true || Boolean(item.badge);
        const isHovered = hoverEnabled && hoverIdx === i;
        const cardActiveBg = isHovered ? cardHoverBg : (isFeatured ? cardFeaturedBg : cardBg);
        const ctaActiveBg = isFeatured || isHovered ? accent : ctaBg;
        const ctaActiveText = isFeatured || isHovered ? pickContrast(accent) : ctaText;

        const tId = item.tariffId;
        const tariff = typeof tId === "number" ? tariffById.get(tId) : undefined;
        const devOpts = tariff?.device_options ?? [];
        const trOpts = tariff?.traffic_options_gb ?? [];
        const hasDevOpts = devOpts.length > 0;
        const hasTrOpts = (trOpts ?? []).length > 0;
        const isConfigurable = Boolean(tariff?.configurable && (hasDevOpts || hasTrOpts));
        const sel = typeof tId === "number" ? selections[tId] : undefined;
        const live = typeof tId === "number" ? livePrices[String(tId)] : undefined;

        const showOptionsBlock = showConfigOptions && isConfigurable && !isPreview;
        const flipSide = (typeof tId === "number" ? flipSideState[tId] : undefined) ?? "device";
        const flipped = Boolean(showOptionsBlock && typeof tId === "number" && flipOpen[tId]);
        const chosenHere = typeof tId === "number" ? chosen[tId] : undefined;

        const displayPrice = (() => {
          if (showOptionsBlock) {
            if (live?.busy) return priceComputingText;
            if (live?.priceRub != null) return live.priceRub.toLocaleString("ru-RU");
            if (tariff) return tariff.price_rub.toLocaleString("ru-RU");
          }
          return item.price ?? "";
        })();

        const ctaHref = (() => {
          const manual = typeof item.ctaHref === "string" ? item.ctaHref.trim() : "";
          if (manual && manual !== "#") return manual;
          if (typeof tId === "number" && tId > 0) {
            return buildCheckoutHref(checkoutSlug, tId, sel ?? {}, hasDevOpts, hasTrOpts);
          }
          return undefined;
        })();

        const ctaFlowClick = ctaFlowId && typeof tId === "number" && tId > 0 && !isPreview
          ? (e: ReactMouseEvent) => {
              e.preventDefault();
              e.stopPropagation();
              import("@/lib/start-flow").then(({ startFlowById }) => {
                const preset: Record<string, unknown> = {
                  selectedTariffId: tId,
                  selectedTariffName: item.name,
                };
                if (tariff) {
                  preset.selectedTariffPrice = tariff.price_rub;
                  preset.selectedTariffIsFree = tariff.price_rub <= 0;
                }
                if (hasDevOpts && sel?.device != null) preset.selectedDeviceLimit = sel.device;
                if (hasTrOpts && sel?.traffic != null) preset.selectedTrafficGb = sel.traffic;
                startFlowById(ctaFlowId, preset);
              });
            }
          : undefined;

        const ctaStyle = {
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          width: "100%",
          padding: "14px 18px",
          borderRadius: t.radius.sm,
          background: ctaActiveBg,
          color: ctaActiveText,
          fontFamily: t.monoFont,
          fontWeight: t.weight.bold,
          fontSize: t.font.sm,
          marginTop: "auto",
          flexShrink: 0,
          textDecoration: "none",
          boxSizing: "border-box" as const,
          border: "none",
          cursor: "pointer",
          transition: `background ${transitionMs}ms ease, color ${transitionMs}ms ease`,
        };

        const cardShell = {
          padding,
          borderRadius: t.radius.md,
          background: cardActiveBg,
          boxShadow: isFeatured
            ? `inset 0 0 0 2px ${accent}, 0 16px 40px -20px ${hexToRgba(accent, 0.5)}`
            : (isHovered ? `${panelShadow(t)}, inset 0 0 0 1.5px ${hexToRgba(accent, 0.5)}` : panelShadow(t)),
          transition: `background ${transitionMs}ms ease, box-shadow ${transitionMs}ms ease`,
          display: "flex",
          flexDirection: "column" as const,
          minWidth: 0,
          boxSizing: "border-box" as const,
          backfaceVisibility: "hidden" as const,
          WebkitBackfaceVisibility: "hidden" as const,
        };

        const priceBlock = (
          <>
            <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginBottom: 6 }}>
              <span
                style={{
                  fontSize: "clamp(34px, 5.5cqi, 52px)",
                  fontWeight: t.weight.bold,
                  letterSpacing: "-0.02em",
                  fontVariantNumeric: "tabular-nums",
                  color: ink,
                  lineHeight: 1,
                }}
              >
                {displayPrice}
              </span>
              {item.currency ? (
                <span style={{ fontSize: "clamp(16px, 1.8cqi, 20px)", color: inkMute, fontWeight: t.weight.medium }}>
                  {item.currency}
                </span>
              ) : null}
            </div>
            {item.period ? (
              <div style={{ fontSize: t.font.xsPlus, color: inkDim, marginBottom: 22 }}>{item.period}</div>
            ) : null}
          </>
        );

        const miniChoose = (kind: "device" | "traffic", label: string) => (
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setFlipSideState((prev) => ({ ...prev, [tId!]: kind }));
              setFlipOpen((prev) => ({ ...prev, [tId!]: true }));
            }}
            style={{
              padding: "3px 12px",
              borderRadius: 999,
              border: `1px solid ${hexToRgba(accent, 0.5)}`,
              background: hexToRgba(accent, 0.1),
              color: ink,
              fontFamily: t.monoFont,
              fontSize: "inherit",
              fontWeight: t.weight.medium,
              cursor: "pointer",
              flexShrink: 0,
              whiteSpace: "nowrap" as const,
            }}
          >
            {label}
          </button>
        );

        const devBtnLabel = chosenHere?.device && sel?.device != null
          ? `${sel.device}${devicesUnit ? " " + devicesUnit : ""}`
          : configChooseLabel;
        const trBtnLabel = chosenHere?.traffic && sel?.traffic != null
          ? (sel.traffic <= 0 ? unlimitedTrafficLabel : `${sel.traffic} ${trafficUnit}`)
          : configChooseLabel;

        const valueStyle = { color: ink, fontWeight: t.weight.medium };

        return (
          <SurfaceEffectFrame
            key={i}
            {...cardFrame}
            targetClassName="grid"
            overflowVisible
            radiusValue={`${t.radius.md}px`}
            className={scrollMode ? "flex min-w-0" : "flex h-full min-w-0"}
            targetStyle={{ borderRadius: t.radius.md }}
            style={{ height: "auto", borderRadius: t.radius.md, ...(scrollMode ? { flex: "0 0 300px", scrollSnapAlign: "start" } : {}) }}
          >
          <div
            onMouseEnter={hoverEnabled ? () => setHoverIdx(i) : undefined}
            onMouseLeave={hoverEnabled ? () => setHoverIdx((cur) => (cur === i ? null : cur)) : undefined}
            style={{
              position: "relative",
              perspective: 1400,
              width: "100%",
              minWidth: 0,
              alignSelf: "stretch",
              transform: isHovered && !flipped ? "translateY(-2px)" : undefined,
              transition: `transform ${transitionMs}ms cubic-bezier(0.2,0.7,0.2,1)`,
              boxSizing: "border-box",
              ...(scrollMode ? { flex: "0 0 300px", scrollSnapAlign: "start" } : {}),
            }}
          >
            <div
              style={{
                position: "relative",
                width: "100%",
                height: "100%",
                transformStyle: "preserve-3d",
                transition: "transform 640ms cubic-bezier(0.34, 0.8, 0.34, 1)",
                transform: flipped ? "rotateY(180deg)" : "none",
              }}
            >
              <article style={{ ...cardShell, position: "relative", width: "100%", height: "100%" }}>
                {item.badge ? (
                  <div
                    style={{ position: "absolute",
                      top: -1,
                      left: "50%",
                      transform: "translateX(-50%)",
                      background: accent,
                      color: badgeText,
                      fontSize: t.font.xxs,
                      letterSpacing: "0.08em",
                      padding: "6px 22px 7px",
                      borderRadius: "0 0 12px 12px",
                      fontWeight: t.weight.bold,
                      whiteSpace: "nowrap",
                      maxWidth: "70%",
                      overflow: "hidden",
                      textOverflow: "ellipsis", }}
                  >
                    {item.badge}
                  </div>
                ) : null}

                <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
                  <div style={{ fontSize: t.font.xs, color: accent, fontWeight: t.weight.medium, marginBottom: 12 }}>
                    {item.code || ""}
                  </div>

                  <div
                    style={{
                      fontSize: "clamp(22px, 3.5cqi, 28px)",
                      fontWeight: t.weight.bold,
                      letterSpacing: "-0.01em",
                      lineHeight: 1.1,
                      minHeight: "2.2em",
                      display: "-webkit-box",
                      WebkitBoxOrient: "vertical",
                      WebkitLineClamp: 2,
                      overflow: "hidden",
                      marginBottom: 10,
                      color: ink,
                    }}
                  >
                    {item.name || ""}
                  </div>

                  {item.desc ? (
                    <div style={{ fontSize: "clamp(12px, 1.4cqi, 13px)", color: inkDim, lineHeight: 1.5, marginBottom: 22 }}>
                      {item.desc}
                    </div>
                  ) : null}

                  {priceBlock}

                  {showOptionsBlock ? (
                    <ul
                      style={{
                        listStyle: "none",
                        padding: 0,
                        margin: "0 0 22px 0",
                        borderTop: `1px solid ${lineColor}`,
                        fontSize: "clamp(11px, 1.3cqi, 13px)",
                      }}
                    >
                      <li style={featureRowStyle}>
                        <span>{devicesLabel}</span>
                        {hasDevOpts
                          ? miniChoose("device", devBtnLabel)
                          : <span style={valueStyle}>{tariff?.device_limit && tariff.device_limit > 0 ? String(tariff.device_limit) : "∞"}</span>}
                      </li>
                      <li style={featureRowStyle}>
                        <span>{trafficLabel}</span>
                        {hasTrOpts
                          ? miniChoose("traffic", trBtnLabel)
                          : <span style={valueStyle}>{tariff?.traffic_limit && tariff.traffic_limit > 0 ? `${tariff.traffic_limit} ${trafficUnit}` : unlimitedTrafficLabel}</span>}
                      </li>
                      {tariff && tariff.duration_days > 0 ? (
                        <li style={featureRowStyle}>
                          <span>Срок</span>
                          <span style={valueStyle}>{durationWords(tariff.duration_days)}</span>
                        </li>
                      ) : null}
                    </ul>
                  ) : (
                    <ul
                      style={{
                        listStyle: "none",
                        padding: 0,
                        margin: "0 0 22px 0",
                        borderTop: `1px solid ${lineColor}`,
                        fontSize: "clamp(11px, 1.3cqi, 13px)",
                      }}
                    >
                      {(item.features || []).slice(0, 3).map((ff, fi) => (
                        <li key={fi} style={featureRowStyle}>
                          <span>{ff.label || ""}</span>
                          <span style={valueStyle}>{ff.value || ""}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                {item.ctaLabel ? (
                  ctaFlowClick ? (
                    <button type="button" onClick={ctaFlowClick} style={ctaStyle}>
                      <span>{item.ctaLabel}</span>
                      <span style={{ marginLeft: 12, transition: "transform 220ms cubic-bezier(0.2,0.7,0.2,1)", transform: isHovered ? "translateX(4px)" : undefined }}>→</span>
                    </button>
                  ) : ctaHref ? (
                    <a href={ctaHref} style={ctaStyle}>
                      <span>{item.ctaLabel}</span>
                      <span style={{ marginLeft: 12, transition: "transform 220ms cubic-bezier(0.2,0.7,0.2,1)", transform: isHovered ? "translateX(4px)" : undefined }}>→</span>
                    </a>
                  ) : (
                    <button type="button" style={ctaStyle}>
                      <span>{item.ctaLabel}</span>
                      <span style={{ marginLeft: 12, transition: "transform 220ms cubic-bezier(0.2,0.7,0.2,1)", transform: isHovered ? "translateX(4px)" : undefined }}>→</span>
                    </button>
                  )
                ) : null}
              </article>

              {showOptionsBlock ? (
                <div style={{ ...cardShell, position: "absolute", inset: 0, transform: "rotateY(180deg)" }}>
                  {priceBlock}
                  <div
                    style={{
                      borderTop: `1px solid ${lineColor}`,
                      paddingTop: 16,
                      marginBottom: 14,
                      fontSize: t.font.sm,
                      fontWeight: t.weight.bold,
                      color: ink,
                    }}
                  >
                    {flipSide === "device" ? configDevicesTitle : configTrafficTitle}
                  </div>
                  <div className="scrollbar-hide" style={{ flex: 1, minHeight: 0, overflowY: "auto", marginBottom: 16 }}>
                    {flipSide === "device"
                      ? renderChips(
                          devOpts,
                          sel?.device,
                          (v) => `${v}${devicesUnit ? " " + devicesUnit : ""}`,
                          (v) => {
                            updateSel(tId!, { device: v });
                            setChosen((prev) => ({ ...prev, [tId!]: { ...(prev[tId!] ?? {}), device: true } }));
                          },
                        )
                      : renderChips(
                          trOpts ?? [],
                          sel?.traffic,
                          (v) => (v <= 0 ? unlimitedTrafficLabel : `${v} ${trafficUnit}`),
                          (v) => {
                            updateSel(tId!, { traffic: v });
                            setChosen((prev) => ({ ...prev, [tId!]: { ...(prev[tId!] ?? {}), traffic: true } }));
                          },
                        )}
                  </div>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      setFlipOpen((prev) => ({ ...prev, [tId!]: false }));
                    }}
                    style={{ ...ctaStyle, justifyContent: "center", background: accent, color: pickContrast(accent) }}
                  >
                    {configDoneLabel}
                  </button>
                </div>
              ) : null}
            </div>
          </div>
          </SurfaceEffectFrame>
        );
      })}
    </div>
  );

  const content = scrollMode ? (
    <div style={{ position: "relative", width: "100%" }}>
      <div ref={scrollWrapRef} style={{ width: scrollRowW ? `${scrollRowW}px` : "100%", maxWidth: scrollRowW ? `${scrollRowW}px` : "100%", minWidth: 0, overflow: "hidden" }}>{cardsRow}</div>
      {arrowState.left ? (
        <button type="button" aria-label="Назад" onClick={() => scrollCards(-1)} style={arrowBtnStyle("left")}>‹</button>
      ) : null}
      {arrowState.right ? (
        <button type="button" aria-label="Вперёд" onClick={() => scrollCards(1)} style={arrowBtnStyle("right")}>›</button>
      ) : null}
    </div>
  ) : (
    cardsRow
  );

  const body = subgroups.showTabs ? (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, width: "100%", minWidth: 0 }}>
      <TariffSubgroupTabs
        tabs={subgroups.tabs}
        active={subgroups.active}
        onSelect={subgroups.setActive}
        accent={accent}
        activeTextColor={pickContrast(accent)}
        textColor={t.inkDim}
        borderColor={hexToRgba(t.ink, 0.18)}
        fontFamily={t.monoFont}
        disabled={isPreview}
      />
      {content}
    </div>
  ) : (
    content
  );

  return wrap ? wrap(body, false, true) : body;
}
