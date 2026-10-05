"use client";

import type React from "react";
import type { WebTariffPublic } from "@/components/constructor/blockContent/tariffTypes";
import { useDefaultTheme, btnGhost, btnRed, buttonShadow } from "..";

type DefaultTheme = ReturnType<typeof useDefaultTheme>;

/** Экран «подберите опции»: устройства, трафик и цена выбранного тарифа. */
export function PickOptionsView({
  t,
  pickedTariff,
  pickDeviceDraft,
  setPickDeviceDraft,
  pickTrafficDraft,
  setPickTrafficDraft,
  pickPriceText,
  submitBusy,
  addonsSubmitLabel,
  addonsCancelLabel,
  addonsDevicesLabel,
  addonsTrafficLabel,
  switchTariffBackLabel,
  closePickOptions,
  onPickOptionsSubmit,
}: {
  t: DefaultTheme;
  pickedTariff: WebTariffPublic | null;
  pickDeviceDraft: string;
  setPickDeviceDraft: (value: string) => void;
  pickTrafficDraft: string;
  setPickTrafficDraft: (value: string) => void;
  pickPriceText: string;
  submitBusy: boolean;
  addonsSubmitLabel: string;
  addonsCancelLabel: string;
  addonsDevicesLabel: string;
  addonsTrafficLabel: string;
  switchTariffBackLabel: string;
  closePickOptions: () => void;
  onPickOptionsSubmit: () => void;
}) {
    if (!pickedTariff) return null;
    const tr = pickedTariff;
    const devOpts: number[] = Array.isArray(tr.device_options) ? tr.device_options : [];
    const trOpts: number[] = Array.isArray(tr.traffic_options_gb) ? tr.traffic_options_gb : [];
    const sectionLabelStyle: React.CSSProperties = {
      fontSize: t.font.xs,
      color: t.inkDim,
      letterSpacing: t.tracking.ultra,
      textTransform: "none",
      fontWeight: t.weight.bold,
      marginBottom: t.space.sm,
    };
    const renderOptions = (
      options: number[],
      selected: string,
      onChange: (v: string) => void,
      unitLabel: string,
    ) => (
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(auto-fit, minmax(110px, 1fr))`,
          gap: t.space.sm,
        }}
      >
        {options.map((value) => {
          const valStr = String(value);
          const isActive = selected === valStr;
          const label = value <= 0 ? `∞ ${unitLabel}` : `${value} ${unitLabel}`;
          return (
            <button
              key={valStr}
              type="button"
              onClick={() => onChange(valStr)}
              style={{
                padding: `${t.space.md}px ${t.space.sm}px`,
                borderRadius: t.radius.sm,
                border: "none",
                background: isActive ? t.panel : t.innerBg,
                boxShadow: isActive ? buttonShadow(t) : "none",
                color: isActive ? t.ink : t.inkDim,
                fontFamily: t.monoFont,
                fontSize: t.font.smPlus,
                fontWeight: isActive ? t.weight.bold : t.weight.medium,
                letterSpacing: t.tracking.tight,
                cursor: "pointer",
                transition: "background 160ms ease, box-shadow 160ms ease, color 160ms ease",
              }}
            >
              {label}
            </button>
          );
        })}
      </div>
    );
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: t.space.lg, flex: "1 1 auto", minHeight: 0 }}>
        <button
          type="button"
          onClick={closePickOptions}
          style={{
            background: "transparent",
            border: "none",
            color: t.inkDim,
            fontFamily: t.monoFont,
            fontSize: t.font.xsPlus,
            letterSpacing: t.tracking.wide,
            textTransform: "none",
            cursor: "pointer",
            padding: 0,
            alignSelf: "flex-start",
          }}
        >
          {switchTariffBackLabel}
        </button>
        <div>
          <div style={{ fontSize: t.font.lg, fontWeight: t.weight.bold, color: t.ink, letterSpacing: t.tracking.tight, fontFamily: t.monoFont }}>
            {tr.name}
          </div>
          <div style={{ fontSize: t.font.sm, color: t.inkDim, marginTop: t.space.xs }}>
            {tr.price_rub.toLocaleString("ru-RU")} ₽
            {tr.duration_days > 0 ? <span style={{ color: t.inkMute, marginLeft: t.space.sm }}>· {tr.duration_days} дн</span> : null}
          </div>
        </div>
        {devOpts.length > 0 ? (
          <div>
            <div style={sectionLabelStyle}>{addonsDevicesLabel}</div>
            {renderOptions(devOpts, pickDeviceDraft, setPickDeviceDraft, "устр.")}
          </div>
        ) : null}
        {trOpts.length > 0 ? (
          <div>
            <div style={sectionLabelStyle}>{addonsTrafficLabel}</div>
            {renderOptions(trOpts, pickTrafficDraft, setPickTrafficDraft, "ГБ")}
          </div>
        ) : null}
        <div style={{ display: "flex", gap: t.space.sm, paddingTop: t.space.sm, marginTop: "auto" }}>
          <button   type="button"
            onClick={closePickOptions}
            disabled={submitBusy}
            style={{ ...btnGhost(t), flex: 1, justifyContent: "center", cursor: submitBusy ? "wait" : "pointer", opacity: submitBusy ? 0.5 : 1 }}
          >
            {addonsCancelLabel}
          </button>
          <button   type="button"
            onClick={onPickOptionsSubmit}
            disabled={submitBusy}
            style={{
              ...btnRed(t),
              flex: 1,
              justifyContent: "center",
              cursor: submitBusy ? "default" : "pointer",
              opacity: submitBusy ? 0.5 : 1,
            }}
          >
            {submitBusy ? "..." : `${addonsSubmitLabel}${pickPriceText ? ` · ${pickPriceText}` : ""}`}
          </button>
        </div>
      </div>
    );
}
