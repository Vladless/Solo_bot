"use client";

import { usePathname } from "next/navigation";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { useDefaultTheme } from "./defaultTheme";
import { f, parseBlockData } from "@/components/constructor/blockContent/cabinetKit/blockSchema";
import { useCabinetTabs } from "@/app/(marketing)/landing/CabinetTabContext";
import { DASHBOARD_TAB_GROUP_ID } from "@/app/(marketing)/landing/dashboardTabSections";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";

const PAGE_HEADER_SCHEMA = {
  source: f.enum(["auto", "manual"] as const, "auto"),
  title: f.str("Заголовок"),
  subtitle: f.str(""),
  meta: f.str(""),
};

const TAB_LABELS: Record<string, string> = {
  profile: "Профиль",
  keys: "Подписки",
  subscriptions: "Подписки",
  dashboard: "Подписки",
  instructions: "Инструкции",
  referrals: "Рефералы",
  partners: "Партнёрка",
  gifts: "Подарки",
  notifications: "Уведомления",
};

const TAB_SUBTITLES: Record<string, string> = {
  profile: "Личные данные и безопасность аккаунта",
  keys: "Тариф, оплата и история платежей",
  subscriptions: "Тариф, оплата и история платежей",
  dashboard: "Тариф, оплата и история платежей",
  instructions: "Инструкции по подключению",
  referrals: "Приглашайте друзей и зарабатывайте вместе с нами",
  partners: "Приглашайте друзей и зарабатывайте вместе с нами",
  gifts: "Промо-акции и бонусы",
  notifications: "Системные события и важные оповещения",
};

export function DefaultPageHeaderBlockView({ block, context }: TypedBlockViewProps<"defaultPageHeader">) {
  const decor = usePanelDecor();
  const { wrap } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, PAGE_HEADER_SCHEMA);
  const pathname = usePathname();
  const cabinetTabs = useCabinetTabs();

  const activeTabId = cabinetTabs?.activeTabByGroup?.[DASHBOARD_TAB_GROUP_ID];
  const segment = (() => {
    if (!pathname) return null;
    const parts = pathname.split("/").filter(Boolean);
    const dashIdx = parts.indexOf("dashboard");
    if (dashIdx === -1) return null;
    return parts[dashIdx + 1] ?? "dashboard";
  })();
  const tabKey = activeTabId || segment;

  const isAuto = cfg.source === "auto";
  const autoLabel = tabKey ? (TAB_LABELS[tabKey] ?? null) : null;
  const autoSubtitle = tabKey ? (TAB_SUBTITLES[tabKey] ?? null) : null;
  const title = isAuto && autoLabel ? autoLabel : cfg.title;
  const subtitle = cfg.subtitle || (isAuto && autoSubtitle ? autoSubtitle : "") || cfg.meta;
  const subtitleExpected = isAuto && !cfg.subtitle && !cfg.meta;

  return wrap(
    <div
      className={ELEMENT_FILL_CLASS}
      style={{
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: 8,
        fontFamily: t.monoFont,
        color: t.ink,
        ...decor,
      }}
    >
      <h1
        style={{
          fontSize: "clamp(26px, 4cqi, 34px)",
          lineHeight: 1.05,
          letterSpacing: "-0.02em",
          fontWeight: t.weight.bold,
          margin: 0,
          color: t.ink,
        }}
      >
        {title}
      </h1>
      {subtitle || subtitleExpected ? (
        <div style={{ fontSize: t.font.md, color: t.inkDim, lineHeight: 1.4, minHeight: `${Math.round(t.font.md * 1.4)}px` }}>
          {subtitle}
        </div>
      ) : null}
    </div>,
    false,
    true,
  );
}
