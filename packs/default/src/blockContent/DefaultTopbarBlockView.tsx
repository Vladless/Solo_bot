"use client";

import { hrefToCabinetTab } from "@/components/constructor/blockContent/cabinetKit/blockHelpers";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { useCabinetTabs } from "@/app/(marketing)/landing/CabinetTabContext";
import { useSectionScreen } from "@/app/(marketing)/landing/SectionScreenContext";
import { useCabinetTarget, type CabinetTarget } from "@/app/(marketing)/landing/useCabinetTarget";
import { DASHBOARD_TAB_GROUP_ID } from "@/app/(marketing)/landing/dashboardTabSections";
import { useDefaultTheme, btnSecondary, btnSolid, panelShadow, panelShadowSm, buttonShadow, pickContrast, useIsMobile, CONTROL_H, CONTROL_R } from "./defaultTheme";
import { hexToRgba } from "@/components/constructor/utils";
import { f, parseBlockData } from "@/components/constructor/blockContent/cabinetKit/blockSchema";
import { STACK_WIDTH, TOPBAR_SEARCH_HIDE_WIDTH, TOPBAR_SEARCH_MIN_WIDTH, topbarAutoScale, useContainerSize } from "./layout";
import { pickBool } from "@/components/constructor/blockContent/cabinetKit/dataPickers";
import type { CabinetTopbarAction } from "@/components/constructor/blockData/blocks";
import { useBlockApi } from "@/components/constructor/blockContent/cabinetKit/apiHooksRegistry";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import { useAppInfo } from "@/app/AppInfoProvider";
import { activateTrial as requestTrial } from "@/lib/trial-activation";
import { hasAuth } from "@/lib/auth";
import { slugToPath } from "@/lib/web-page-registry";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { useBlockNavigate } from "@/lib/block-navigation";

type SearchHit = { kind: string; label: string; sublabel: string; href: string; meta: string };

type SupportLink = { label?: string; href?: string };

const TOPBAR_SCHEMA = {
  searchSectionHint: f.str("Раздел кабинета"),
  trialErrorText: f.str("Не удалось активировать пробную подписку"),
  greetingFormat: f.str("С возвращением, {name} 👋"),
  searchPlaceholder: f.str("Поиск по кабинету…"),
  supportConfirmTitle: f.str("Возникли проблемы?"),
  supportConfirmText: f.str("Если что-то не работает или есть вопрос — поддержка на связи и быстро поможет."),
  supportConfirmYes: f.str("Обратиться в поддержку"),
  supportConfirmNo: f.str("Нет, всё в порядке"),
  supportLinks: f.array<SupportLink>([]),
};

/**
 * Куда ведёт кнопка шапки внутри кабинета. Экран раздела и якорь блока задаёт админ; у старых
 * кнопок на «Подписки» экран не задан — им сохраняем прежнее поведение с экраном смены тарифа.
 */
function actionTarget(act: CabinetTopbarAction): CabinetTarget {
  const screenGroup = (act.cabinetScreenGroup ?? "").trim();
  const screenId = (act.cabinetScreenId ?? "").trim();
  const legacyKeysSwitch = !screenId && (act.cabinetTabId ?? "").trim() === "keys";
  return {
    tabGroup: act.cabinetTabGroup,
    tabId: act.cabinetTabId,
    screenGroup: legacyKeysSwitch ? "tariffPanel" : screenGroup,
    screenId: legacyKeysSwitch ? "switch" : screenId,
    anchor: act.anchor,
  };
}

const DEFAULT_TOPBAR_ACTIONS: CabinetTopbarAction[] = [
  { label: "Помощь", variant: "ghost", href: "/dashboard/instructions" },
  { label: "Купить трафик", variant: "red", href: "/tariffs" },
];

