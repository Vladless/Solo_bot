"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { formatPeriodLabel } from "@/components/constructor/blockContent/cabinetKit/blockHelpers";
import { useViewportIsMobile } from "@/lib/responsive";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useScreenFlip } from "@/components/constructor/blockContent/pageFlip";
import { buildAppImportUrl } from "@/lib/platform-detect";
import { useAppInfo } from "@/app/AppInfoProvider";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { useDefaultTheme, useIsMobile, parseBlockData, useBlockApi, useBlockApiMutate, apiFetch, apiFetchPublic } from "..";
import { useLoadedFade, useLoadedReveal } from "@/components/constructor/blockContent/useLoadedFade";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import { useSectionScreen } from "@/app/(marketing)/landing/SectionScreenContext";
import type { WebTariffPublic } from "@/components/constructor/blockContent/tariffTypes";
import { OPTION_CARD_HEIGHT, OPTION_CARD_MIN_WIDTH } from "../layout";
import { OPTION_ROW_HEIGHT, OPTION_ROW_MIN_WIDTH } from "../DefaultOptionRow";
import { useAutoPagedItems } from "../DefaultPagination";
import type { CabinetTariffFeature } from "@/components/constructor/blockData/blocks";
import { useBlockNavigate } from "@/lib/block-navigation";
import { formatMoney } from "@/lib/format-number";
import { TARIFF_PANEL_SCHEMA, PANEL_SCREENS, formatExpiryDate, type AccountKeyRenewLite } from "./schema";
import { useTariffConfigPrice } from "@/components/constructor/blockContent/cabinetKit/useTariffConfigPrice";

