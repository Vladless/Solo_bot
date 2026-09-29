"use client";

import { useEffect, useMemo, useState } from "react";
import { metricPair, type SubscriptionMetricsContext } from "@/components/constructor/blockContent/statMetrics";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { useDefaultTheme, statTileStyle, iconSquareStyle, useIsMobile } from "./defaultTheme";
import { useBlockApi } from "@/components/constructor/blockContent/cabinetKit/apiHooksRegistry";
import { f, parseBlockData } from "@/components/constructor/blockContent/cabinetKit/blockSchema";
import type { CabinetStatTile } from "@/components/constructor/blockData/blocks";

const STAT_STRIP_SCHEMA = {
  tiles: f.array<CabinetStatTile>(),
};

const DEFAULT_TILES: CabinetStatTile[] = [
  { metric: "balance" },
  { metric: "keysTotal" },
  { metric: "referralsTotal" },
  { metric: "referralBonusTotal" },
];

export function DefaultStatStripBlockView({ block, context }: TypedBlockViewProps<"defaultStatStrip">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);

  const { tiles: rawTiles } = parseBlockData(d, STAT_STRIP_SCHEMA);
  const tiles = rawTiles.length > 0 ? rawTiles : DEFAULT_TILES;

  const isMobile = useIsMobile(760);
  const cols = isMobile ? Math.min(2, tiles.length) : tiles.length;

  const usesSubscriptionMetric = useMemo(
    () => tiles.some((tt) => tt.metric === "subscriptionDevices" || tt.metric === "subscriptionTraffic" || tt.metric === "subscriptionDaysLeft" || tt.metric === "subscriptionRenewalDate" || tt.metric === "subscriptionRenewalPrice"),
    [tiles],
  );
  const usesTariffMetric = useMemo(
    () => tiles.some((tt) => tt.metric === "subscriptionRenewalPrice"),
    [tiles],
  );
  const apiNeeds = useMemo(() => {
    const needs: Array<"summary" | "activeSubscription" | "activeTariff"> = ["summary"];
    if (usesSubscriptionMetric) needs.push("activeSubscription");
    if (usesTariffMetric) needs.push("activeTariff");
    return needs;
  }, [usesSubscriptionMetric, usesTariffMetric]);
  const api = useBlockApi({ needs: apiNeeds, disabled: previewMode === true, mock: previewMode === true });
  const summary = { data: api.summary.data, isLoading: api.summary.isLoading };
  const [nowMs, setNowMs] = useState<number>(() => (typeof window !== "undefined" ? Date.now() : 0));
  useEffect(() => {
    if (!usesSubscriptionMetric) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, [usesSubscriptionMetric]);
  const subLoading = usesSubscriptionMetric && api.keys.isLoading;
  const subContext: SubscriptionMetricsContext = {
    details: api.activeDetails ?? null,
    tariff: api.activeTariff,
    expiryMs: api.activeKey?.expiry_time ?? null,
    nowMs,
    loading: subLoading,
  };


  return wrap(
    <div className={ELEMENT_FILL_CLASS} style={{ display: "grid", gridTemplateColumns: `repeat(${cols}, minmax(0,1fr))`, gap: isMobile ? t.space.smPlus : t.space.lg, ...decor }}>
      {tiles.map((tile, i) => {
        const pair = tile.metric && tile.metric !== "static" ? metricPair(tile.metric, summary.data, subContext) : null;
        const label = tile.label ?? pair?.label ?? "—";
        const value = tile.value ?? pair?.value ?? (summary.isLoading || subLoading ? "…" : "—");
        const unit = tile.unit ?? pair?.unit ?? "";
        const delta = tile.delta;
        const dDir = (tile.deltaDirection as string | undefined) ?? "up";
        return (
          <StatTile
            key={i}
            t={t}
            tint={t.accent}
            metric={tile.metric}
            label={label}
            value={value}
            unit={unit}
            delta={delta}
            deltaDirection={dDir}
            compact={isMobile}
          />
        );
      })}
    </div>,
    false,
    true,
  );
}

const ICON_BY_METRIC: Record<string, string> = {
  balance: "wallet", partnerBalance: "wallet", referralBonusTotal: "wallet", subscriptionRenewalPrice: "wallet",
  keysTotal: "key",
  referralsTotal: "users", referralsActive: "users", partnerReferredTotal: "users",
  partnerReferredPaid: "check", giftsClaimed: "check", trialStatus: "check",
  referralConversion: "percent", partnerConversion: "percent", partnerPercent: "percent",
  giftsSent: "gift", couponsUsed: "gift",
  unreadNotifications: "bell",
};