const SECTION_SEARCH: { id: string; label: string; keywords: string }[] = [
  { id: "profile", label: "Профиль", keywords: "профиль аккаунт почта email телефон язык данные безопасность" },
  { id: "keys", label: "Подписки", keywords: "подписки ключи ключ тариф трафик подключение vpn оплата продление устройства" },
  { id: "partners", label: "Партнёрка", keywords: "партнёрка партнерка рефералы реферал друзья вывод бонус заработок ссылка" },
  { id: "gifts", label: "Подарки", keywords: "подарки подарок промокод купон gift активировать" },
  { id: "notifications", label: "Уведомления", keywords: "уведомления оповещения notifications колокол" },
];

const KIND_LABEL: Record<string, string> = {
  action: "Действие",
  section: "Раздел",
  subscription: "Подписка",
  payment: "Платёж",
  notification: "Уведомление",
};

type QuickAction = {
  label: string;
  sublabel: string;
  keywords: string;
  tabId?: string;
  screenGroup?: string;
  screenId?: string;
  href?: string;
  flowId?: string;
};

const ACTION_SEARCH: QuickAction[] = [
  { label: "Продлить подписку", sublabel: "Тариф и кнопка продления", keywords: "продлить продление оплатить подписку renew истекает срок", tabId: "keys" },
  { label: "Купить подписку", sublabel: "Выбор и покупка тарифа", keywords: "купить покупка тариф подключить оформить buy новая подписка", tabId: "keys", screenGroup: "tariffPanel", screenId: "switch" },
  { label: "Сменить тариф", sublabel: "Каталог тарифов", keywords: "сменить тариф смена апгрейд другой тариф upgrade", tabId: "keys", screenGroup: "tariffPanel", screenId: "switch" },
  { label: "Докупить опции", sublabel: "Трафик и устройства", keywords: "докупить трафик устройства гб опции расширить", tabId: "keys", screenGroup: "tariffPanel", screenId: "addons" },
  { label: "Мои устройства", sublabel: "Подключённые устройства", keywords: "устройства девайсы подключения device отвязать", tabId: "keys" },
  { label: "Активировать промокод", sublabel: "Подарки и купоны", keywords: "промокод купон активировать код подарок gift", tabId: "gifts" },
  { label: "Отправить подарок", sublabel: "Подарить подписку", keywords: "подарок подарить отправить другу gift", tabId: "gifts" },
  { label: "Пригласить друга", sublabel: "Партнёрская ссылка", keywords: "пригласить друг ссылка реферал партнёрка партнерка заработать", tabId: "partners" },
  { label: "Вывести средства", sublabel: "Заявка на вывод", keywords: "вывести вывод средства деньги баланс заявка выплата", tabId: "partners" },
  { label: "Транзакции", sublabel: "Чеки и оплаты", keywords: "история платежей оплаты чеки платеж pdf счёт", tabId: "profile" },
  { label: "Настройки аккаунта", sublabel: "Профиль и безопасность", keywords: "настройки аккаунт безопасность пароль язык email почта", tabId: "profile", screenGroup: "profile", screenId: "settings" },
  { label: "Выйти из аккаунта", sublabel: "Завершить сессию", keywords: "выйти выход logout завершить сессию", href: "/logout" },
];