/** Данные, состояние и действия панели тарифа: вид только рисует то, что вернул хук. */
export function useTariffPanel({ block, context }: Pick<TypedBlockViewProps<"defaultTariffPanel">, "block" | "context">) {
  const navigate = useBlockNavigate();
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);

  const cfg = parseBlockData(d, TARIFF_PANEL_SCHEMA);
  const {
    switchTariffHref,
    renewalPrefix, labelDevices, labelTraffic, labelExpiry, labelAlias, trafficUnlimitedText,
    switchTariffMode, switchTariffScreenGroup, switchTariffScreenId,
    switchTariffGroupCode,
    checkoutSlug,
  } = cfg;
  const effectiveSwitchGroupCode = switchTariffGroupCode || (typeof d.tariffsGroupCode === "string" ? d.tariffsGroupCode : "");
  const fallbackPanelHeaderHint = cfg.panelHeaderHint;
  const fallbackPricePrimary = cfg.pricePrimary;
  const fallbackPricePeriodLabel = cfg.pricePeriodLabel;
  const fallbackRenewalText = cfg.renewalText;
  const fallbackFeatures = cfg.features;
  const rawPrimaryLabel = cfg.primaryButtonLabel;
  const adminPrimaryHref = cfg.primaryButtonHref;
  const rawSecondaryLabel = cfg.addonsButtonLabel;
  const adminSecondaryHref = cfg.addonsButtonHref;

  const viewportIsMobile = useViewportIsMobile();
  const isPreview = previewMode === true;
  const api = useBlockApi({
    needs: ["activeSubscription", "activeTariff"],
    disabled: isPreview,
    mock: isPreview,
    syncSelection: true,
  });
  const mut = useAccountMutations();
  const appInfo = useAppInfo();
  const list = api.keys.data ?? [];
  const activeKey = api.activeKey;
  const details = api.activeDetails;
  const isLoadingKeys = api.keys.isLoading;
  const hasKeys = list.length > 0;
  const setSelectedClientId = api.setSelectedClientId;
  const tariff = api.activeTariff;

  const isMobile = useIsMobile();
  const [copied, setCopied] = useState(false);
  const [qrOpen, setQrOpen] = useState(false);
  const [qrImg, setQrImg] = useState<string | null>(null);
  const [qrLoading, setQrLoading] = useState(false);
  const [qrError, setQrError] = useState("");
  const [editingAlias, setEditingAlias] = useState(false);
  const [aliasDraft, setAliasDraft] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameError, setRenameError] = useState("");

  const connectLink = (activeKey?.remnawave_link?.trim() || activeKey?.key?.trim() || "").trim();
  const keyTitle = activeKey?.alias?.trim() || activeKey?.email?.split("@")[0] || activeKey?.client_id?.slice(0, 8) || "VPN";
  const startRename = () => { setAliasDraft(activeKey?.alias ?? ""); setRenameError(""); setEditingAlias(true); };
  const saveRename = async () => {
    if (!activeKey || renameBusy) return;
    const v = aliasDraft.trim();
    setRenameBusy(true);
    setRenameError("");
    try {
      if (v) await mut.renameKey(activeKey.client_id, v);
      setEditingAlias(false);
    } catch {
      setRenameError(cfg.renameErrorText);
    }
    finally { setRenameBusy(false); }
  };
  const onConnect = () => {
    if (!connectLink || isPreview) return;
    const remna = activeKey?.remnawave_link?.trim() || "";
    if (remna) {
      window.open(remna, "_blank", "noopener,noreferrer");
    } else {
      navigate(buildAppImportUrl(connectLink, appInfo.connect));
    }
  };
  const onCopy = () => {
    if (!connectLink || typeof navigator === "undefined" || !navigator.clipboard) return;
    navigator.clipboard.writeText(connectLink).catch(() => { });
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };
  const onShare = async () => {
    if (!connectLink || isPreview) return;
    if (typeof navigator !== "undefined" && navigator.share) {
      try {
        await navigator.share({ title: cfg.shareTitle, text: cfg.shareTitle, url: connectLink });
        return;
      } catch { }
    }
    onCopy();
  };
  const onToggleQr = async () => {
    if (qrOpen) {
      setQrOpen(false);
      return;
    }
    setQrOpen(true);
    if (qrImg || isPreview || !activeKey) return;
    setQrLoading(true);
    setQrError("");
    try {
      const data = await mut.fetchKeyQr(activeKey.client_id, { forceWeb: true });
      setQrImg(data.image_data_url ?? "");
    } catch {
      setQrError(cfg.qrErrorText);
    } finally {
      setQrLoading(false);
    }
  };

  const headerHint = isPreview
    ? fallbackPanelHeaderHint || "PRIME"
    : (tariff?.name || details?.tariff_name || tariff?.subgroup_title || details?.subgroup_title || fallbackPanelHeaderHint || "—");

  const pricePrimary = isPreview
    ? fallbackPricePrimary
    : tariff
      ? formatMoney(tariff.price_rub)
      : "—";

  const pricePeriodLabel = isPreview
    ? fallbackPricePeriodLabel
    : tariff
      ? formatPeriodLabel(tariff.duration_days)
      : "₽";

  const renewalText = isPreview
    ? fallbackRenewalText
    : activeKey?.expiry_time
      ? `${renewalPrefix.toUpperCase()} · ${formatExpiryDate(activeKey.expiry_time).toUpperCase()}`
      : "";

  const apiFeatures: CabinetTariffFeature[] = (() => {
    if (!details) return [];
    const det = details;
    const items: CabinetTariffFeature[] = [];
    if (det.alias || det.email) items.push({ label: labelAlias, value: String(det.alias || det.email) });
    if (typeof det.device_limit === "number") {
      const cur = typeof det.connected_devices === "number" ? det.connected_devices : null;
      items.push({ label: labelDevices, value: cur !== null ? `${cur} / ${det.device_limit}` : String(det.device_limit) });
    }
    if (typeof det.traffic_limit_gb === "number") {
      const used = typeof det.used_traffic_gb === "number" ? `${det.used_traffic_gb.toFixed(1)} / ` : "";
      const limit = det.traffic_limit_gb > 0 ? `${det.traffic_limit_gb} ГБ` : trafficUnlimitedText;
      items.push({ label: labelTraffic, value: `${used}${limit}` });
    }
    if (typeof det.expiry_time === "number" && det.expiry_time > 0) {
      items.push({ label: labelExpiry, value: formatExpiryDate(det.expiry_time) });
    }
    return items;
  })();

  const features = isPreview || apiFeatures.length === 0 ? fallbackFeatures : apiFeatures;

  const tariffNameForLabel = (tariff?.name || details?.tariff_name || tariff?.subgroup_title || details?.subgroup_title || "").trim();
  const substituteTariff = (s: string): string => {
    let out = s;
    if (tariffNameForLabel) {
      out = out.replace(/\{tariff\}/gi, tariffNameForLabel);
      out = out.replace(/\bSHADOW\b/gi, tariffNameForLabel);
    } else {
      out = out.replace(/\{tariff\}/gi, "").replace(/\s+→/, " →").replace(/\s{2,}/g, " ").trim();
    }
    return out;
  };
  const LEGACY_TRANSITION = /^Перейти на (?:SHADOW|\{tariff\})\s*→?$/i;
  const normalizedRawPrimary = LEGACY_TRANSITION.test(rawPrimaryLabel.trim()) ? "Продлить" : rawPrimaryLabel;
  const resolvedPrimaryLabel = substituteTariff(normalizedRawPrimary) || "Продлить";
  const resolvedSecondaryLabel = substituteTariff(rawSecondaryLabel) || "Докупить";

  const tariffIdForUrl = activeKey?.tariff_id ?? null;
  const tariffParam = tariffIdForUrl ? `&tariffId=${tariffIdForUrl}` : "";
  const renewHref = adminPrimaryHref || (activeKey ? `/${(checkoutSlug || "checkout").trim()}?subKey=${encodeURIComponent(activeKey.client_id)}&flow=renew${tariffParam}` : "/tariffs");

  const tariffDeviceOptions: number[] = Array.isArray(tariff?.device_options) ? tariff.device_options : [];
  const tariffTrafficOptions: number[] = Array.isArray(tariff?.traffic_options_gb) ? tariff.traffic_options_gb : [];
  const hasDeviceOptions = tariffDeviceOptions.length > 0;
  const hasTrafficOptions = tariffTrafficOptions.length > 0;
  const hasAnyAddonOption = hasDeviceOptions || hasTrafficOptions;
  const addonsHref = adminSecondaryHref || (activeKey ? `/${(checkoutSlug || "checkout").trim()}?subKey=${encodeURIComponent(activeKey.client_id)}&flow=addons${tariffParam}` : "#");

  const isTrialTariff = !isPreview && tariff?.group_code === "trial";
  const renewAvailable = isPreview || details?.can_renew !== false;
  const showPrimaryButton = (hasKeys || isPreview) && resolvedPrimaryLabel && !isTrialTariff && renewAvailable;

  const refreshApi = useBlockApiMutate();
  const [renewBusy, setRenewBusy] = useState(false);
  const [renewDone, setRenewDone] = useState(false);
  const primaryLabelDisplay = renewBusy ? "Продлеваем..." : renewDone ? "Продлено ✓" : resolvedPrimaryLabel;
  const onRenewClick = async (e: React.MouseEvent) => {
    if (isPreview) {
      e.preventDefault();
      return;
    }
    if (adminPrimaryHref || !activeKey?.client_id) return;
    e.preventDefault();
    if (renewBusy) return;
    if (!tariff || (tariff.configurable && hasDeviceOptions)) {
      navigate(renewHref);
      return;
    }
    setRenewBusy(true);
    try {
      const headers = { "Content-Type": "application/json" };
      const body = JSON.stringify({
        tariff_id: activeKey.tariff_id ?? null,
        coupon_code: null,
        selected_device_limit: null,
        selected_traffic_limit: null,
      });
      const subKey = encodeURIComponent(activeKey.client_id);
      const preview = await apiFetch<AccountKeyRenewLite>(`/api/keys/${subKey}/renew?preview=true`, { method: "POST", headers, body });
      if (preview?.requires_tariff_selection || preview?.payment_required) {
        navigate(renewHref);
        return;
      }
      const res = await apiFetch<AccountKeyRenewLite>(`/api/keys/${subKey}/renew`, { method: "POST", headers, body });
      if (res?.payment_required && res?.payment_url) {
        navigate(res.payment_url);
        return;
      }
      await refreshApi("*");
      setRenewDone(true);
      window.setTimeout(() => setRenewDone(false), 2600);
    } catch {
      navigate(renewHref);
    } finally {
      setRenewBusy(false);
    }
  };

  const [panelMode, setPanelMode] = useState<"main" | "addons" | "switch" | "pickOptions">("main");
  const screenFlip = useScreenFlip(panelMode, PANEL_SCREENS);
  const { revealRef, fadeClass: introFade } = useLoadedReveal(isLoadingKeys);
  const panelModeFadeClass = introFade ? " d-mode-fade" : "";
  const [pickedTariff, setPickedTariff] = useState<WebTariffPublic | null>(null);
  const [pickDeviceDraft, setPickDeviceDraft] = useState("");
  const [pickTrafficDraft, setPickTrafficDraft] = useState("");
  const sectionScreen = useSectionScreen();
  const [submitBusy, setSubmitBusy] = useState(false);

  const pickPrice = useTariffConfigPrice({
    tariffId: pickedTariff?.id,
    deviceLimit: pickDeviceDraft.trim() ? Number(pickDeviceDraft.trim()) : null,
    trafficGb: pickTrafficDraft.trim() ? Number(pickTrafficDraft.trim()) : null,
    basePriceRub: pickedTariff?.price_rub ?? null,
    disabled: panelMode !== "pickOptions" || !pickedTariff || isPreview,
  });
  const pickPriceRub = pickPrice.priceRub;
  const pickPriceBusy = pickPrice.busy;

  const pickPriceText = pickPriceBusy
    ? "..."
    : pickPriceRub != null
      ? `${pickPriceRub.toLocaleString("ru-RU")} ₽`
      : pickedTariff
        ? `${pickedTariff.price_rub.toLocaleString("ru-RU")} ₽`
        : "";

  const openAddons = () => {
    if (isPreview || !activeKey) return;
    setPanelMode("addons");
  };

  const onAddonsClick = (e: React.MouseEvent) => {
    if (isPreview || !activeKey) return;
    if (adminSecondaryHref) return;
    e.preventDefault();
    openAddons();
  };

  const panelCommand = sectionScreen?.activeScreenByGroup?.["tariffPanel"];
  useEffect(() => {
    if (isPreview || !panelCommand || panelCommand === "main") return;
    if (panelCommand === "switch") {
      setPanelMode("switch");
    } else if (panelCommand === "addons") {
      openAddons();
    } else if (panelCommand === "renew") {
      if (!isPreview) navigate(renewHref);
      return;
    }
    sectionScreen?.openScreen("tariffPanel", "main");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panelCommand, isPreview]);

  const closeAddons = () => setPanelMode("main");

  const switchListUrl = !isPreview && switchTariffMode === "inside"
    ? (effectiveSwitchGroupCode ? `/api/tariffs/public?group_code=${encodeURIComponent(effectiveSwitchGroupCode)}` : "/api/tariffs/public")
    : null;
  const { data: switchTariffsList, isLoading: switchListLoading } = useSWR<WebTariffPublic[]>(
    switchListUrl,
    (url: string) => apiFetchPublic<WebTariffPublic[]>(url),
  );
  const switchLoadedFade = useLoadedFade(!isPreview && panelMode === "switch" && switchListLoading && !switchTariffsList);
  const switchCountKnown = isPreview || Array.isArray(switchTariffsList);
  const switchSortedTariffs = useMemo<WebTariffPublic[]>(() => {
    const list: WebTariffPublic[] = isPreview
      ? [
          { id: 1, name: "PROBE", group_code: "main", duration_days: 30, price_rub: 199, traffic_limit: 30, device_limit: 1, subgroup_title: null, sort_order: 0, vless: true },
          { id: 2, name: "CORE", group_code: "main", duration_days: 30, price_rub: 399, traffic_limit: 100, device_limit: 3, subgroup_title: null, sort_order: 1, vless: true },
          { id: 3, name: "PRIME", group_code: "main", duration_days: 30, price_rub: 599, traffic_limit: null, device_limit: 5, subgroup_title: null, sort_order: 2, vless: true },
        ]
      : (switchTariffsList ?? []);
    return [...list].sort((a, b) => {
      if ((a.sort_order ?? 0) !== (b.sort_order ?? 0)) return (a.sort_order ?? 0) - (b.sort_order ?? 0);
      return a.price_rub - b.price_rub;
    });
  }, [isPreview, switchTariffsList]);
  const switchPaged = useAutoPagedItems(switchSortedTariffs, { rowHeight: t.px(OPTION_ROW_HEIGHT), gap: 6, minRowWidth: OPTION_ROW_MIN_WIDTH });
  const switchCards = useAutoPagedItems(switchSortedTariffs, {
    rowHeight: t.px(OPTION_CARD_HEIGHT),
    gap: 10,
    minRowWidth: OPTION_CARD_MIN_WIDTH,
    maxCols: 5,
  });
  const switchCompact = viewportIsMobile || (switchCountKnown && (switchCards.cols === 1 || switchCards.rowsFit < 1));
  const switchPager = switchCompact ? switchPaged : switchCards;
  const switchPagedRef = switchPaged.ref;
  const switchCardsRef = switchCards.ref;
  const setSwitchGrid = useCallback(
    (node: HTMLElement | null) => {
      switchPagedRef(node);
      switchCardsRef(node);
    },
    [switchPagedRef, switchCardsRef],
  );

  const onSwitchClick = (e: React.MouseEvent) => {
    if (isPreview) return;
    e.preventDefault();
    if (switchTariffMode === "screen" && switchTariffScreenGroup && switchTariffScreenId && sectionScreen) {
      sectionScreen.openScreen(switchTariffScreenGroup, switchTariffScreenId);
      return;
    }
    if (switchTariffMode === "page") {
      navigate(switchTariffHref || "/tariffs");
      return;
    }
    setPanelMode("switch");
  };

  const [switchHoverId, setSwitchHoverId] = useState<number | null>(null);

  const closeSwitch = () => {
    setPanelMode("main");
  };

  const goCheckoutForTariff = (
    newTariffId: number,
    options?: { selectedDeviceLimit?: string; selectedTrafficGb?: string },
  ) => {
    if (isPreview) return;
    const params = new URLSearchParams();
    params.set("tariff_id", String(newTariffId));
    if (activeKey) {
      params.set("subKey", activeKey.client_id);
      params.set("flow", "upgrade");
    } else {
      params.set("flow", "buy");
    }
    const dev = options?.selectedDeviceLimit?.trim();
    const tr = options?.selectedTrafficGb?.trim();
    if (dev) {
      params.set("include_device", "true");
      params.set("selected_device_limit", dev);
    }
    if (tr) {
      params.set("include_traffic", "true");
      params.set("selected_traffic_gb", tr);
    }
    const slug = (checkoutSlug || "checkout").trim();
    navigate(`/${slug}?${params.toString()}`);
  };

  const onPickTariff = (newTariffId: number) => {
    if (isPreview) return;
    const tariffPicked = (switchTariffsList ?? []).find((tr) => tr.id === newTariffId);
    const hasDevOpts = Array.isArray(tariffPicked?.device_options) && (tariffPicked?.device_options?.length ?? 0) > 0;
    const hasTrOpts = Array.isArray(tariffPicked?.traffic_options_gb) && (tariffPicked?.traffic_options_gb?.length ?? 0) > 0;
    const isConfig = Boolean(tariffPicked?.configurable && (hasDevOpts || hasTrOpts));
    if (isConfig && tariffPicked) {
      setPickedTariff(tariffPicked);
      setPickDeviceDraft(hasDevOpts && tariffPicked.device_limit ? String(tariffPicked.device_limit) : "");
      setPickTrafficDraft(hasTrOpts && tariffPicked.traffic_limit ? String(tariffPicked.traffic_limit) : "");
      setPanelMode("pickOptions");
      return;
    }
    goCheckoutForTariff(newTariffId);
  };

  const closePickOptions = () => {
    setPanelMode("switch");
    setPickedTariff(null);
    setPickDeviceDraft("");
    setPickTrafficDraft("");
  };

  const onPickOptionsSubmit = () => {
    if (!pickedTariff || submitBusy) return;
    setSubmitBusy(true);
    try {
      goCheckoutForTariff(pickedTariff.id, {
        selectedDeviceLimit: pickDeviceDraft,
        selectedTrafficGb: pickTrafficDraft,
      });
    } finally {
      setSubmitBusy(false);
    }
  };



  return {
    wrap,
    d,
    t,
    decor,
    cfg,
    isPreview,
    list,
    activeKey,
    details,
    isLoadingKeys,
    hasKeys,
    setSelectedClientId,
    tariff,
    isMobile,
    copied,
    qrOpen,
    setQrOpen,
    qrImg,
    qrLoading,
    qrError,
    editingAlias,
    setEditingAlias,
    aliasDraft,
    setAliasDraft,
    renameBusy,
    renameError,
    connectLink,
    keyTitle,
    startRename,
    saveRename,
    onConnect,
    onCopy,
    onShare,
    onToggleQr,
    headerHint,
    pricePrimary,
    pricePeriodLabel,
    renewalText,
    features,
    resolvedSecondaryLabel,
    renewHref,
    tariffDeviceOptions,
    tariffTrafficOptions,
    hasAnyAddonOption,
    addonsHref,
    showPrimaryButton,
    renewBusy,
    primaryLabelDisplay,
    onRenewClick,
    panelMode,
    screenFlip,
    revealRef,
    panelModeFadeClass,
    pickedTariff,
    pickDeviceDraft,
    setPickDeviceDraft,
    pickTrafficDraft,
    setPickTrafficDraft,
    submitBusy,
    pickPriceText,
    onAddonsClick,
    closeAddons,
    switchTariffsList,
    switchListLoading,
    switchLoadedFade,
    switchSortedTariffs,
    switchPaged,
    switchCards,
    switchCompact,
    switchPager,
    setSwitchGrid,
    onSwitchClick,
    switchHoverId,
    setSwitchHoverId,
    closeSwitch,
    onPickTariff,
    closePickOptions,
    onPickOptionsSubmit,
  };
}
