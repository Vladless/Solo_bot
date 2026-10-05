"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { useSectionScreen } from "@/app/(marketing)/landing/SectionScreenContext";
import { useCabinetTabs } from "@/app/(marketing)/landing/CabinetTabContext";
import { DASHBOARD_TAB_GROUP_ID } from "@/app/(marketing)/landing/dashboardTabSections";
import { useAppInfo } from "@/app/AppInfoProvider";
import { featureFlagsFromAppInfo } from "@/lib/feature-flags";
import { DEFAULT_CABINET_NAV } from "@/lib/cabinet-navigation";
import { NavIcon } from "@/lib/nav-icons";
import { useDefaultTheme, buttonShadow, pickContrast, f, parseBlockData, NAV_R } from ".";
import { SEGMENT_SLIDE_TRANSITION } from "./SlidingSegmented";
import { isTruthyApiFlag } from "@/lib/cabinet-feature-flags";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";

type TabItem = {
  screenId?: string;
  label?: string;
  labelEn?: string;
  iconName?: string;
  iconUrl?: string;
  backendVisibilityKey?: string;
  respectBackendVisibility?: boolean;
};

const SCHEMA = {
  defaultScreenId: f.str("main"),
  tabsGroup: f.str(""),
  displayMode: f.str("text"),
  orientation: f.str("horizontal"),
};


