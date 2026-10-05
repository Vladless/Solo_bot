"use client";

import { formatDurationLabel } from "@/components/constructor/blockContent/cabinetKit/blockHelpers";
import { useCallback, useEffect, useState } from "react";
import { useViewportIsMobile } from "@/lib/responsive";
import useSWR from "swr";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useScreenFlip } from "@/components/constructor/blockContent/pageFlip";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import {
  useDefaultTheme,
  panelStyle,
  panelHeaderStyle,
  panelBodyStyle,
  btnSolid,
  btnSecondary,
  linkBtn,
  pickContrast,
  pillStyle,
  f,
  parseBlockData,
  useBlockApi,
  useBlockApiMutate,
  apiFetch,
  apiFetchPublic,
  useLoadedFade,
} from ".";
import { hexToRgba } from "@/components/constructor/utils";
import type { WebTariffPublic } from "@/components/constructor/blockContent/tariffTypes";
import { formatPricePerDayValue } from "@/components/constructor/blockContent/tariffUtils";
import { useBalancedColumns } from "@/components/constructor/blockContent/useBalancedColumns";
import { COMPACT_LIST_FROM, DefaultOptionRow, OPTION_ROW_HEIGHT, OPTION_ROW_MIN_WIDTH } from "./DefaultOptionRow";
import { DefaultOptionSlots } from "./layout";
import { MONO_ICON_PATHS } from "@/lib/cabinet-sidebar-icons";
import { DefaultPageDots, useAutoPagedItems } from "./DefaultPagination";
import { formatMoney } from "@/lib/format-number";
import { useBlockNavigate, useBlockPrefetch } from "@/lib/block-navigation";
import { useFlowPage, flowSelectTariffOverride } from "@/lib/flow-page-provider";
import { useTariffConfigPrice } from "@/components/constructor/blockContent/cabinetKit/useTariffConfigPrice";
import { useTariffSubgroups } from "@/components/constructor/blockContent/tariffSubgroups";
import { TariffSubgroupTabs } from "@/components/constructor/blockContent/TariffSubgroupTabs";

const SWITCHER_SCREENS = ["grid", "switch", "config"] as const;

const TARIFF_SWITCHER_SCHEMA = {
  pageLabel: f.str("Страница"),
  panelHeader: f.str("Сменить тариф"),
  panelHeaderHint: f.str(""),
  emptyText: f.str("Тарифы не найдены"),
  loadingText: f.str("Загрузка..."),
  errorText: f.str("Не удалось загрузить тарифы"),
  currentBadge: f.str("Текущий"),
  upgradeLabel: f.str("Перейти →"),
  downgradeLabel: f.str("Сменить"),
  switchLabel: f.str("Выбрать"),
  selectLabel: f.str("Купить"),
  switchTitle: f.str("Смена тарифа"),
  switchCreditLine: f.str("Неиспользованный остаток прежней подписки — {credit} ₽ — вернём на баланс."),
  switchCreditDaysLine: f.str("Неиспользованный остаток — {credit} ₽ — конвертируем в +{days} дн к сроку."),
  switchKeepPeriodLine: f.str("Срок подписки сохранится, доплата только за оставшийся период. Остаток {credit} ₽ идёт в зачёт."),
  switchPayLine: f.str("К оплате: {net} ₽."),
  switchRefundLine: f.str("Доплачивать не нужно, на баланс вернётся {refund} ₽."),
  switchPrimaryLabel: f.str("Подтвердить смену"),
  switchProceedHint: f.str("При смене тарифа счётчик трафика обнуляется."),
  switchErrorText: f.str("Не удалось сменить тариф. Попробуйте позже."),
  switchPreviewUnavailableText: f.str("Предпросчёт недоступен — итоговую сумму увидите после подтверждения."),
  switchDoneTitle: f.str("Тариф изменён ✅"),
  switchDoneCreditLine: f.str("На баланс зачислено {credit} ₽."),
  switchDoneButton: f.str("Готово"),
  checkoutSlug: f.str("checkout"),
  groupCode: f.str(""),
  subgroupMode: f.enum(["all", "tabs", "one"] as const, "all"),
  subgroup: f.str(""),
  subgroupOtherLabel: f.str("Остальные"),
  perDayLabel: f.str("в день"),
  devicesLabel: f.str("устр."),
  trafficLabel: f.str("ГБ"),
  unlimitedText: f.str("∞"),
  backLabel: f.str("← Назад"),
  fromPriceLabel: f.str("от"),
  optionsDevicesLabel: f.str("Устройств"),
  optionsTrafficLabel: f.str("Трафик"),
  priceComputingText: f.str("..."),
  compactListFrom: f.num(COMPACT_LIST_FROM),
};




