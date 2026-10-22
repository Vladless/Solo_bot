"use client";

import { useState } from "react";
import useSWR from "swr";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { useDefaultTheme, panelStyle, btnSolid, f, parseBlockData } from ".";
import { apiFetchPublic } from "@/lib/api";
import { activateTrial } from "@/lib/trial-activation";
import { useTrialAvailability } from "../../../_shared/trialAvailability";
import { hasAuth } from "@/lib/auth";
import { slugToPath } from "@/lib/web-page-registry";
import { formatPeriod, pickLatestTariff } from "@/components/constructor/blockContent/tariffUtils";
import type { WebTariffPublic } from "@/components/constructor/blockContent/tariff";
import { useBlockNavigate } from "@/lib/block-navigation";

const TRIAL_GROUP_CODE = "trial";

const PREVIEW_TRIAL_TARIFF: WebTariffPublic = {
  id: 999001,
  name: "Пробный доступ",
  group_code: TRIAL_GROUP_CODE,
  duration_days: 7,
  price_rub: 0,
  traffic_limit: 0,
  device_limit: 1,
  subgroup_title: null,
  sort_order: 1,
  vless: true,
  configurable: false,
};

const SCHEMA = {
  trialErrorText: f.str("Не удалось активировать пробную подписку"),
  panelHint: f.str("Пробный доступ"),
  titleText: f.str("Пробный период"),
  freeLabel: f.str("Бесплатно"),
  ctaLabel: f.str("Попробовать"),
  usedLabel: f.str("Уже использован"),
  noTrialText: f.str("Нет пробного тарифа в группе trial"),
  loadingText: f.str("Загрузка..."),
  devicesUnit: f.str("устр."),
  trafficUnit: f.str("ГБ"),
  unlimitedText: f.str("Безлимит"),
};

export function DefaultTrialCardBlockView({ block, context, editMode }: TypedBlockViewProps<"defaultTrialCard">) {
  const navigate = useBlockNavigate();
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);
  const cfg = parseBlockData(d, SCHEMA);
  const isPreview = previewMode === true || editMode === true;
  const trial = useTrialAvailability(isPreview);

  const { data: trialList, isLoading } = useSWR<WebTariffPublic[]>(
    isPreview || !trial.enabled ? null : `/api/tariffs/public?group_code=${encodeURIComponent(TRIAL_GROUP_CODE)}`,
    (url: string) => apiFetchPublic<WebTariffPublic[]>(url, { cache: "no-store" }),
    { revalidateOnFocus: false, dedupingInterval: 300_000, errorRetryCount: 1 },
  );
  const realTariff = pickLatestTariff(trialList);
  const tariff = realTariff ?? (isPreview ? PREVIEW_TRIAL_TARIFF : null);
  const loading = !isPreview && !realTariff && isLoading;

  const [busy, setBusy] = useState(false);

  const onActivate = async () => {
    if (isPreview || busy || !trial.available) return;
    if (!hasAuth()) {
      navigate(`${slugToPath("login")}?from=${encodeURIComponent(slugToPath("dashboard"))}`);
      return;
    }
    setBusy(true);
    const result = await activateTrial();
    if (result.paymentRequired && result.paymentUrl) {
      setBusy(false);
      navigate(result.paymentUrl);
      return;
    }
    if (result.ok) {
      trial.markUsed();
      if (!result.alreadyUsed) navigate(slugToPath("dashboard-keys"));
    } else {
      alert(result.error || cfg.trialErrorText);
    }
    setBusy(false);
  };

  const emptyShell = (text: string) => wrap(
    <div
      className={ELEMENT_FILL_CLASS}
      style={{ ...panelStyle(t), padding: t.space.xl, color: t.inkDim, display: "flex", alignItems: "center", justifyContent: "center", height: "100%", fontSize: t.font.sm, ...decor }}
    >
      {text}
    </div>,
    false,
    true,
  );

  if (!trial.enabled) return null;
  if (loading) return emptyShell(cfg.loadingText);
  if (!tariff) return emptyShell(cfg.noTrialText);

  const priceText = tariff.price_rub > 0 ? `${tariff.price_rub.toLocaleString("ru-RU")} ₽` : cfg.freeLabel;
  const devicesText = tariff.device_limit && tariff.device_limit > 0
    ? `${tariff.device_limit} ${cfg.devicesUnit}`
    : `∞ ${cfg.devicesUnit}`;
  const trafficText = tariff.traffic_limit && tariff.traffic_limit > 0
    ? `${tariff.traffic_limit} ${cfg.trafficUnit}`
    : cfg.unlimitedText;
  const metaText = `${devicesText} · ${trafficText}${tariff.duration_days > 0 ? ` · ${formatPeriod(tariff.duration_days)}` : ""}`;
  const ctaDisabled = busy || !trial.available;
  const ctaLabel = trial.used ? cfg.usedLabel : (busy || trial.pending ? cfg.loadingText : cfg.ctaLabel);

  return wrap(
    <div
      className={ELEMENT_FILL_CLASS}
      style={{ ...panelStyle(t), padding: "26px 26px", display: "flex", flexDirection: "column", height: "100%", gap: t.space.lg, ...decor }}
    >
      <div>
        <div style={{ fontSize: t.font.smPlus, color: t.inkDim, marginBottom: 10, textTransform: "uppercase", letterSpacing: "0.08em" }}>
          {cfg.panelHint}
        </div>
        <div style={{ fontSize: t.font.giant, fontWeight: t.weight.bold, letterSpacing: "-0.02em", lineHeight: 1.1, color: t.ink, overflow: "hidden", textOverflow: "ellipsis" }}>
          {cfg.titleText}
        </div>
        <div style={{ display: "flex", alignItems: "baseline", gap: t.space.sm, marginTop: 12, flexWrap: "wrap" }}>
          <span style={{ fontSize: t.font.xl, fontWeight: t.weight.bold, color: t.accent }}>{priceText}</span>
          <span style={{ fontSize: t.font.sm, color: t.inkDim }}>{metaText}</span>
        </div>
      </div>
      <div style={{ marginTop: "auto" }}>
        <button
          type="button"
          onClick={onActivate}
          disabled={ctaDisabled}
          style={{ ...btnSolid(t), width: "100%", cursor: ctaDisabled ? "default" : "pointer", opacity: ctaDisabled ? 0.6 : 1 }}
        >
          {ctaLabel}
        </button>
      </div>
    </div>,
    false,
    true,
  );
}
