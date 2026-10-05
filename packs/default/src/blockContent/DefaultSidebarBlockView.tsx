"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import useSWR from "swr";
import { useCabinetTabs } from "@/app/(marketing)/landing/CabinetTabContext";
import { DASHBOARD_TAB_GROUP_ID } from "@/app/(marketing)/landing/dashboardTabSections";
import { useCabinetNavConfigFromDom, parseCabinetNavConfig } from "@/lib/cabinet-nav-config";
import { attentionMatchesTab, useCabinetAttention } from "@/lib/cabinet-attention";
import { AttentionDot } from "@/components/constructor/blockContent/cabinet/AttentionDot";
import { MONO_ICON_PATHS, resolveMonoIcon, iconPackStyle, type MonoIconKey } from "@/lib/cabinet-sidebar-icons";
import { useDefaultTheme, avatarGradientStyle, pickContrast, useIsMobile, panelShadowFlush, NAV_R } from "./defaultTheme";
import { hexToRgba } from "@/components/constructor/utils";
import { resolveEntranceAnimation } from "@/app/(marketing)/landing/blockAnimation/entrance";
import { useBlockApi, apiFetch } from "@/components/constructor/blockContent/cabinetKit/apiHooksRegistry";
import { f, parseBlockData } from "@/components/constructor/blockContent/cabinetKit/blockSchema";
import { pickBool } from "@/components/constructor/blockContent/cabinetKit/dataPickers";
import type { CabinetSidebarTab } from "@/components/constructor/blockData/blocks";
import { avatarInitials } from "@/lib/format-text";
import { AccountAvatar } from "@/components/constructor/blockContent/account/AccountAvatar";

type ConnectionInfo = {
  online: boolean;
  is_frozen: boolean;
  expires_in_days: number;
  server_name: string;
  cluster_name: string;
  panel_type: string;
  protocol: string;
  is_online?: boolean | null;
};

const SIDEBAR_SCHEMA = {
  brandName: f.str(""),
  brandVersion: f.str(""),
  menuLabel: f.str("Меню"),
  connTunnelLabel: f.str(""),
  connTunnelValue: f.str("WG-01"),
  connStatusConnectedLabel: f.str("Подключено"),
  connStatusDisconnectedLabel: f.str("Отключено"),
  connStatusOfflineLabel: f.str("Не в сети"),
  connLocLabel: f.str("AMS-03 · Амстердам · 14 ms"),
  userPlanFormat: f.str("{tariff} · {days} дн"),
  userPlanEmpty: f.str("Нет подписки"),
  logoutLabel: f.str("↗"),
};

const ICON_PATHS = MONO_ICON_PATHS;

const SIDEBAR_ICON_BY_ID: Record<string, MonoIconKey> = {
  profile: "prof",
  keys: "bill",
  instructions: "instructions",
  referrals: "ref",
  partners: "partners",
  gifts: "gift",
  notifications: "bell",
};