type RenewSwitchPreview = {
  is_switch?: boolean;
  keeps_period?: boolean;
  credit_to_balance_rub?: number;
  refund_to_balance_rub?: number;
  credit_days?: number;
  credit_value_rub?: number;
  final_price_rub?: number;
  required_amount_rub?: number;
  payment_required?: boolean;
  new_device_limit?: number | null;
  new_traffic_gb?: number | null;
};
type SwitchTarget = { id: number; device?: number; traffic?: number; hasDev: boolean; hasTr: boolean };
type SwitchResult = { busy: boolean; done: boolean; error: string; credited: number; balance: number | null };

export function DefaultTariffSwitcherBlockView({ block, context }: TypedBlockViewProps<"defaultTariffSwitcher">) {
  const navigate = useBlockNavigate();
  const prefetch = useBlockPrefetch();
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);

  const cfg = parseBlockData(d, TARIFF_SWITCHER_SCHEMA);

  const [mode, setModeRaw] = useState<"grid" | "config" | "switch">("grid");
  const screenFlip = useScreenFlip(mode, SWITCHER_SCREENS);
  const [configTariff, setConfigTariff] = useState<WebTariffPublic | null>(null);
  const [sel, setSel] = useState<{ device?: number; traffic?: number }>({});
  const [switchTarget, setSwitchTarget] = useState<SwitchTarget | null>(null);
  const [switchPreview, setSwitchPreview] = useState<{ busy: boolean; data: RenewSwitchPreview | null; error?: boolean }>({ busy: false, data: null });
  const [switchResult, setSwitchResult] = useState<SwitchResult>({ busy: false, done: false, error: "", credited: 0, balance: null });
  const refreshApi = useBlockApiMutate();
  const setMode = setModeRaw;

  const viewportIsMobile = useViewportIsMobile();
  const isPreview = previewMode === true;
  const api = useBlockApi({
    needs: ["activeSubscription"],
    disabled: isPreview,
    syncSelection: false,
  });
  const activeKey = api.activeKey;
  const currentTariffId = activeKey?.tariff_id ?? null;

  const flowTariff = flowSelectTariffOverride(useFlowPage());
  const effectiveGroupCode = flowTariff.groupCode ?? cfg.groupCode;
  const tariffsKey = !isPreview && (effectiveGroupCode ? `/api/tariffs/public?group_code=${encodeURIComponent(effectiveGroupCode)}` : "/api/tariffs/public");
  const { data: tariffs, error, isLoading } = useSWR<WebTariffPublic[]>(
    tariffsKey || null,
    (url: string) => apiFetchPublic<WebTariffPublic[]>(url),
    { revalidateOnFocus: false, dedupingInterval: 300_000 },
  );
  const loadedFade = useLoadedFade(!isPreview && isLoading && !tariffs);

  const previewTariffs: WebTariffPublic[] = [
    { id: 1, name: "PROBE", group_code: "main", duration_days: 30, price_rub: 199, traffic_limit: 30, device_limit: 1, subgroup_title: null, sort_order: 0, vless: true },
    { id: 2, name: "CORE", group_code: "main", duration_days: 30, price_rub: 399, traffic_limit: 100, device_limit: 3, subgroup_title: null, sort_order: 1, vless: true },
    { id: 3, name: "PRIME", group_code: "main", duration_days: 30, price_rub: 599, traffic_limit: null, device_limit: 5, subgroup_title: null, sort_order: 2, vless: true },
  ];

  const scopedTariffs = flowTariff.tariffIds
    ? (tariffs ?? []).filter((tr) => flowTariff.tariffIds!.includes(tr.id))
    : (tariffs ?? []);
  const listAll: WebTariffPublic[] = isPreview ? previewTariffs : scopedTariffs;
  const subgroups = useTariffSubgroups(listAll, {
    mode: cfg.subgroupMode,
    subgroup: cfg.subgroup,
    otherLabel: cfg.subgroupOtherLabel,
  });
  const list = subgroups.tariffs;
  const sorted = [...list].sort((a, b) => {
    if ((a.sort_order ?? 0) !== (b.sort_order ?? 0)) return (a.sort_order ?? 0) - (b.sort_order ?? 0);
    return a.price_rub - b.price_rub;
  });
  const { ref: cardGridRef, columns: cardCols } = useBalancedColumns(sorted.length, { minCardWidth: 220, maxCols: 4 });
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
    if (activeKey) {
      params.set("subKey", activeKey.client_id);
      params.set("flow", "upgrade");
    } else {
      params.set("flow", "buy");
    }
    if (hasDev && picked?.device != null) {
      params.set("include_device", "true");
      params.set("selected_device_limit", String(picked.device));
    }
    if (hasTr && picked?.traffic != null) {
      params.set("include_traffic", "true");
      params.set("selected_traffic_gb", String(picked.traffic));
    }
    const slug = cfg.checkoutSlug.trim() || "checkout";
    navigate(`/${slug}?${params.toString()}`);
  };

  const proceedSwitch = (target: SwitchTarget) => {
    setSwitchTarget(target);
    setSwitchPreview({ busy: true, data: null, error: false });
    setSwitchResult({ busy: false, done: false, error: "", credited: 0, balance: null });
    setMode("switch");
  };

  const confirmSwitch = async (target: SwitchTarget, pv: RenewSwitchPreview | null) => {
    if (isPreview || !activeKey) return;
    if (pv?.payment_required) {
      goCheckout(target.id, { device: target.device, traffic: target.traffic }, target.hasDev, target.hasTr);
      return;
    }
    setSwitchResult({ busy: true, done: false, error: "", credited: 0, balance: null });
    try {
      const res = await apiFetch<RenewSwitchPreview & { ok?: boolean; balance_rub?: number; payment_required?: boolean; payment_url?: string | null }>(
        `/api/keys/${encodeURIComponent(activeKey.client_id)}/renew`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            coupon_code: null,
            tariff_id: target.id,
            selected_device_limit: target.hasDev ? target.device ?? null : null,
            selected_traffic_limit: target.hasTr ? target.traffic ?? null : null,
          }),
        },
      );
      if (res?.payment_required && res.payment_url) {
        navigate(res.payment_url);
        return;
      }
      setSwitchResult({
        busy: false,
        done: true,
        error: "",
        credited: Math.max(0, Math.round(res?.refund_to_balance_rub ?? 0)),
        balance: typeof res?.balance_rub === "number" ? res.balance_rub : null,
      });
      await refreshApi("*");
    } catch {
      setSwitchResult({ busy: false, done: false, error: cfg.switchErrorText, credited: 0, balance: null });
    }
  };

  const onSelect = (tr: WebTariffPublic) => {
    if (isPreview) return;
    const devOpts = tr.device_options ?? [];
    const trOpts = tr.traffic_options_gb ?? [];
    if (tr.configurable && (devOpts.length > 0 || trOpts.length > 0)) {
      setConfigTariff(tr);
      setSel({
        device: devOpts.length > 0 ? (tr.device_limit != null && devOpts.includes(tr.device_limit) ? tr.device_limit : devOpts[0]) : undefined,
        traffic: trOpts.length > 0 ? (tr.traffic_limit != null && trOpts.includes(tr.traffic_limit) ? tr.traffic_limit : trOpts[0]) : undefined,
      });
      setMode("config");
      return;
    }
    if (activeKey) {
      proceedSwitch({ id: tr.id, hasDev: false, hasTr: false });
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

  useEffect(() => {
    if (mode !== "switch" || !switchTarget || !activeKey || isPreview) return;
    let cancelled = false;
    setSwitchPreview((prev) => ({ busy: true, data: prev.data, error: false }));
    (async () => {
      try {
        const data = await apiFetch<RenewSwitchPreview>(`/api/keys/${encodeURIComponent(activeKey.client_id)}/renew?preview=true`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            coupon_code: null,
            tariff_id: switchTarget.id,
            selected_device_limit: switchTarget.hasDev ? switchTarget.device ?? null : null,
            selected_traffic_limit: switchTarget.hasTr ? switchTarget.traffic ?? null : null,
          }),
        });
        if (!cancelled) setSwitchPreview({ busy: false, data, error: false });
      } catch {
        if (!cancelled) setSwitchPreview({ busy: false, data: null, error: true });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [mode, switchTarget, activeKey, isPreview]);

  const subIcon = (size: number) => (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={t.accent} strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {MONO_ICON_PATHS.key}
    </svg>
  );

  const renderTariff = (tr: WebTariffPublic) => {
    const isCurrent = currentTariffId === tr.id;
    const pricePerDay = tr.duration_days > 0 ? tr.price_rub / tr.duration_days : tr.price_rub;
    const period = formatDurationLabel(tr.duration_days);
    const devOpts = tr.device_options ?? [];
    const trOptsPos = (tr.traffic_options_gb ?? []).filter((v) => v > 0);
    const isCfg = Boolean(tr.configurable && ((tr.device_options ?? []).length > 0 || (tr.traffic_options_gb ?? []).length > 0));
    const traffic = trOptsPos.length > 1
      ? `${Math.min(...trOptsPos)}–${Math.max(...trOptsPos)} ${cfg.trafficLabel}`
      : tr.traffic_limit == null || tr.traffic_limit <= 0 ? cfg.unlimitedText : `${tr.traffic_limit} ${cfg.trafficLabel}`;
    const devices = devOpts.length > 1
      ? `${Math.min(...devOpts)}–${Math.max(...devOpts)} ${cfg.devicesLabel}`
      : tr.device_limit == null || tr.device_limit <= 0 ? cfg.unlimitedText : `${tr.device_limit} ${cfg.devicesLabel}`;
    const currentPrice = sorted.find((x) => x.id === currentTariffId)?.price_rub ?? 0;
    const actionLabel = isCurrent
      ? cfg.currentBadge
      : currentTariffId == null
        ? cfg.selectLabel
        : tr.price_rub > currentPrice
          ? cfg.upgradeLabel
          : tr.price_rub < currentPrice
            ? cfg.downgradeLabel
            : cfg.switchLabel;
    if (compactList) {
      return (
        <DefaultOptionRow
          key={tr.id}
          t={t}
          title={tr.name}
          leading={subIcon(22)}
          meta={`${devices} · ${traffic}${period ? ` · ${period}` : ""}`}
          price={`${isCfg ? `${cfg.fromPriceLabel} ` : ""}${formatMoney(tr.price_rub)} ₽`}
          priceNote={paged.cols === 1 ? `~${formatPricePerDayValue(pricePerDay)} ₽ ${cfg.perDayLabel}` : undefined}
          actionLabel={actionLabel}
          badge={isCurrent ? cfg.currentBadge : undefined}
          selected={isCurrent}
          disabled={isCurrent}
          onClick={() => onSelect(tr)}
        />
      );
    }
    return (
      <div
        key={tr.id}
        style={{
          borderRadius: t.radius.md,
          padding: t.space.lg,
          display: "flex",
          flexDirection: "column",
          gap: t.space.md,
          background: isCurrent ? hexToRgba(t.accent, 0.08) : t.innerBg,
          boxShadow: isCurrent ? `inset 0 0 0 1.5px ${hexToRgba(t.accent, 0.4)}` : "none",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: t.space.sm }}>
          <span style={{ fontSize: t.font.lg, fontWeight: t.weight.bold, color: t.ink }}>
            {tr.name}
          </span>
          {isCurrent ? <span style={pillStyle(t, t.accent)}>{cfg.currentBadge}</span> : null}
        </div>
        <div style={{ display: "flex", alignItems: "baseline", gap: t.space.sm, flexWrap: "wrap" }}>
          <span style={{ fontSize: t.font.display, fontWeight: t.weight.bold, color: t.ink, lineHeight: 1, letterSpacing: "-0.02em" }}>
            {isCfg ? `${cfg.fromPriceLabel} ` : ""}{formatMoney(tr.price_rub)} ₽
          </span>
          {period ? (
            <span style={{ fontSize: t.font.xsPlus, color: t.inkDim }}>/ {period}</span>
          ) : null}
          <span style={{ fontSize: t.font.xs, color: t.inkMute, marginLeft: "auto" }}>
            ~{formatPricePerDayValue(pricePerDay)} ₽ {cfg.perDayLabel}
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: t.font.sm, color: t.inkDim }}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span>{cfg.devicesLabel}</span>
            <b style={{ color: t.ink, fontWeight: t.weight.bold }}>{devices}</b>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span>{cfg.trafficLabel}</span>
            <b style={{ color: t.ink, fontWeight: t.weight.bold }}>{traffic}</b>
          </div>
        </div>
        <button
          type="button"
          disabled={isCurrent}
          onClick={() => onSelect(tr)}
          style={{
            ...(isCurrent ? btnSecondary(t) : btnSolid(t)),
            width: "100%",
            cursor: isCurrent ? "default" : "pointer",
            opacity: isCurrent ? 0.6 : 1,
            marginTop: "auto",
          }}
        >
          {isCurrent
            ? cfg.currentBadge
            : currentTariffId == null
              ? cfg.selectLabel
              : tr.price_rub > (sorted.find((x) => x.id === currentTariffId)?.price_rub ?? 0)
                ? cfg.upgradeLabel
                : cfg.switchLabel}
        </button>
      </div>
    );
  };

  const body = (() => {
    if (!isPreview && isLoading && !tariffs) {
      return <div style={{ color: t.inkDim, fontSize: t.font.sm, padding: t.space.lg }}>{cfg.loadingText}</div>;
    }
    if (!isPreview && error) {
      return <div style={{ color: t.error, fontSize: t.font.sm, padding: t.space.lg }}>{cfg.errorText}</div>;
    }
    if (sorted.length === 0) {
      return <div style={{ color: t.inkDim, fontSize: t.font.sm, padding: t.space.lg }}>{cfg.emptyText}</div>;
    }
    return (
      <div ref={setCardGrid} style={{ display: "flex", flexDirection: "column", gap: subgroups.showTabs ? t.space.md : 0, minHeight: 0, flex: "1 1 auto" }}>
        {subgroups.showTabs ? (
          <TariffSubgroupTabs
            tabs={subgroups.tabs}
            active={subgroups.active}
            onSelect={subgroups.setActive}
            accent={t.accent}
            activeTextColor={pickContrast(t.accent)}
            textColor={t.inkDim}
            borderColor={t.line}
            fontSize={t.font.sm}
            disabled={isPreview}
          />
        ) : null}
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
                  overflow: "hidden",
                }
              : { display: "grid", gridTemplateColumns: `repeat(${cardCols}, minmax(0, 1fr))`, gap: t.space.md }
          }
        >
          {(compactList ? paged.pageItems : sorted).map(renderTariff)}
          {compactList ? <DefaultOptionSlots count={paged.perPage - paged.pageItems.length} /> : null}
        </div>
        {compactList ? (
          <DefaultPageDots t={t} page={paged.page} totalPages={paged.totalPages} onPick={paged.setPage} label={cfg.pageLabel} />
        ) : null}
      </div>
    );
  })();

  if (mode === "config" && configTariff) {
    const devOpts = configTariff.device_options ?? [];
    const trOpts = configTariff.traffic_options_gb ?? [];
    const priceText = configPrice.busy ? cfg.priceComputingText : formatMoney(configPrice.priceRub ?? configTariff.price_rub);
    const isCurrentCfg = currentTariffId === configTariff.id;
    const confirmLabel = currentTariffId == null
      ? cfg.selectLabel
      : configTariff.price_rub > (sorted.find((x) => x.id === currentTariffId)?.price_rub ?? 0)
        ? cfg.upgradeLabel
        : cfg.switchLabel;
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
          <button type="button" onClick={() => setMode("grid")} style={{ ...linkBtn(t), color: t.inkDim }}>
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
              <div style={{ fontSize: t.font.xs, color: t.inkDim, marginBottom: 8 }}>{cfg.optionsDevicesLabel}</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {devOpts.map((v) => chip(sel.device === v, String(v), () => setSel((p) => ({ ...p, device: v }))))}
              </div>
            </div>
          ) : null}
          {trOpts.length > 0 ? (
            <div>
              <div style={{ fontSize: t.font.xs, color: t.inkDim, marginBottom: 8 }}>{cfg.optionsTrafficLabel}</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {trOpts.map((v) => chip(sel.traffic === v, v <= 0 ? cfg.unlimitedText : `${v} ${cfg.trafficLabel}`, () => setSel((p) => ({ ...p, traffic: v }))))}
              </div>
            </div>
          ) : null}
          <button
            type="button"
            disabled={isCurrentCfg}
            onClick={() =>
              activeKey
                ? proceedSwitch({ id: configTariff.id, device: sel.device, traffic: sel.traffic, hasDev: devOpts.length > 0, hasTr: trOpts.length > 0 })
                : goCheckout(configTariff.id, sel, devOpts.length > 0, trOpts.length > 0)
            }
            style={{ ...btnSolid(t), width: "100%", justifyContent: "center", marginTop: "auto", opacity: isCurrentCfg ? 0.6 : 1 }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>,
      false,
      true,
    );
  }

  if (mode === "switch" && switchTarget) {
    const pv = switchPreview.data;
    const credit = Math.max(0, Math.round(pv?.credit_to_balance_rub ?? 0));
    const refund = Math.max(0, Math.round(pv?.refund_to_balance_rub ?? 0));
    const net = Math.round(pv?.final_price_rub ?? 0);
    const creditDays = Math.max(0, Math.round(pv?.credit_days ?? 0));
    const creditValue = Math.max(0, Math.round(pv?.credit_value_rub ?? credit));
    const keepsPeriod = Boolean(pv?.keeps_period);
    const targetName = sorted.find((x) => x.id === switchTarget.id)?.name ?? "";
    const creditLine = keepsPeriod
      ? cfg.switchKeepPeriodLine.replace("{credit}", formatMoney(creditValue))
      : creditDays > 0
        ? cfg.switchCreditDaysLine.replace("{credit}", formatMoney(creditValue)).replace("{days}", String(creditDays))
        : cfg.switchCreditLine.replace("{credit}", formatMoney(credit));
    const payLine =
      net > 0
        ? cfg.switchPayLine.replace("{net}", formatMoney(net))
        : cfg.switchRefundLine.replace("{refund}", formatMoney(refund));
    const busy = switchResult.busy || switchPreview.busy;

    const switchBody = switchResult.done ? (
      <div style={{ display: "flex", flexDirection: "column", gap: t.space.md, margin: "auto 0", textAlign: "center", alignItems: "center" }}>
        <span style={{ fontSize: t.font.xl, fontWeight: t.weight.bold, color: t.ink }}>{cfg.switchDoneTitle}</span>
        {switchResult.credited > 0 ? (
          <span style={{ fontSize: t.font.sm, color: t.accent, fontWeight: t.weight.bold }}>
            {cfg.switchDoneCreditLine.replace("{credit}", formatMoney(switchResult.credited))}
          </span>
        ) : null}
        <button type="button" onClick={() => setMode("grid")} style={{ ...btnSolid(t), justifyContent: "center", paddingLeft: t.space.lg, paddingRight: t.space.lg }}>
          {cfg.switchDoneButton}
        </button>
      </div>
    ) : (
      <>
        {targetName ? (
          <span style={{ fontSize: t.font.xl, fontWeight: t.weight.bold, color: t.ink }}>{targetName}</span>
        ) : null}
        {creditValue > 0 || keepsPeriod ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: t.font.sm, color: t.inkDim, lineHeight: 1.5 }}>
            <span>{creditLine}</span>
            <span style={{ color: t.ink, fontWeight: t.weight.bold }}>{payLine}</span>
          </div>
        ) : null}
        <span style={{ fontSize: t.font.xs, color: t.inkMute }}>{cfg.switchProceedHint}</span>
        {switchPreview.error && !switchPreview.busy ? (
          <span style={{ fontSize: t.font.xs, color: t.inkMute }}>{cfg.switchPreviewUnavailableText}</span>
        ) : null}
        {switchResult.error ? (
          <span style={{ fontSize: t.font.sm, color: t.error }}>{switchResult.error}</span>
        ) : null}
        <button
          type="button"
          disabled={busy}
          onClick={() => void confirmSwitch(switchTarget, pv)}
          style={{ ...btnSolid(t), width: "100%", justifyContent: "center", marginTop: "auto", opacity: busy ? 0.6 : 1 }}
        >
          {switchResult.busy ? cfg.priceComputingText : cfg.switchPrimaryLabel}
        </button>
      </>
    );

    return wrap(
      <div key="switch" ref={screenFlip} className={ELEMENT_FILL_CLASS} style={{ ...panelStyle(t), ...decor }}>
        <div style={panelHeaderStyle(t)}>
          <button type="button" onClick={() => setMode("grid")} style={{ ...linkBtn(t), color: t.inkDim }}>
            {cfg.backLabel}
          </button>
          <b style={{ color: t.ink, fontWeight: t.weight.bold }}>{cfg.switchTitle}</b>
        </div>
        <div className="scrollbar-hide" style={{ ...panelBodyStyle(t), minHeight: 0, flex: "1 1 auto", overflowY: "auto", display: "flex", flexDirection: "column", gap: t.space.lg }}>
          {switchBody}
        </div>
      </div>,
      false,
      true,
    );
  }

  return wrap(
    <div key="grid" ref={screenFlip} onPointerEnter={() => prefetch(`/${cfg.checkoutSlug.trim() || "checkout"}`)} className={ELEMENT_FILL_CLASS} style={{ ...panelStyle(t), ...decor }}>
      <div style={panelHeaderStyle(t)}>
        <b style={{ color: t.ink, fontWeight: t.weight.bold }}>{cfg.panelHeader}</b>
        <span>{cfg.panelHeaderHint}</span>
      </div>
      <div className={loadedFade} style={{ ...panelBodyStyle(t), display: "flex", flexDirection: "column", gap: t.space.mdPlus }}>{body}</div>
    </div>,
    false,
    true,
  );
}
