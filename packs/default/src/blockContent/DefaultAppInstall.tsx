"use client";

import type { CSSProperties } from "react";
import { hexToRgba } from "@/components/constructor/utils";
import { btnSecondary, btnSm, btnGhost, pickContrast, CONTROL_R, type DefaultTheme } from "./defaultTheme";
import type { AppItem, AppPlatform, AppStep } from "@/components/constructor/blockContent/appCatalog";
import { appDownloadUrl } from "@/components/constructor/blockContent/appCatalog";

export type ChipPalette = {
  idleBg: string;
  idleInk: string;
  activeBg: string;
  activeInk: string;
};

type ChipOption = { id: string; label: string };

function chipPalette(t: DefaultTheme): ChipPalette {
  return { idleBg: t.innerBg, idleInk: t.inkDim, activeBg: t.accent, activeInk: pickContrast(t.accent) };
}

export function DefaultChipPicker({
  t,
  options,
  value,
  onChange,
  palette,
}: {
  t: DefaultTheme;
  options: ChipOption[];
  value: string;
  onChange: (id: string) => void;
  palette?: ChipPalette;
}) {
  const colors = palette ?? chipPalette(t);
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: t.space.sm }}>
      {options.map((option) => {
        const active = option.id === value;
        return (
          <button
            key={option.id}
            type="button"
            onClick={() => onChange(option.id)}
            style={{
              ...btnSecondary(t),
              ...btnSm(t),
              borderRadius: CONTROL_R,
              background: active ? colors.activeBg : colors.idleBg,
              color: active ? colors.activeInk : colors.idleInk,
              fontWeight: active ? t.weight.bold : t.weight.medium,
              cursor: "pointer",
            }}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function DefaultAppList({
  t,
  apps,
  platform,
  selectedId,
  onSelect,
  downloadLabel,
  emptyText,
  linksDisabled = false,
}: {
  t: DefaultTheme;
  apps: AppItem[];
  platform: AppPlatform;
  selectedId: string;
  onSelect: (app: AppItem) => void;
  downloadLabel: string;
  emptyText: string;
  linksDisabled?: boolean;
}) {
  if (apps.length === 0) {
    return <div style={{ fontSize: t.font.smPlus, color: t.inkMute }}>{emptyText}</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: t.space.sm }}>
      {apps.map((app) => {
        const active = app.id === selectedId;
        const url = appDownloadUrl(app, platform);
        const rowStyle: CSSProperties = {
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: t.space.md,
          width: "100%",
          padding: `${t.space.md}px ${t.space.mdPlus}px`,
          borderRadius: t.radius.sm,
          background: active ? hexToRgba(t.accent, 0.12) : t.innerBg,
          border: `1px solid ${active ? t.accent : "transparent"}`,
          textAlign: "left",
          cursor: "pointer",
          transition: "background 200ms ease, border-color 200ms ease",
        };
        return (
          <div
            key={app.id}
            role="button"
            tabIndex={0}
            data-interactive
            aria-pressed={active}
            onClick={() => onSelect(app)}
            onKeyDown={(e) => {
              if (e.key !== "Enter" && e.key !== " ") return;
              e.preventDefault();
              onSelect(app);
            }}
            style={rowStyle}
          >
            <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
              <div style={{ display: "flex", alignItems: "center", gap: t.space.sm, minWidth: 0 }}>
                <span style={{ fontSize: t.font.md, color: t.ink, fontWeight: t.weight.bold }}>{app.name}</span>
                {app.badge ? (
                  <span
                    style={{
                      flexShrink: 0,
                      fontSize: t.font.xxs,
                      color: t.accent,
                      background: hexToRgba(t.accent, 0.14),
                      borderRadius: CONTROL_R,
                      padding: `${t.px(2)}px ${t.px(8)}px`,
                    }}
                  >
                    {app.badge}
                  </span>
                ) : null}
              </div>
              {app.note ? (
                <span style={{ fontSize: t.font.xs, color: t.inkMute, lineHeight: 1.45 }}>{app.note}</span>
              ) : null}
            </div>
            {url && downloadLabel ? (
              <a
                href={linksDisabled ? undefined : url}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => e.stopPropagation()}
                style={{
                  ...btnSecondary(t),
                  ...btnSm(t),
                  flexShrink: 0,
                  borderRadius: CONTROL_R,
                  textDecoration: "none",
                  color: t.accent,
                  cursor: linksDisabled ? "default" : "pointer",
                }}
              >
                {downloadLabel}
              </a>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

export function DefaultStepList({ t, steps }: { t: DefaultTheme; steps: AppStep[] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: t.space.md }}>
      {steps.map((step, i) => (
        <div key={`${step.title}-${i}`} style={{ display: "flex", gap: t.space.md, alignItems: "flex-start" }}>
          <span
            style={{
              flexShrink: 0,
              width: t.px(28),
              height: t.px(28),
              borderRadius: CONTROL_R,
              background: hexToRgba(t.accent, 0.14),
              color: t.accent,
              fontSize: t.font.xs,
              fontWeight: t.weight.bold,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            {i + 1}
          </span>
          <div style={{ minWidth: 0 }}>
            {step.title ? (
              <div style={{ fontSize: t.font.smPlus, color: t.ink, fontWeight: t.weight.medium }}>{step.title}</div>
            ) : null}
            {step.text ? (
              <div style={{ fontSize: t.font.xs, color: t.inkMute, lineHeight: 1.5, marginTop: t.px(2) }}>{step.text}</div>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}

export function DefaultStageBar({
  t,
  title,
  counter,
  backLabel,
  onBack,
}: {
  t: DefaultTheme;
  title: string;
  counter: string;
  backLabel: string;
  onBack: (() => void) | null;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: t.space.md }}>
      <div style={{ display: "flex", alignItems: "center", gap: t.space.sm, minWidth: 0 }}>
        {onBack && backLabel ? (
          <button type="button" onClick={onBack} style={{ ...btnGhost(t), ...btnSm(t), borderRadius: CONTROL_R, color: t.inkDim }}>
            {backLabel}
          </button>
        ) : null}
        <span style={{ fontSize: t.font.smPlus, color: t.ink, fontWeight: t.weight.medium }}>{title}</span>
      </div>
      {counter ? <span style={{ flexShrink: 0, fontSize: t.font.xs, color: t.inkDim }}>{counter}</span> : null}
    </div>
  );
}