export function DefaultSidebarBlockView({ block, context, editMode }: TypedBlockViewProps<"defaultSidebar">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);

  const api = useBlockApi({
    needs: ["summary", "activeSubscription"],
    disabled: previewMode === true,
    mock: previewMode === true,
  });
  const summary = { data: api.summary.data };
  const tabsCtx = useCabinetTabs();

  const cfg = parseBlockData(d, SIDEBAR_SCHEMA);
  const { brandName, brandVersion, menuLabel, connStatusDisconnectedLabel, connStatusOfflineLabel, userPlanFormat, userPlanEmpty, logoutLabel } = cfg;
  const fallbackTunnelValue = cfg.connTunnelValue;
  const fallbackLocLabel = cfg.connLocLabel;
  const connStatusConnectedLabel = typeof d.connStatusConnectedLabel === "string"
    ? d.connStatusConnectedLabel
    : (typeof d.connStatusLabel === "string" ? d.connStatusLabel : cfg.connStatusConnectedLabel);
  const showBrand = brandName.trim() !== "" || brandVersion.trim() !== "";
  const showConn = pickBool(d, "showConnCard", true);
  const showUserFooter = pickBool(d, "showUserFooter", true);
  const tabsGroupRaw = typeof d.tabGroupId === "string" && d.tabGroupId.trim()
    ? d.tabGroupId.trim()
    : (typeof d.tabsGroup === "string" && d.tabsGroup.trim() ? d.tabsGroup.trim() : DASHBOARD_TAB_GROUP_ID);
  const tabsGroup = tabsGroupRaw;

  const itemsField = Array.isArray(d.tabs) ? d.tabs : null;
  const iconStyle = useMemo(() => iconPackStyle(typeof d.iconPack === "string" ? d.iconPack : undefined), [d.iconPack]);
  const themeNavConfig = useCabinetNavConfigFromDom();
  const configItems = useMemo(() => parseCabinetNavConfig(themeNavConfig), [themeNavConfig]);
  const rawTabs: CabinetSidebarTab[] = useMemo(() => {
    if (configItems && configItems.length > 0) {
      return configItems.map((it, i) => ({
        id: it.id,
        num: String(i + 1).padStart(2, "0"),
        label: it.label,
        icon: resolveMonoIcon(it.id, undefined, it.icon, SIDEBAR_ICON_BY_ID),
        badgeKey: it.badgeKey as CabinetSidebarTab["badgeKey"],
      }));
    }
    return itemsField && itemsField.length > 0
      ? (itemsField as CabinetSidebarTab[])
      : [
          { id: "profile", num: "01", label: "Профиль", icon: "prof" },
          { id: "keys", num: "02", label: "Подписки", icon: "bill" },
          { id: "partners", num: "03", label: "Партнёрка", icon: "ref" },
          { id: "gifts", num: "04", label: "Подарки", icon: "gift", badgeKey: "giftsClaimed" },
          { id: "notifications", num: "05", label: "Уведомления", icon: "bell", badgeKey: "unreadNotifications" },
        ];
  }, [configItems, itemsField]);
  const navVisibility = useMemo<Map<string, boolean>>(() => {
    const map = new Map<string, boolean>();
    if (!Array.isArray(themeNavConfig)) return map;
    for (const item of themeNavConfig) {
      if (!item || typeof item !== "object") continue;
      const rec = item as Record<string, unknown>;
      const id = String(rec.id ?? "").trim().toLowerCase();
      if (id) map.set(id, rec.visible !== false);
    }
    return map;
  }, [themeNavConfig]);

  const tabs: CabinetSidebarTab[] = useMemo(
    () =>
      rawTabs.filter((tab) => {
        const id = String(tab.id ?? "").trim().toLowerCase();
        return navVisibility.get(id) !== false;
      }),
    [rawTabs, navVisibility],
  );

  const activeTabId = tabsCtx?.activeTabByGroup[tabsGroup] ?? tabs[0]?.id ?? "";

  useEffect(() => {
    const ctxActive = tabsCtx?.activeTabByGroup[tabsGroup];
    const firstId = tabs[0]?.id;
    if (!previewMode && firstId && ctxActive && !rawTabs.some((tab) => tab.id === ctxActive)) {
      tabsCtx?.setActiveTab(tabsGroup, firstId);
    }
  }, [rawTabs, tabs, tabsCtx, tabsGroup, previewMode]);

  const activeKey = api.activeKey;
  const activeKeyDetails = api.activeDetails;
  const tariffName = activeKeyDetails?.tariff_name || activeKeyDetails?.subgroup_title || "";
  const { data: connInfo } = useSWR<ConnectionInfo>(
    !previewMode && activeKey ? `/api/keys/${encodeURIComponent(activeKey.client_id)}/connection` : null,
    (url: string) => apiFetch<ConnectionInfo>(url),
    { refreshInterval: 60_000 },
  );
  const [nowMs, setNowMs] = useState<number>(() => (typeof window !== "undefined" ? Date.now() : 0));
  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, []);
  const daysLeft = previewMode
    ? 247
    : (typeof connInfo?.expires_in_days === "number" ? connInfo.expires_in_days : (activeKey?.expiry_time && nowMs > 0
        ? Math.max(0, Math.ceil((activeKey.expiry_time - nowMs) / (1000 * 60 * 60 * 24)))
        : 0));
  const planLabel = previewMode
    ? "PRIME · 247 дн"
    : (activeKey && tariffName)
      ? userPlanFormat.replace("{tariff}", tariffName).replace("{days}", String(daysLeft))
      : userPlanEmpty;

  const subActive = previewMode
    ? true
    : connInfo
      ? connInfo.online
      : (activeKey ? !activeKey.is_frozen && (activeKey.expiry_time ?? 0) > nowMs : false);
  const liveOnline = previewMode ? true : (typeof connInfo?.is_online === "boolean" ? connInfo.is_online : null);
  let connStatusLabel: string;
  let connStatusColor: string;
  if (!subActive) {
    connStatusLabel = connStatusDisconnectedLabel;
    connStatusColor = t.accent;
  } else if (liveOnline === false) {
    connStatusLabel = connStatusOfflineLabel;
    connStatusColor = t.inkDim;
  } else {
    connStatusLabel = connStatusConnectedLabel;
    connStatusColor = t.success;
  }
  const connTunnelValue = previewMode
    ? fallbackTunnelValue
    : (connInfo?.protocol || fallbackTunnelValue);
  const connLocLabel = previewMode
    ? fallbackLocLabel
    : (connInfo
        ? [connInfo.cluster_name, connInfo.server_name].filter(Boolean).join(" · ") || fallbackLocLabel
        : fallbackLocLabel);

  const login = summary.data?.email?.split("@")[0] ?? (summary.data?.tg_id ? `tg · ${summary.data.tg_id}` : "Аккаунт");
  const avatar = avatarInitials(summary.data?.email, login);

  const resolveBadge = (tab: CabinetSidebarTab): string | null => {
    if (tab.badge && tab.badge.length > 0) return tab.badge;
    if (!summary.data) return null;
    if (tab.badgeKey === "unreadNotifications" && summary.data.unread_notifications > 0)
      return String(summary.data.unread_notifications);
    if (tab.badgeKey === "giftsClaimed" && summary.data.gifts_claimed > 0)
      return String(summary.data.gifts_claimed);
    return null;
  };

  const onAccent = pickContrast(t.accent);
  const isMobile = useIsMobile();
  const attention = useCabinetAttention();
  const railBg = t.panel;
  const railDecor = usePanelDecor(railBg);
  const railRadius = typeof railDecor.borderRadius === "string"
    ? railDecor.borderRadius
    : `var(--block-visual-radius, ${t.radius.md}px)`;

  const navRef = useRef<HTMLElement | null>(null);
  const asideRef = useRef<HTMLElement | null>(null);
  const [railWidth, setRailWidth] = useState(0);
  const fixedRail = d.edgeRail === true && !isMobile && !editMode && !previewMode;
  useEffect(() => {
    if (!fixedRail) { setRailWidth(0); return; }
    const el = asideRef.current;
    if (!el) return;
    const measure = () => {
      const body = document.body;
      const cell = (el.closest("[data-block-cell]") as HTMLElement | null) ?? el;
      const zoom = body.offsetWidth > 0 ? body.getBoundingClientRect().width / body.offsetWidth : 1;
      const rightVisual = cell.getBoundingClientRect().right;
      setRailWidth(Math.max(0, Math.round(rightVisual / (zoom || 1))));
    };
    measure();
    window.addEventListener("resize", measure);
    if (typeof ResizeObserver !== "undefined") {
      const ro = new ResizeObserver(measure);
      ro.observe(document.body);
      return () => { window.removeEventListener("resize", measure); ro.disconnect(); };
    }
    return () => window.removeEventListener("resize", measure);
  }, [fixedRail]);
  useEffect(() => {
    if (isMobile || previewMode) return;
    const el = asideRef.current;
    if (!el) return;
    const root = document.documentElement;
    const publish = () => {
      const cell = (el.closest("[data-block-cell]") as HTMLElement | null) ?? el;
      const right = Math.round(cell.getBoundingClientRect().right);
      if (right > 0) root.style.setProperty("--cabinet-sidebar-edge", `${right}px`);
    };
    publish();
    window.addEventListener("resize", publish);
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(publish) : null;
    ro?.observe(el);
    ro?.observe(document.body);
    return () => {
      ro?.disconnect();
      window.removeEventListener("resize", publish);
      root.style.removeProperty("--cabinet-sidebar-edge");
    };
  }, [isMobile, previewMode]);

  const entrance = resolveEntranceAnimation(d.animationType);
  const entranceOn = Boolean(entrance) && d.animationTrigger !== "scroll" && !editMode && !previewMode;
  const entranceMs = typeof d.animationDuration === "number" && d.animationDuration > 0 ? d.animationDuration : 420;
  const [entranceDone, setEntranceDone] = useState(false);
  const entranceClass = entranceOn && !entranceDone ? `${entrance!.className} block-animate-in-view` : "";

  const itemRefs = useRef(new Map<string, HTMLButtonElement>());
  const indicatorMountedRef = useRef(false);
  const [indicator, setIndicator] = useState<{ top: number; height: number; animate: boolean } | null>(null);
  useEffect(() => {
    const measure = (animate: boolean) => {
      const el = activeTabId ? itemRefs.current.get(activeTabId) : null;
      if (!el) {
        setIndicator(null);
        return;
      }
      setIndicator({ top: el.offsetTop, height: el.offsetHeight, animate });
    };
    measure(indicatorMountedRef.current);
    const raf = requestAnimationFrame(() => {
      measure(indicatorMountedRef.current);
      indicatorMountedRef.current = true;
    });
    const nav = navRef.current;
    if (!nav || typeof ResizeObserver === "undefined") return () => cancelAnimationFrame(raf);
    const ro = new ResizeObserver(() => measure(false));
    ro.observe(nav);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, [activeTabId, tabs.length]);

  return wrap(
    <aside
      ref={asideRef}
      className={`${ELEMENT_FILL_CLASS} ${entranceClass}`.trim()}
      onAnimationEnd={entranceOn && !entranceDone ? () => setEntranceDone(true) : undefined}
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        fontFamily: t.monoFont,
        color: t.ink,
        overflow: "visible",
        ...(entranceClass ? { ["--block-anim-duration" as string]: `${entranceMs}ms` } : {}),
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 12,
          background: railBg,
          borderRight: `1px solid ${t.line}`,
          padding: "18px 14px 14px",
          boxSizing: "border-box",
          boxShadow: panelShadowFlush(t),
          ...railDecor,
          borderRadius: `0 ${railRadius} ${railRadius} 0`,
          ...(isMobile
            ? { flex: 1, minHeight: 0 }
            : fixedRail && railWidth > 0
              ? {
                  position: "fixed" as const,
                  left: 0,
                  top: "calc(var(--header-height, 84px) * var(--header-zoom, 1) / var(--body-zoom, 1))",
                  bottom: 0,
                  width: `${railWidth}px`,
                  zIndex: 30,
                }
              : {
                  position: "sticky" as const,
                  top: 0,
                  height: "calc((100dvh - var(--header-height, 84px)) / var(--body-zoom, 1))",
                  maxHeight: "100%",
                  flex: "0 0 auto",
                }),
        }}
      >
      {showBrand ? (
        <div style={{ display: "flex", alignItems: "center", gap: t.space.smPlus, padding: "4px 4px 0", fontWeight: t.weight.bold }}>
          <span style={{ width: 28, height: 28, borderRadius: 8, background: t.accent, flexShrink: 0 }} />
          {brandName.trim() !== "" ? <span style={{ fontSize: t.font.lg }}>{brandName}</span> : null}
          {brandVersion.trim() !== "" ? <span style={{ fontSize: t.font.xxs, color: t.inkDim, marginLeft: "auto" }}>{brandVersion}</span> : null}
        </div>
      ) : null}

      {showConn ? (
        <div style={{ background: t.innerBg, borderRadius: t.radius.sm, padding: "12px 14px", display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: t.space.sm, flexWrap: "wrap" }}>
            <span style={{ display: "flex", alignItems: "center", gap: t.space.sm, fontSize: t.font.sm, color: connStatusColor, fontWeight: t.weight.bold, whiteSpace: "nowrap" }}>
              <span style={{ width: 8, height: 8, borderRadius: "50%", background: connStatusColor }} />
              {connStatusLabel}
            </span>
            {connTunnelValue ? (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                {cfg.connTunnelLabel ? (
                  <span style={{ fontSize: t.font.xxs, color: t.inkDim }}>{cfg.connTunnelLabel}</span>
                ) : null}
                <span style={{ fontSize: t.font.xxs, fontWeight: t.weight.bold, color: t.inkDim, background: t.panel, borderRadius: 999, padding: "3px 8px" }}>{connTunnelValue}</span>
              </span>
            ) : null}
          </div>
          <div style={{ fontSize: t.font.xs, color: t.inkDim }}>{connLocLabel}</div>
        </div>
      ) : null}

      {menuLabel ? (
        <div style={{ fontSize: t.font.xxs, color: t.inkMute, fontWeight: t.weight.bold, letterSpacing: "0.08em", padding: "6px 6px 0", textTransform: "uppercase" }}>{menuLabel}</div>
      ) : null}

      <style>{`
        .dsb-item { transition: color 180ms ease, background 160ms ease; }
        .dsb-item:not([data-active="true"]):hover { background: var(--dsb-hover); color: var(--dsb-ink); }
        .dsb-ind { transition: top 300ms cubic-bezier(0.3, 1.1, 0.4, 1), height 200ms ease; }
      `}</style>
      <nav ref={navRef} style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6, minHeight: 0, position: "relative", ["--dsb-hover" as never]: t.innerBg, ["--dsb-ink" as never]: t.ink }}>
        {indicator ? (
          <span
            aria-hidden
            className="dsb-ind"
            style={{
              position: "absolute",
              top: indicator.top,
              height: indicator.height,
              left: 0,
              right: 0,
              borderRadius: NAV_R,
              background: t.accent,
              boxShadow: `0 10px 24px -10px ${hexToRgba(t.accent, 0.55)}`,
              zIndex: 0,
              pointerEvents: "none",
              ...(indicator.animate ? {} : { transition: "none" }),
            }}
          />
        ) : null}
        {tabs.map((tab, tabIdx) => {
          const isActive = tab.id === activeTabId;
          const badge = resolveBadge(tab);
          const needsAttention = attentionMatchesTab(attention, tab.id);
          return (
            <button
              key={tab.id ?? `tab-${tabIdx}`}
              ref={(el) => {
                if (el && tab.id) itemRefs.current.set(tab.id, el);
                else if (tab.id) itemRefs.current.delete(tab.id);
              }}
              className="dsb-item"
              data-active={isActive ? "true" : "false"}
              onClick={() => tab.id && tabsCtx?.setActiveTab(tabsGroup, tab.id)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: t.space.smPlus,
                padding: "12px 14px",
                border: 0,
                borderRadius: NAV_R,
                background: "transparent",
                color: isActive ? onAccent : t.inkDim,
                fontFamily: t.monoFont,
                textAlign: "left",
                fontSize: t.font.smPlus,
                fontWeight: isActive ? t.weight.bold : t.weight.medium,
                cursor: "pointer",
                position: "relative",
                zIndex: 1,
              }}
            >
              {tab.icon && ICON_PATHS[tab.icon] ? (
                <svg viewBox="0 0 16 16" style={{ width: 16, height: 16, flexShrink: 0, ...iconStyle }}>
                  {ICON_PATHS[tab.icon]}
                </svg>
              ) : null}
              <span style={{ flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{tab.label ?? ""}</span>
              {badge ? (
                <span style={{ fontSize: t.font.xxs, fontWeight: t.weight.bold, background: isActive ? hexToRgba(onAccent, 0.25) : t.accent, color: onAccent, minWidth: 18, height: 18, borderRadius: 999, display: "inline-flex", alignItems: "center", justifyContent: "center", padding: "0 5px" }}>{badge}</span>
              ) : null}
              {needsAttention ? (
                <AttentionDot color={attention.color || t.accent} onBackground={isActive ? t.accent : t.innerBg} />
              ) : null}
            </button>
          );
        })}
      </nav>

      {showUserFooter ? (
        <div style={{ borderTop: `1px solid ${t.line}`, paddingTop: 12, display: "flex", alignItems: "center", gap: t.space.smPlus }}>
          <AccountAvatar enabled={!previewMode} fallback={avatar} style={{ ...avatarGradientStyle(t, 36), fontSize: t.font.sm }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: t.weight.bold, fontSize: t.font.sm, color: t.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{login}</div>
            <div style={{ fontSize: t.font.xs, color: t.inkDim }}>{planLabel}</div>
          </div>
          <Link
            href="/logout"
            title="Выйти"
            style={{
              width: 30,
              height: 30,
              borderRadius: t.radius.sm,
              background: t.innerBg,
              color: t.inkDim,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: t.font.smPlus,
              textDecoration: "none",
              flexShrink: 0,
            }}
          >
            {logoutLabel}
          </Link>
        </div>
      ) : null}
      </div>
    </aside>,
    false,
    true,
  );
}
