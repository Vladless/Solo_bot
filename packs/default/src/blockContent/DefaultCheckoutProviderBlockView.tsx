"use client";

import { useEffect } from "react";
import { DEFAULT_PROVIDER_LABELS, PREVIEW_PROVIDERS, type ProviderLabels } from "@/components/constructor/blockContent/checkout/providerLabels";
import { useAppInfo } from "@/app/AppInfoProvider";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, panelShadowSm, pickContrast } from "./defaultTheme";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { useCheckoutFlowContext } from "@/components/constructor/blockContent/checkout/checkoutFlowContext";
import { useFlowPage } from "@/lib/flow-page-provider";
import { hexToRgba } from "@/components/constructor/utils";


const DCP_CSS = `
.dcp-anim > * { animation: dcp-up .5s cubic-bezier(.22,.9,.28,1) both; }
.dcp-anim > *:nth-child(2) { animation-delay: 60ms; }
.dcp-anim > *:nth-child(3) { animation-delay: 120ms; }
.dcp-anim > *:nth-child(4) { animation-delay: 180ms; }
@keyframes dcp-up { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: none; } }
.dcp-opt { transition: border-color .16s, background .16s, box-shadow .16s; }
.dcp-opt:not(.dcp-active):hover { border-color: var(--dcp-accent) !important; }
`;