export function DefaultSectionTabsBlockView({ block, editMode, context }: TypedBlockViewProps<"defaultSectionTabs">) {
  const decor = usePanelDecor();
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, SCHEMA);
  const adminControl = editMode || Boolean(previewMode);

  const sectionScreen = useSectionScreen();
  const cabinetTabsCtx = useCabinetTabs();
  const tabsGroup = String(cfg.tabsGroup ?? "").trim();
  const displayMode = (["text", "icon", "icon-text"].includes(cfg.displayMode) ? cfg.displayMode : "text") as "text" | "icon" | "icon-text";
  const vertical = cfg.orientation === "vertical";
  const appInfo = useAppInfo();
  const features = useMemo(() => featureFlagsFromAppInfo(appInfo), [appInfo]);
  const isCabinetGroup = tabsGroup === DASHBOARD_TAB_GROUP_ID;

  const discoveredScreens = tabsGroup ? sectionScreen?.screensByGroup[tabsGroup] : undefined;

  const rawTabs: TabItem[] = useMemo(() => {
    const fromData = Array.isArray(d.tabs) ? (d.tabs as Array<Record<string, unknown>>) : null;
    const hasValidFormat = !!fromData && fromData.length > 0
      && fromData.every((it) => typeof it?.screenId === "string" && (it.screenId as string).trim() !== "");
    if (hasValidFormat) return fromData as unknown as TabItem[];
    if (isCabinetGroup) {
      return DEFAULT_CABINET_NAV.map((nav) => ({
        screenId: nav.id,
        label: nav.label,
        labelEn: nav.labelEn,
        iconName: nav.icon,
        backendVisibilityKey: nav.backendVisibilityKey,
        respectBackendVisibility: true,
      }));
    }
    if (Array.isArray(discoveredScreens) && discoveredScreens.length > 0) {
      return discoveredScreens.map((screenId) => ({ screenId, label: screenId.toUpperCase() }));
    }
    return [];
  }, [d.tabs, isCabinetGroup, discoveredScreens]);

  const tabs = useMemo<TabItem[]>(() => {
    const seen = new Set<string>();
    return rawTabs.filter((tab) => {
      const id = String(tab.screenId ?? "").trim();
      if (!id || seen.has(id)) return false;
      seen.add(id);
      if (previewMode) return true;
      if (tab.respectBackendVisibility !== false && tab.backendVisibilityKey) {
        return isTruthyApiFlag(features, tab.backendVisibilityKey);
      }
      return true;
    });
  }, [rawTabs, features, previewMode]);

  const defaultScreenId = cfg.defaultScreenId || tabs[0]?.screenId || "main";
  const adminPreview = tabsGroup ? sectionScreen?.adminPreviewScreenByGroup[tabsGroup] : undefined;
  const runtimeActive = tabsGroup ? sectionScreen?.activeScreenByGroup[tabsGroup] : undefined;
  const cabinetActive = isCabinetGroup ? cabinetTabsCtx?.activeTabByGroup?.[tabsGroup] : undefined;
  const activeScreen = (adminControl ? adminPreview : (runtimeActive || cabinetActive)) || defaultScreenId;

  const listRef = useRef<HTMLDivElement>(null);
  const tabRefs = useRef(new Map<string, HTMLButtonElement>());
  const pillMountedRef = useRef(false);
  const [pillRect, setPillRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null);

  useEffect(() => {
    const measure = () => {
      const el = tabRefs.current.get(activeScreen);
      if (!el) {
        setPillRect(null);
        return;
      }
      setPillRect({ left: el.offsetLeft, top: el.offsetTop, width: el.offsetWidth, height: el.offsetHeight });
    };
    measure();
    const raf = requestAnimationFrame(() => {
      measure();
      pillMountedRef.current = true;
    });
    const list = listRef.current;
    if (!list || typeof ResizeObserver === "undefined") return () => cancelAnimationFrame(raf);
    const ro = new ResizeObserver(measure);
    ro.observe(list);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [activeScreen, tabs.length]);

  const handleClick = (screenId: string) => {
    if (adminControl) {
      if (tabsGroup && sectionScreen) sectionScreen.setAdminPreviewScreen(tabsGroup, screenId);
      return;
    }
    if (isCabinetGroup && cabinetTabsCtx) {
      cabinetTabsCtx.setActiveTab(tabsGroup, screenId);
      return;
    }
    if (tabsGroup && sectionScreen) sectionScreen.openScreen(tabsGroup, screenId);
  };

  return wrap(
    <div
      ref={listRef}
      className={ELEMENT_FILL_CLASS}
      role="tablist"
      aria-orientation={vertical ? "vertical" : "horizontal"}
      style={{
        position: "relative",
        display: "flex",
        flexDirection: vertical ? "column" : "row",
        gap: 4,
        fontFamily: t.monoFont,
        alignItems: "stretch",
        borderRadius: t.radius.md,
        background: t.innerBg,
        padding: 4,
        overflowX: vertical ? "visible" : "auto",
        overflowY: vertical ? "auto" : "visible",
        ...decor,
      }}
    >
      {pillRect ? (
        <span
          aria-hidden
          style={{
            position: "absolute",
            left: pillRect.left,
            top: pillRect.top,
            width: pillRect.width,
            height: pillRect.height,
            borderRadius: NAV_R,
            background: t.ink,
            boxShadow: buttonShadow(t),
            transition: pillMountedRef.current
              ? `left ${SEGMENT_SLIDE_TRANSITION}, top ${SEGMENT_SLIDE_TRANSITION}, width ${SEGMENT_SLIDE_TRANSITION}, height ${SEGMENT_SLIDE_TRANSITION}`
              : "none",
            pointerEvents: "none",
          }}
        />
      ) : null}
      {tabs.map((tab, index) => {
        const isActive = tab.screenId === activeScreen;
        const label = tab.label || tab.screenId || "—";
        const iconRef = (tab.iconUrl && tab.iconUrl.trim()) || (tab.iconName && tab.iconName.trim()) || "";
        const showIcon = (displayMode === "icon" || displayMode === "icon-text") && Boolean(iconRef);
        const showText = displayMode !== "icon";
        return (
          <button
            key={`${tab.screenId}-${index}`}
            ref={(el) => {
              const id = tab.screenId || "";
              if (el) tabRefs.current.set(id, el);
              else tabRefs.current.delete(id);
            }}
            type="button"
            role="tab"
            aria-selected={isActive}
            tabIndex={isActive ? 0 : -1}
            onClick={() => tab.screenId && handleClick(tab.screenId)}
            style={{
              position: "relative",
              zIndex: 1,
              flex: vertical ? "0 0 auto" : "1 1 0",
              minWidth: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: vertical ? "flex-start" : "center",
              gap: 8,
              padding: `${t.space.smPlus}px ${t.space.md}px`,
              border: "none",
              borderRadius: NAV_R,
              background: "transparent",
              fontFamily: t.monoFont,
              fontSize: t.font.sm,
              fontWeight: isActive ? t.weight.bold : t.weight.medium,
              color: isActive ? pickContrast(t.ink) : t.inkDim,
              cursor: "pointer",
              transition: "color 200ms ease",
              whiteSpace: "nowrap",
            }}
          >
            {showIcon ? (
              <span style={{ color: isActive ? pickContrast(t.ink) : t.inkDim, display: "inline-flex", flexShrink: 0, transition: "color 200ms ease" }}>
                <NavIcon name={iconRef} size={vertical ? 20 : 16} />
              </span>
            ) : null}
            {showText ? <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{label}</span> : null}
          </button>
        );
      })}
    </div>,
    false,
    true,
  );
}
