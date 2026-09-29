"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { btnRed, btnGhost, buttonShadow, type DefaultTheme } from "./defaultTheme";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import type { AccountKeyAddonsPreviewPayload } from "@/components/constructor/types";
import { useBlockNavigate } from "@/lib/block-navigation";

type AddonsFocus = "device" | "traffic" | "both";

type AddonsScreenLabels = {
  title: string;
  hint: string;
  devicesLabel: string;
  trafficLabel: string;
  totalLabel: string;
  submitLabel: string;
  cancelLabel: string;
  unavailableText: string;
  deviceUnitFormat: string;
  deviceUnlimitedText: string;
  trafficUnitFormat: string;
  trafficUnlimitedText: string;
};

export const DEFAULT_ADDONS_LABELS: AddonsScreenLabels = {
  title: "Докупить опции",
  hint: "Доп. устройства/трафик к текущему тарифу",
  devicesLabel: "Устройства",
  trafficLabel: "Трафик",
  totalLabel: "Итого к оплате",
  submitLabel: "Оформить",
  cancelLabel: "Отмена",
  unavailableText: "Для текущего тарифа нет доступных опций",
  deviceUnitFormat: "{n} устр.",
  deviceUnlimitedText: "∞ устр.",
  trafficUnitFormat: "{n} ГБ",
  trafficUnlimitedText: "∞",
};

type Props = {
  t: DefaultTheme;
  focus: AddonsFocus;
  activeKey: { client_id: string; tariff_id?: number | null } | null;
  details: { device_limit?: number | null; traffic_limit_gb?: number | null } | null | undefined;
  deviceOptions: number[];
  trafficOptions: number[];
  checkoutSlug: string;
  labels: AddonsScreenLabels;
  onClose: () => void;
  isPreview?: boolean;
};