function metricIcon(metric: string | undefined): React.ReactNode {
  const common = { width: 20, height: 20, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (ICON_BY_METRIC[metric ?? ""] ?? "chart") {
    case "wallet": return (<svg {...common}><rect x="3" y="6" width="18" height="13" rx="2.5" /><path d="M3 10.5h18" /><circle cx="16.5" cy="14" r="1.3" /></svg>);
    case "users": return (<svg {...common}><circle cx="9" cy="8" r="3" /><path d="M3 19v-1a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v1" /><path d="M16 5.2a3 3 0 0 1 0 5.6M21 19v-1a4 4 0 0 0-3-3.8" /></svg>);
    case "check": return (<svg {...common}><circle cx="12" cy="12" r="9" /><path d="m8.5 12 2.4 2.4 4.6-5" /></svg>);
    case "percent": return (<svg {...common}><line x1="19" y1="5" x2="5" y2="19" /><circle cx="7.5" cy="7.5" r="2.2" /><circle cx="16.5" cy="16.5" r="2.2" /></svg>);
    case "key": return (<svg {...common}><circle cx="8" cy="15" r="4" /><path d="M10.8 12.2 20 3m-3.5.5 3 3M14 6l2.5 2.5" /></svg>);
    case "gift": return (<svg {...common}><rect x="3.5" y="9" width="17" height="11" rx="1.5" /><path d="M12 9v11M3.5 13h17" /><path d="M12 9C12 9 10.5 4.5 8 5.5S9.5 9 12 9zm0 0s1.5-4.5 4-3.5S14.5 9 12 9z" /></svg>);
    case "bell": return (<svg {...common}><path d="M6 9a6 6 0 1 1 12 0c0 4.5 2 5.5 2 5.5H4S6 13.5 6 9z" /><path d="M9.5 18a2.5 2.5 0 0 0 5 0" /></svg>);
    default: return (<svg {...common}><path d="M4 20V11M9.5 20V5M15 20v-6M20.5 20V8" /></svg>);
  }
}

function StatTile({ t, tint, metric, label, value, unit, delta, deltaDirection, compact }: { t: ReturnType<typeof useDefaultTheme>; tint: string; metric?: string; label: string; value: string; unit: string; delta?: string; deltaDirection: string; compact?: boolean }) {
  const deltaColor = deltaDirection === "down" ? t.error : deltaDirection === "neutral" || deltaDirection === "flat" ? t.inkDim : t.success;
  const deltaArrow = deltaDirection === "down" ? "↓ " : deltaDirection === "neutral" || deltaDirection === "flat" ? "" : "↑ ";
  const iconSize = compact ? 32 : 40;
  const baseValueSize = compact ? 17 : 22;
  const valueLen = value.length + (unit ? unit.length + 1 : 0);
  const valueSize = valueLen <= 6 ? baseValueSize : Math.max(compact ? 12 : 14, Math.round(baseValueSize - (valueLen - 6) * 1.4));
  const labelSize = compact ? t.font.xs : t.font.sm;
  const pad = compact ? "10px 12px" : "14px 16px";
  return (
    <div style={{ ...statTileStyle(t), flexDirection: "row", alignItems: "center", gap: compact ? 10 : 13, padding: pad }}>
      <div style={{ ...iconSquareStyle(t, tint, iconSize), flexShrink: 0 }}>{metricIcon(metric)}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
        <div style={{ fontSize: valueSize, fontWeight: t.weight.bold, letterSpacing: "-0.01em", fontVariantNumeric: "tabular-nums", lineHeight: 1.1, color: t.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {value}
          {unit ? <span style={{ color: t.inkDim, fontSize: "0.7em", marginLeft: 4, fontWeight: t.weight.medium }}>{unit}</span> : null}
          {delta ? <span style={{ color: deltaColor, fontSize: compact ? 11 : 12, marginLeft: 6, fontWeight: t.weight.medium }}>{deltaArrow}{delta}</span> : null}
        </div>
        <div style={{ fontSize: labelSize, color: t.inkDim, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{label}</div>
      </div>
    </div>
  );
}