export function DefaultTopbarBlockView({ block, context }: TypedBlockViewProps<"defaultTopbar">) {
  const navigate = useBlockNavigate();
  const decor = usePanelDecor();
  const { ref: barRef, width: barWidth, height: barHeight } = useContainerSize();
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const ownFontScale = typeof d.fontScale === "number" && d.fontScale > 0 ? d.fontScale : 1;
  const autoScale = topbarAutoScale(barWidth, barHeight, ownFontScale);
  const scaledData = useMemo(
    () => ({ ...d, fontScale: ownFontScale * autoScale }),
    [d, ownFontScale, autoScale],
  );
  const t = useDefaultTheme(scaledData);

  const tabsCtx = useCabinetTabs();
  const sectionScreen = useSectionScreen();
  const goToTarget = useCabinetTarget();
  const isMobile = useIsMobile();
  const api = useBlockApi({ needs: ["summary"], disabled: previewMode === true, mock: previewMode === true });
  const mut = useAccountMutations();
  const appInfo = useAppInfo();
  const keysTotal = typeof api.summary.data?.keys_total === "number" ? api.summary.data.keys_total : 0;
  const trialStatus = typeof api.summary.data?.trial_status === "number" ? api.summary.data.trial_status : 0;
  const trialAvailable = !!appInfo.features.trialEnabled && (trialStatus === 0 || trialStatus === -1);
  const [trialBusy, setTrialBusy] = useState(false);
  const activateTrial = async () => {
    if (previewMode || trialBusy) return;
    if (!hasAuth()) {
      navigate(`${slugToPath("login")}?from=${encodeURIComponent(slugToPath("dashboard"))}`);
      return;
    }
    setTrialBusy(true);
    const result = await requestTrial();
    if (result.ok) {
      navigate(slugToPath("dashboard-keys"));
      return;
    }
    alert(result.error || trialErrorText);
    setTrialBusy(false);
  };
  const { greetingFormat, searchPlaceholder, supportConfirmTitle, supportConfirmText, supportConfirmYes, supportConfirmNo, supportLinks, trialErrorText, searchSectionHint } = parseBlockData(d, TOPBAR_SCHEMA);
  const extraSupportLinks = supportLinks
    .map((item) => ({
      label: typeof item?.label === "string" ? item.label.trim() : "",
      href: typeof item?.href === "string" ? item.href.trim() : "",
    }))
    .filter((item) => item.label !== "" && item.href !== "");
  const [supportConfirm, setSupportConfirm] = useState<{ href: string; external: boolean } | null>(null);
  const [supportShown, setSupportShown] = useState(false);
  const supportCloseTimer = useRef<number | null>(null);
  const closeSupport = () => {
    setSupportShown(false);
    if (supportCloseTimer.current != null) window.clearTimeout(supportCloseTimer.current);
    supportCloseTimer.current = window.setTimeout(() => setSupportConfirm(null), 260);
  };
  useEffect(() => {
    if (!supportConfirm) return;
    if (supportCloseTimer.current != null) {
      window.clearTimeout(supportCloseTimer.current);
      supportCloseTimer.current = null;
    }
    let r2 = 0;
    const r1 = requestAnimationFrame(() => {
      r2 = requestAnimationFrame(() => setSupportShown(true));
    });
    return () => {
      cancelAnimationFrame(r1);
      cancelAnimationFrame(r2);
    };
  }, [supportConfirm]);
  useEffect(() => () => {
    if (supportCloseTimer.current != null) window.clearTimeout(supportCloseTimer.current);
  }, []);
  useEffect(() => {
    if (!supportConfirm) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closeSupport(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [supportConfirm]);
  const showSearchSetting = pickBool(d, "showSearch", true);
  const narrowBar = barWidth > 0 && barWidth < TOPBAR_SEARCH_MIN_WIDTH;
  const searchOwnRow = barWidth > 0 && barWidth < STACK_WIDTH;
  const showSearch = showSearchSetting && (barWidth === 0 || barWidth >= TOPBAR_SEARCH_HIDE_WIDTH);
  const showBell = pickBool(d, "showBell", true);
  const showGear = pickBool(d, "showGear", true);
  const SETTINGS_GROUP = "profile";
  const onProfileTab = (tabsCtx?.activeTabByGroup?.[DASHBOARD_TAB_GROUP_ID] ?? "profile") === "profile";
  const settingsActive = onProfileTab && sectionScreen?.activeScreenByGroup?.[SETTINGS_GROUP] === "settings";
  const notifActive = (tabsCtx?.activeTabByGroup?.[DASHBOARD_TAB_GROUP_ID] ?? "profile") === "notifications";
  const toggleSettings = () => {
    if (settingsActive) {
      sectionScreen?.openScreen(SETTINGS_GROUP, "main");
    } else {
      tabsCtx?.setActiveTab(DASHBOARD_TAB_GROUP_ID, "profile");
      sectionScreen?.openScreen(SETTINGS_GROUP, "settings");
    }
  };
  const unread = previewMode ? 1 : (typeof api.summary.data?.unread_notifications === "number" ? api.summary.data.unread_notifications : 0);
  const greetingName = previewMode
    ? "Кирилл"
    : (api.summary.data?.email?.split("@")[0] ?? (api.summary.data?.tg_id ? String(api.summary.data.tg_id) : ""));
  const greetingParts = greetingFormat.split("{name}");
  const showGreeting = greetingFormat.trim() !== "";

  const [searchQuery, setSearchQuery] = useState("");
  const [searchHits, setSearchHits] = useState<SearchHit[] | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchTimer = useRef<number | null>(null);

  const trimmedQuery = searchQuery.trim();
  const shouldQuery = !previewMode && showSearch && trimmedQuery.length >= 2;

  useEffect(() => {
    if (!shouldQuery) return;
    if (searchTimer.current) window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => {
      mut.searchAccountContent(trimmedQuery)
        .then((res) => setSearchHits((res.hits ?? []) as unknown as SearchHit[]))
        .catch(() => setSearchHits([]));
    }, 250);
    return () => {
      if (searchTimer.current) window.clearTimeout(searchTimer.current);
    };
  }, [trimmedQuery, shouldQuery]);

  const visibleHits = shouldQuery ? searchHits : null;

  type CombinedHit = { kind: string; label: string; sublabel: string; href: string; tabId: string | null; screenGroup?: string; screenId?: string; flowId?: string };

  const customActions = Array.isArray(d.searchActions) ? (d.searchActions as QuickAction[]).filter((a) => a && a.label) : [];
  const effectiveActions = customActions.length > 0 ? customActions : ACTION_SEARCH;
  const actionHits = useMemo(() => {
    if (!shouldQuery) return [] as CombinedHit[];
    const ql = trimmedQuery.toLowerCase();
    return effectiveActions
      .filter((a) => a.label.toLowerCase().includes(ql) || String(a.keywords ?? "").includes(ql))
      .slice(0, 5)
      .map((a) => ({ kind: "action", label: a.label, sublabel: a.sublabel ?? "", href: a.href ?? "", tabId: a.tabId ?? null, screenGroup: a.screenGroup, screenId: a.screenId, flowId: a.flowId }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shouldQuery, trimmedQuery, JSON.stringify(customActions)]);

  const sectionHits = useMemo(() => {
    if (!shouldQuery) return [] as CombinedHit[];
    const ql = trimmedQuery.toLowerCase();
    return SECTION_SEARCH
      .filter((s) => s.label.toLowerCase().includes(ql) || s.keywords.includes(ql))
      .map((s) => ({ kind: "section", label: s.label, sublabel: searchSectionHint, href: "", tabId: s.id }));
  }, [shouldQuery, trimmedQuery]);

  const combinedHits: CombinedHit[] = [
    ...actionHits,
    ...sectionHits,
    ...(visibleHits ?? []).map((h) => ({ kind: h.kind, label: h.label, sublabel: h.sublabel, href: h.href, tabId: hrefToCabinetTab(h.href) })),
  ];
  const searchLoaded = visibleHits !== null;
  const showSearchDropdown = searchOpen && shouldQuery && (combinedHits.length > 0 || searchLoaded);

  const handleHitClick = (e: React.MouseEvent, hit: { tabId: string | null; screenGroup?: string; screenId?: string; href?: string; flowId?: string }) => {
    if (hit.flowId && hit.flowId.trim()) {
      e.preventDefault();
      const flowId = hit.flowId.trim();
      void import("@/lib/start-flow").then(({ startFlowById }) => startFlowById(flowId));
      setSearchOpen(false);
      setSearchQuery("");
      return;
    }
    if (hit.tabId && tabsCtx) {
      e.preventDefault();
      tabsCtx.setActiveTab(DASHBOARD_TAB_GROUP_ID, hit.tabId);
      if (hit.screenGroup && hit.screenId && sectionScreen) {
        sectionScreen.openScreen(hit.screenGroup, hit.screenId);
      }
      setSearchOpen(false);
      setSearchQuery("");
    }
  };

  const actions: CabinetTopbarAction[] = Array.isArray(d.actions)
    ? (d.actions as CabinetTopbarAction[])
    : DEFAULT_TOPBAR_ACTIONS;


  return wrap(
    <div
      ref={barRef}
      className={ELEMENT_FILL_CLASS}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: showGreeting ? "space-between" : "flex-end",
        padding: "6px 4px",
        fontFamily: t.monoFont,
        color: t.ink,
        gap: t.space.lg,
        rowGap: t.space.sm,
        flexWrap: narrowBar ? "wrap" : "nowrap",
        minWidth: 0,
        maxWidth: "100%",
        boxSizing: "border-box",
        ...decor,
      }}
    >
      {showGreeting ? (
        <div
          style={{
            fontSize: t.font.md,
            color: t.inkDim,
            flexShrink: 1,
            minWidth: 0,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {greetingParts[0]}
          {greetingName ? <b style={{ color: t.ink, fontWeight: t.weight.bold }}>{greetingName}</b> : null}
          {greetingParts[1] ?? ""}
        </div>
      ) : null}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: t.space.smPlus,
          rowGap: t.space.sm,
          flexWrap: narrowBar ? "wrap" : "nowrap",
          justifyContent: "flex-end",
          minWidth: 0,
          flex: "1 1 auto",
          overflow: "visible",
        }}
      >
        {showSearch ? (
          <div style={{ position: "relative", flex: searchOwnRow ? "1 1 100%" : "1 1 130px", minWidth: 0, maxWidth: "100%", order: narrowBar ? -1 : 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: t.space.sm, padding: `0 ${t.px(16)}px`, minHeight: t.px(CONTROL_H), borderRadius: CONTROL_R, background: t.panel, boxShadow: buttonShadow(t), fontSize: t.font.sm, color: t.inkDim }}>
              <span style={{ opacity: 0.7 }}>⌕</span>
              <input
                placeholder={searchPlaceholder}
                value={searchQuery}
                onChange={(e) => { setSearchQuery(e.target.value); setSearchOpen(true); }}
                onFocus={() => setSearchOpen(true)}
                onBlur={() => window.setTimeout(() => setSearchOpen(false), 150)}
                style={{
                  background: "transparent",
                  border: 0,
                  color: t.ink,
                  fontFamily: t.monoFont,
                  outline: "none",
                  flex: 1,
                  minWidth: 0,
                  fontSize: t.font.sm,
                }}
              />
            </div>
            {showSearchDropdown ? (
              <div style={{ position: "absolute", top: "100%", left: 0, right: 0, marginTop: 6, background: t.panel, borderRadius: t.radius.sm, boxShadow: panelShadowSm(t), fontFamily: t.monoFont, maxHeight: 360, overflowY: "auto", zIndex: 50, overflowX: "hidden" }}>
                {combinedHits.length === 0 ? (
                  <div style={{ padding: "12px 14px", fontSize: t.font.xsPlus, color: t.inkDim }}>Ничего не найдено</div>
                ) : (
                  combinedHits.map((h, i) => (
                    <a
                      key={`${h.kind}-${h.tabId ?? h.href}-${i}`}
                      href={h.href || "#"}
                      onClick={(e) => handleHitClick(e, h)}
                      style={{ display: "block", padding: "12px 14px", borderBottom: i < combinedHits.length - 1 ? `1px solid ${t.line}` : "none", textDecoration: "none", color: t.ink, cursor: "pointer" }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: t.space.sm }}>
                        <span style={{ fontSize: t.font.sm, fontWeight: t.weight.bold }}>{h.label}</span>
                        <span style={{ fontSize: t.font.xxs, color: t.accent }}>{KIND_LABEL[h.kind] ?? h.kind}</span>
                      </div>
                      {h.sublabel ? <div style={{ fontSize: t.font.xs, color: t.inkDim, marginTop: 3 }}>{h.sublabel}</div> : null}
                    </a>
                  ))
                )}
              </div>
            ) : null}
          </div>
        ) : null}
        {showBell ? (
          <button
            type="button"
            title="Уведомления"
            aria-label="Уведомления"
            onClick={() => tabsCtx?.setActiveTab(DASHBOARD_TAB_GROUP_ID, "notifications")}
            style={{ position: "relative", width: t.px(CONTROL_H), height: t.px(CONTROL_H), flexShrink: 0, borderRadius: CONTROL_R, background: notifActive ? t.accent : t.panel, boxShadow: buttonShadow(t), border: "none", color: notifActive ? pickContrast(t.accent) : t.ink, cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center", transition: "background 160ms ease, color 160ms ease" }}
          >
            <svg viewBox="0 0 20 20" width={18} height={18} fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 8a5 5 0 0 1 10 0v4l1.5 2h-13L5 12z" />
              <path d="M8 16.5a2 2 0 0 0 4 0" />
            </svg>
            {unread > 0 ? <span style={{ position: "absolute", top: 9, right: 10, width: 8, height: 8, borderRadius: 999, background: notifActive ? pickContrast(t.accent) : t.accent, boxShadow: `0 0 0 2px ${notifActive ? t.accent : t.panel}` }} /> : null}
          </button>
        ) : null}
        {showGear ? (
          <button
            type="button"
            title="Настройки"
            aria-label="Настройки"
            onClick={toggleSettings}
            style={{ width: t.px(CONTROL_H), height: t.px(CONTROL_H), flexShrink: 0, borderRadius: CONTROL_R, background: settingsActive ? t.accent : t.panel, boxShadow: buttonShadow(t), border: "none", color: settingsActive ? pickContrast(t.accent) : t.ink, cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center", transition: "background 160ms ease, color 160ms ease" }}
          >
            <svg viewBox="0 0 24 24" width={18} height={18} fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.09A1.65 1.65 0 0 0 10 4.6V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.09a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
        ) : null}
        {actions.map((act, i) => {
          const visibility = act.visibility ?? "always";
          if (!previewMode) {
            if (visibility === "hidden") return null;
            if (visibility === "if_no_keys" && keysTotal > 0) return null;
          }
          const isSupportIcon = act.variant === "supportIcon";
          const style = isSupportIcon
            ? {
                width: t.px(CONTROL_H),
                height: t.px(CONTROL_H),
                flexShrink: 0,
                borderRadius: CONTROL_R,
                background: t.panel,
                boxShadow: buttonShadow(t),
                color: t.ink,
                display: "inline-flex" as const,
                alignItems: "center" as const,
                justifyContent: "center" as const,
              }
            : {
                ...(act.variant === "red" ? btnSolid(t) : btnSecondary(t)),
                borderRadius: CONTROL_R,
                whiteSpace: "nowrap" as const,
                flexShrink: 0,
                ...(isMobile ? { width: "100%", flexBasis: "100%", justifyContent: "center" as const } : {}),
              };
          const content = isSupportIcon ? (
            <svg viewBox="0 0 24 24" width={19} height={19} fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M4 13a8 8 0 0 1 16 0" />
              <rect x="3" y="13" width="4" height="6" rx="2" />
              <rect x="17" y="13" width="4" height="6" rx="2" />
              <path d="M19 19a4 4 0 0 1-4 2.5h-1.5" />
            </svg>
          ) : (act.label ?? "");
          const iconA11y = isSupportIcon ? { title: act.label || "Помощь", "aria-label": act.label || "Помощь" } : {};
          const linkType = act.linkType ?? "url";
          if (act.trialMode && (previewMode || trialAvailable)) {
            const trialText = (act.trialLabel && act.trialLabel.trim()) || "Попробовать";
            return (
              <button
                key={i}
                type="button"
                {...iconA11y}
                onClick={() => { void activateTrial(); }}
                disabled={trialBusy}
                style={{ ...style, textDecoration: "none", border: 0, cursor: trialBusy ? "default" : "pointer", fontFamily: t.monoFont, opacity: trialBusy ? 0.6 : 1 }}
              >
                {trialText}
              </button>
            );
          }
          const target = actionTarget(act);
          if (linkType === "tab" && (target.tabId || target.screenId || target.anchor)) {
            return (
              <button
                key={i}
                type="button"
                {...iconA11y}
                onClick={() => goToTarget(target)}
                style={{ ...style, textDecoration: "none", border: 0, cursor: "pointer", fontFamily: t.monoFont }}
              >
                {content}
              </button>
            );
          }
          let href = "#";
          if (linkType === "telegram" && act.telegramUsername) {
            href = `https://telegram.me/${act.telegramUsername.replace(/^@/, "")}`;
          } else if (linkType === "url" || !linkType) {
            href = act.href ?? "#";
          }
          const isExternal = href.startsWith("http") || href.startsWith("tg://");
          if (isSupportIcon) {
            return (
              <button
                key={i}
                type="button"
                {...iconA11y}
                onClick={() => setSupportConfirm({ href, external: isExternal })}
                style={{ ...style, textDecoration: "none", border: 0, cursor: "pointer", fontFamily: t.monoFont }}
              >
                {content}
              </button>
            );
          }
          return (
            <a
              key={i}
              href={href}
              {...iconA11y}
              {...(isExternal ? { target: "_blank", rel: "noopener noreferrer" } : {})}
              style={{ ...style, textDecoration: "none" }}
            >
              {content}
            </a>
          );
        })}
      </div>
      {supportConfirm && typeof document !== "undefined" ? createPortal(
        <div
          onClick={closeSupport}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 100000,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 20,
            background: "rgba(8,10,16,0.62)",
            backdropFilter: "blur(6px)",
            WebkitBackdropFilter: "blur(6px)",
            opacity: supportShown ? 1 : 0,
            transition: "opacity 200ms ease",
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: "100%",
              maxWidth: 340,
              background: t.panel,
              color: t.ink,
              borderRadius: t.radius.md,
              boxShadow: panelShadow(t),
              fontFamily: t.monoFont,
              padding: "24px 22px 20px",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 14,
              opacity: supportShown ? 1 : 0,
              transform: supportShown ? "scale(1) translateY(0)" : "scale(0.92) translateY(8px)",
              transition: "opacity 220ms ease, transform 240ms cubic-bezier(0.2,0.8,0.2,1)",
            }}
          >
            <span
              style={{
                width: 52,
                height: 52,
                borderRadius: 18,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: hexToRgba(t.accent, 0.12),
                color: t.accent,
              }}
            >
              <svg viewBox="0 0 24 24" width={26} height={26} fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M4 13a8 8 0 0 1 16 0" />
                <rect x="3" y="13" width="4" height="6" rx="2" />
                <rect x="17" y="13" width="4" height="6" rx="2" />
                <path d="M19 19a4 4 0 0 1-4 2.5h-1.5" />
              </svg>
            </span>
            <div style={{ fontSize: t.font.lg, fontWeight: t.weight.bold, color: t.ink, textAlign: "center" }}>{supportConfirmTitle}</div>
            <p style={{ margin: 0, color: t.inkDim, fontSize: t.font.sm, lineHeight: 1.55, textAlign: "center" }}>{supportConfirmText}</p>
            <a
              href={supportConfirm.href}
              {...(supportConfirm.external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
              onClick={closeSupport}
              style={{ ...btnSolid(t), alignSelf: "stretch", textDecoration: "none", cursor: "pointer" }}
            >
              {supportConfirmYes}
            </a>
            {extraSupportLinks.map((link, idx) => {
              const external = /^https?:\/\//i.test(link.href) || link.href.startsWith("tg://");
              return (
                <a
                  key={`${link.href}-${idx}`}
                  href={link.href}
                  {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                  onClick={closeSupport}
                  style={{ ...btnSecondary(t), alignSelf: "stretch", textDecoration: "none", cursor: "pointer", fontFamily: t.monoFont }}
                >
                  {link.label}
                </a>
              );
            })}
            <button
              type="button"
              onClick={closeSupport}
              style={{ ...btnSecondary(t), alignSelf: "stretch", cursor: "pointer", fontFamily: t.monoFont }}
            >
              {supportConfirmNo}
            </button>
          </div>
        </div>,
        document.body,
      ) : null}
    </div>,
    false,
    true,
  );
}