export function DefaultCheckoutProviderBlockView({ block, context, editMode }: TypedBlockViewProps<"defaultCheckoutProvider">) {
  const { wrap } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);

  const title = typeof d.title === "string" ? d.title : "Способ оплаты";
  const subtitle = typeof d.subtitle === "string" ? d.subtitle : "";
  const noProvidersText = typeof d.noProvidersText === "string" ? d.noProvidersText : "Нет доступных способов оплаты";
  const balanceCoversText = typeof d.balanceCoversText === "string" ? d.balanceCoversText : "Спишется с баланса — выбор кассы не нужен.";
  const defaultProvider = String(d.defaultProvider ?? "").trim();
  const showSingleProvider = d.showSingleProvider !== false;
  const customLabels = (d.providerLabels ?? {}) as ProviderLabels;

  const appInfo = useAppInfo();
  const checkout = useCheckoutFlowContext();
  const flowPage = useFlowPage();

  const previewMode = context.previewMode === true;
  const live = checkout?.available === true && !editMode && !previewMode;

  const allProviders = appInfo.payments.webLinkProviderIds;
  const flowFilter = flowPage?.active ? flowPage.currentNode?.config?.providerIds : undefined;
  const filteredProviders = Array.isArray(flowFilter) && flowFilter.length > 0
    ? allProviders.filter((id) => flowFilter.includes(id))
    : allProviders;
  const providerOrder = Array.isArray(d.providerOrder)
    ? (d.providerOrder as unknown[]).filter((x): x is string => typeof x === "string")
    : [];
  const liveProviders = providerOrder.length > 0
    ? [...filteredProviders].sort((a, b) => {
        const ia = providerOrder.indexOf(a);
        const ib = providerOrder.indexOf(b);
        return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
      })
    : filteredProviders;
  const providers = liveProviders.length > 0 ? liveProviders : PREVIEW_PROVIDERS;

  const selectedId = checkout?.providerId ?? "";
  const setSelectedId = checkout?.setProviderId;

  useEffect(() => {
    if (!live || !setSelectedId || selectedId) return;
    if (defaultProvider && providers.includes(defaultProvider)) {
      setSelectedId(defaultProvider);
    } else if (providers.length > 0) {
      setSelectedId(providers[0]);
    }
  }, [live, defaultProvider, providers, selectedId, setSelectedId]);

  const onAccent = pickContrast(t.accent);
  const effectiveSelected = selectedId || (providers.length > 0
    ? (defaultProvider && providers.includes(defaultProvider) ? defaultProvider : providers[0])
    : "");

  function label(id: string): string {
    const custom = customLabels[id];
    if (custom && custom.trim()) return custom.trim();
    return appInfo.payments.providerLabels?.[id] ?? DEFAULT_PROVIDER_LABELS[id] ?? id;
  }

  const showSelector = providers.length > 1 || editMode || previewMode;
  const singleProvider = providers.length === 1;

  if (live && Math.round(checkout!.requiredToPayRub) === 0) {
    const balanceNode = (
      <div
        className="dcp-anim"
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          gap: 12,
          padding: "22px 26px",
          borderRadius: t.radius.md,
          background: t.panel,
          boxShadow: panelShadowSm(t),
          fontFamily: t.monoFont,
          color: t.ink,
          boxSizing: "border-box",
          ...decor,
        }}
      >
        <style>{DCP_CSS}</style>
        <div>
          <div style={{ fontSize: t.font.lg, fontWeight: t.weight.bold, letterSpacing: "-0.01em", color: t.ink }}>{title}</div>
        </div>
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          fontSize: t.font.smPlus,
          fontWeight: t.weight.medium,
          color: t.ink,
          padding: "14px 16px",
          borderRadius: t.radius.sm,
          background: t.innerBg,
          border: `1px solid ${t.line}`,
        }}>{balanceCoversText}</div>
      </div>
    );
    return wrap ? wrap(balanceNode, false, true) : balanceNode;
  }

  const node = (
    <div
      className="dcp-anim"
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 12,
        padding: "22px 26px",
        borderRadius: t.radius.md,
        background: t.panel,
        boxShadow: panelShadowSm(t),
        fontFamily: t.monoFont,
        color: t.ink,
        boxSizing: "border-box",
        overflow: "hidden",
        ["--dcp-accent" as never]: t.accent,
        ...decor,
      }}
    >
      <style>{DCP_CSS}</style>

      <div>
        <div style={{ fontSize: t.font.lg, fontWeight: t.weight.bold, letterSpacing: "-0.01em", color: t.ink }}>{title}</div>
        {subtitle ? <div style={{ fontSize: t.font.sm, color: t.inkMute, marginTop: 3 }}>{subtitle}</div> : null}
      </div>

      {providers.length === 0 ? (
        <div style={{
          fontSize: t.font.sm,
          color: t.inkMute,
          padding: "14px 16px",
          borderRadius: t.radius.sm,
          background: t.innerBg,
          border: `1px solid ${t.line}`,
        }}>{noProvidersText}</div>
      ) : !showSelector && singleProvider && showSingleProvider ? (
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          fontSize: t.font.smPlus,
          fontWeight: t.weight.medium,
          color: t.ink,
          padding: "14px 16px",
          borderRadius: t.radius.sm,
          background: t.innerBg,
          border: `1px solid ${t.line}`,
        }}>{label(providers[0])}</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, flex: "1 1 auto", minHeight: 0, overflowY: "auto" }}>
          {providers.map((id) => {
            const isSelected = effectiveSelected === id;
            return (
              <button
                key={id}
                type="button"
                disabled={editMode}
                onClick={() => { if (!editMode) setSelectedId?.(id); }}
                className={`dcp-opt${isSelected ? " dcp-active" : ""}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  width: "100%",
                  textAlign: "left",
                  padding: "13px 16px",
                  borderRadius: t.radius.sm,
                  border: `1.5px solid ${isSelected ? t.accent : t.line}`,
                  background: isSelected ? hexToRgba(t.accent, 0.08) : t.innerBg,
                  color: t.ink,
                  fontFamily: t.monoFont,
                  cursor: editMode ? "default" : "pointer",
                }}
              >
                <span
                  style={{
                    flexShrink: 0,
                    width: 20,
                    height: 20,
                    borderRadius: "50%",
                    border: `2px solid ${isSelected ? t.accent : t.lineStrong}`,
                    background: isSelected ? t.accent : "transparent",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  {isSelected ? <span style={{ width: 8, height: 8, borderRadius: "50%", background: onAccent }} /> : null}
                </span>
                <span style={{ fontSize: t.font.smPlus, fontWeight: isSelected ? t.weight.bold : t.weight.medium, color: t.ink }}>
                  {label(id)}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );

  return wrap ? wrap(node, false, true) : node;
}