export function DefaultAddonsScreen({
  t, focus, activeKey, details, deviceOptions, trafficOptions, checkoutSlug, labels, onClose, isPreview = false,
}: Props) {
  const navigate = useBlockNavigate();
  const mut = useAccountMutations();

  const wantDevice = focus !== "traffic";
  const wantTraffic = focus !== "device";
  const hasDeviceOptions = wantDevice && deviceOptions.length > 0;
  const hasTrafficOptions = wantTraffic && trafficOptions.length > 0;
  const hasAnyAddonOption = hasDeviceOptions || hasTrafficOptions;

  const [deviceDraft, setDeviceDraft] = useState(() => {
    const dev = details?.device_limit ?? null;
    return dev != null && wantDevice && deviceOptions.length > 0 ? String(dev) : "";
  });
  const [trafficDraft, setTrafficDraft] = useState(() => {
    const tr = details?.traffic_limit_gb ?? null;
    return tr != null && wantTraffic && trafficOptions.length > 0 ? String(tr) : "";
  });
  const [submitBusy, setSubmitBusy] = useState(false);
  const [previewPriceRub, setPreviewPriceRub] = useState<number | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [seededFromApi, setSeededFromApi] = useState(false);

  useEffect(() => {
    if (!activeKey || isPreview) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setPreviewBusy(true);
      try {
        const devSel = deviceDraft.trim();
        const trSel = trafficDraft.trim();
        const preview = await mut.fetchAddonsPreview(activeKey.client_id, {
          force_web: true,
          include_device: hasDeviceOptions ? Boolean(devSel) : undefined,
          selected_device_limit: hasDeviceOptions && devSel ? devSel : undefined,
          include_traffic: hasTrafficOptions ? Boolean(trSel) : undefined,
          selected_traffic_gb: hasTrafficOptions && trSel ? trSel : undefined,
        }) as AccountKeyAddonsPreviewPayload;
        if (cancelled) return;
        if (!seededFromApi) {
          setSeededFromApi(true);
          const apiDevice = preview.selected_device_limit;
          const apiTraffic = preview.selected_traffic_gb;
          if (hasDeviceOptions && apiDevice != null && String(apiDevice) !== deviceDraft) {
            setDeviceDraft(String(apiDevice));
          }
          if (hasTrafficOptions && apiTraffic != null && String(apiTraffic) !== trafficDraft) {
            setTrafficDraft(String(apiTraffic));
          }
        }
        const price = Number(preview.final_price_rub ?? preview.extra_price_rub ?? 0);
        setPreviewPriceRub(Number.isFinite(price) ? Math.max(0, Math.round(price)) : 0);
      } catch {
        if (!cancelled) setPreviewPriceRub(null);
      } finally {
        if (!cancelled) setPreviewBusy(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [activeKey, isPreview, hasDeviceOptions, hasTrafficOptions, deviceDraft, trafficDraft, seededFromApi]);

  const totalText = previewBusy ? "..." : previewPriceRub != null ? `${previewPriceRub} ₽` : "—";

  const onSubmit = () => {
    if (!activeKey || submitBusy) return;
    setSubmitBusy(true);
    try {
      const params = new URLSearchParams();
      params.set("subKey", activeKey.client_id);
      params.set("flow", "addons");
      const tid = Number(activeKey.tariff_id);
      if (Number.isFinite(tid) && tid > 0) params.set("tariff_id", String(tid));
      if (hasDeviceOptions) {
        const dev = deviceDraft.trim();
        params.set("include_device", dev ? "true" : "false");
        if (dev) params.set("selected_device_limit", dev);
      }
      if (hasTrafficOptions) {
        const tr = trafficDraft.trim();
        params.set("include_traffic", tr ? "true" : "false");
        if (tr) params.set("selected_traffic_gb", tr);
      }
      navigate(`/${(checkoutSlug || "checkout").trim()}?${params.toString()}`);
    } finally {
      setSubmitBusy(false);
    }
  };

  const sectionLabelStyle: CSSProperties = {
    fontSize: t.font.xs,
    color: t.inkDim,
    letterSpacing: t.tracking.ultra,
    textTransform: "none",
    fontWeight: t.weight.bold,
    marginBottom: t.space.sm,
  };

  const renderOptions = (
    options: { value: number; label: string }[],
    selected: string,
    onChange: (v: string) => void,
  ) => (
    <div style={{ display: "grid", gridTemplateColumns: `repeat(auto-fit, minmax(110px, 1fr))`, gap: t.space.sm }}>
      {options.map((opt) => {
        const valStr = String(opt.value);
        const isActive = selected === valStr;
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
            {opt.label}
          </button>
        );
      })}
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: t.space.lg, flex: "1 1 auto", minHeight: 0 }}>
      <div>
        <div style={{ fontSize: t.font.lg, fontWeight: t.weight.bold, color: t.ink, letterSpacing: t.tracking.tight }}>{labels.title}</div>
        {labels.hint ? (
          <div style={{ fontSize: t.font.sm, color: t.inkDim, marginTop: t.space.xs, letterSpacing: t.tracking.tight }}>{labels.hint}</div>
        ) : null}
      </div>
      {hasDeviceOptions ? (
        <div>
          <div style={sectionLabelStyle}>{labels.devicesLabel}</div>
          {renderOptions(deviceOptions.map((v) => ({ value: v, label: v <= 0 ? labels.deviceUnlimitedText : labels.deviceUnitFormat.replace("{n}", String(v)) })), deviceDraft, setDeviceDraft)}
        </div>
      ) : null}
      {hasTrafficOptions ? (
        <div>
          <div style={sectionLabelStyle}>{labels.trafficLabel}</div>
          {renderOptions(trafficOptions.map((v) => ({ value: v, label: v <= 0 ? labels.trafficUnlimitedText : labels.trafficUnitFormat.replace("{n}", String(v)) })), trafficDraft, setTrafficDraft)}
        </div>
      ) : null}
      {!hasAnyAddonOption ? (
        <div style={{ fontSize: t.font.sm, color: t.error, letterSpacing: t.tracking.tight }}>{labels.unavailableText}</div>
      ) : null}
      <div
        style={{
          borderTop: `1px solid ${t.line}`,
          paddingTop: t.space.md,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          gap: t.space.md,
          marginTop: "auto",
        }}
      >
        <span style={sectionLabelStyle}>{labels.totalLabel}</span>
        <span style={{ fontSize: t.font.xl, fontWeight: t.weight.bold, color: t.accent, fontFamily: t.monoFont, letterSpacing: t.tracking.tight }}>
          {totalText}
        </span>
      </div>
      <div style={{ display: "flex", gap: t.space.sm, paddingTop: t.space.sm }}>
        <button
          type="button"
          onClick={onClose}
          disabled={submitBusy}
          style={{ ...btnGhost(t), flex: 1, justifyContent: "center", cursor: submitBusy ? "wait" : "pointer", opacity: submitBusy ? 0.5 : 1 }}
        >
          {labels.cancelLabel}
        </button>
        <button
          type="button"
          onClick={onSubmit}
          disabled={submitBusy || !hasAnyAddonOption}
          style={{
            ...btnRed(t),
            flex: 1,
            justifyContent: "center",
            cursor: submitBusy || !hasAnyAddonOption ? "default" : "pointer",
            opacity: submitBusy || !hasAnyAddonOption ? 0.5 : 1,
          }}
        >
          {submitBusy ? "..." : labels.submitLabel}
        </button>
      </div>
    </div>
  );
}
