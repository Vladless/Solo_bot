"use client";

import { useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, DefaultPanel, btnSolid, f, parseBlockData, useBlockApi } from ".";
import { DefaultFooter } from "./layout";
import { DefaultAddonsScreen, DEFAULT_ADDONS_LABELS } from "./DefaultAddonsScreen";
import { hexToRgba } from "@/components/constructor/utils";
import { formatGigabytes } from "@/lib/format-number";

const SCHEMA = {
  panelHeader: f.str("Трафик"),
  usedFormat: f.str("{used} / {limit} ГБ"),
  remainingFormat: f.str("Осталось {left} ГБ"),
  unlimitedText: f.str("Безлимитный трафик"),
  unlimitedHint: f.str("∞"),
  noKeysText: f.str("Нет активной подписки"),
  loadingText: f.str("Загрузка..."),
  lowWarnPercent: f.num(90),
  addonLabel: f.str("Докупить трафик"),
  addonsHint: f.str(""),
  addonsTrafficLabel: f.str("Трафик"),
  addonsTrafficUnitFormat: f.str("{n} ГБ"),
  addonsTrafficUnlimitedText: f.str("∞"),
  addonsUnavailableText: f.str("Для текущего тарифа нет доступных опций"),
  addonsTotalLabel: f.str("Итого к оплате"),
  addonsSubmitLabel: f.str("Оформить"),
  addonsCancelLabel: f.str("Отмена"),
};


export function DefaultTrafficUsageBlockView({ block, context }: TypedBlockViewProps<"defaultTrafficUsage">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, SCHEMA);
  const isPreview = previewMode === true;

  const api = useBlockApi({ needs: ["activeSubscription", "activeTariff"], disabled: isPreview });
  const details = isPreview ? null : api.activeDetails;
  const limitGb = isPreview ? 200 : (typeof details?.traffic_limit_gb === "number" ? details.traffic_limit_gb : null);
  const usedGb = isPreview ? 84 : (typeof details?.used_traffic_gb === "number" ? details.used_traffic_gb : 0);

  const noKey = !isPreview && !api.keys.isLoading && api.activeKey === null;
  const loadingState = !isPreview && !noKey && (api.keys.isLoading || (api.activeKey !== null && api.activeDetails === undefined));

  const unlimited = limitGb === null || limitGb <= 0;
  const pct = unlimited ? 0 : Math.max(0, Math.min(100, Math.round((usedGb / limitGb!) * 100)));
  const leftGb = unlimited ? 0 : Math.max(0, limitGb! - usedGb);
  const low = !unlimited && pct >= Math.max(1, Math.min(100, cfg.lowWarnPercent));
  const barColor = low ? t.error : t.accent;

  const usedText = cfg.usedFormat.replace("{used}", formatGigabytes(usedGb)).replace("{limit}", formatGigabytes(limitGb ?? 0));
  const remainingText = cfg.remainingFormat.replace("{left}", formatGigabytes(leftGb));

  const addonsEnabled = isPreview ? true : Boolean(details?.addons_traffic_enabled);
  const showAddon = !unlimited && addonsEnabled && pct >= Math.max(1, Math.min(100, cfg.lowWarnPercent));
  const trafficOptions: number[] = Array.isArray(api.activeTariff?.addon_traffic_options) ? api.activeTariff!.addon_traffic_options! : (Array.isArray(api.activeTariff?.traffic_options_gb) ? api.activeTariff!.traffic_options_gb : []);
  const [addonsOpen, setAddonsOpen] = useState(false);
  const openAddons = () => {
    if (isPreview) return;
    setAddonsOpen(true);
  };

  return wrap(
    <DefaultPanel
      t={t}
      header={cfg.panelHeader}
      hint={unlimited ? cfg.unlimitedHint : `${pct}%`}
      hintColor={low ? t.error : undefined}
      loading={loadingState}
      loadingText={cfg.loadingText}
      empty={noKey}
      emptyText={cfg.noKeysText}
      bodyStyle={{ flex: "1 1 auto", justifyContent: "flex-start" }}
    >
      {addonsOpen ? (
        <DefaultAddonsScreen
          t={t}
          focus="traffic"
          activeKey={api.activeKey}
          details={api.activeDetails}
          deviceOptions={[]}
          trafficOptions={trafficOptions}
          checkoutSlug="checkout"
          labels={{
            ...DEFAULT_ADDONS_LABELS,
            title: cfg.addonLabel,
            hint: cfg.addonsHint,
            trafficLabel: cfg.addonsTrafficLabel,
            trafficUnitFormat: cfg.addonsTrafficUnitFormat,
            trafficUnlimitedText: cfg.addonsTrafficUnlimitedText,
            totalLabel: cfg.addonsTotalLabel,
            submitLabel: cfg.addonsSubmitLabel,
            cancelLabel: cfg.addonsCancelLabel,
            unavailableText: cfg.addonsUnavailableText,
          }}
          onClose={() => setAddonsOpen(false)}
          isPreview={isPreview}
        />
      ) : unlimited ? (
        <div style={{ display: "flex", alignItems: "center", gap: t.space.smPlus, color: t.inkDim, fontSize: t.font.sm }}>
          <span style={{ fontSize: t.font.xl, fontWeight: t.weight.bold, color: t.ink, lineHeight: 1 }}>∞</span>
          {cfg.unlimitedText}
        </div>
      ) : (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: t.space.smPlus }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: t.space.md }}>
              <span style={{ fontSize: t.font.lg, fontWeight: t.weight.bold, color: t.ink, fontVariantNumeric: "tabular-nums" }}>{usedText}</span>
              <span style={{ fontSize: t.font.sm, color: t.inkDim }}>{remainingText}</span>
            </div>
            <div style={{ height: 8, borderRadius: 999, background: hexToRgba(barColor, 0.18), overflow: "hidden" }}>
              <div data-motion-bar="true" style={{ width: `${Math.max(2, pct)}%`, height: "100%", borderRadius: 999, background: barColor, transition: "width 320ms cubic-bezier(0.3,0.9,0.4,1), background 200ms ease" }} />
            </div>
          </div>
          <DefaultFooter t={t}>
            {showAddon ? (
              <button type="button" onClick={openAddons} style={{ ...btnSolid(t), width: "100%", justifyContent: "center", cursor: "pointer" }}>
                {cfg.addonLabel}
              </button>
            ) : null}
          </DefaultFooter>
        </>
      )}
    </DefaultPanel>,
    false,
    true,
  );
}
