"use client";

import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme } from "./defaultTheme";
import { hexToRgba } from "@/components/constructor/utils";
import { DefaultPanel } from "./DefaultPanel";
import { DefaultRow, DefaultRows } from "./layout";
import { useBlockApi } from "@/components/constructor/blockContent/cabinetKit/apiHooksRegistry";
import { f, parseBlockData } from "@/components/constructor/blockContent/cabinetKit/blockSchema";
import type { CabinetReferralTierRow } from "@/components/constructor/blockData/blocks";

function buildTierSchema(mode: "referral" | "partner") {
  return {
    panelHeader: f.str(mode === "partner" ? "Условия партнёрки" : "Условия программы"),
    panelHeaderHint: f.str(""),
    rows: f.array<CabinetReferralTierRow>(),
    referralsLabel: f.str(mode === "partner" ? "Приглашено партнёром" : "Приглашено"),
    activeReferralsLabel: f.str(mode === "partner" ? "Оплатили" : "Активных"),
    bonusLabel: f.str(mode === "partner" ? "Партнёрский баланс" : "Заработано"),
    conditionsLabel: f.str("Условия"),
    showConditions: f.bool(true),
    showProgress: f.bool(true),
    showSummary: f.bool(true),
    progressTarget: f.num(20),
    tierName: f.str("Bronze"),
    nextTierName: f.str("Silver"),
    progressHeaderFormat: f.str("Прогресс до {next}"),
    toNextFormat: f.str("Ещё {n} рефералов до следующего уровня"),
    progressLineFormat: f.str("{current} / {target} рефералов · {tier} tier"),
  };
}

export function DefaultReferralTierBlockView({ block, context }: TypedBlockViewProps<"defaultReferralTier">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);

  const mode: "referral" | "partner" = d.mode === "partner" ? "partner" : "referral";
  const cfg = parseBlockData(d, buildTierSchema(mode));
  const { panelHeader, panelHeaderHint, referralsLabel, activeReferralsLabel, bonusLabel, conditionsLabel, showConditions, showProgress } = cfg;
  const fallbackRows = cfg.rows;

  const api = useBlockApi({
    needs: mode === "partner" ? ["summary", "partnersConditions"] : ["summary", "referralsConditions"],
    disabled: previewMode === true,
    mock: previewMode === true,
  });
  const conditions = mode === "partner" ? api.partnersConditions : api.referralsConditions;

  const isPreview = previewMode === true;
  const referralsTotal = isPreview
    ? 7
    : mode === "partner"
      ? (api.summary.data?.partner_referred_total ?? 0)
      : (api.summary.data?.referrals_total ?? 0);
  const referralsActive = mode === "partner"
    ? (api.summary.data?.partner_referred_paid ?? 0)
    : (api.summary.data?.referrals_active ?? 0);
  const bonus = mode === "partner"
    ? (api.summary.data?.partner_balance ?? 0)
    : (api.summary.data?.referral_bonus_total ?? 0);

  const conditionsData = conditions.conditions as Record<string, unknown> | undefined;
  const conditionsSummary = String((conditionsData?.summary as string | undefined) ?? "").trim();
  const conditionsRows: CabinetReferralTierRow[] = [];
  if (showConditions && conditionsData) {
    const bonusModeLabel = String((conditionsData.bonus_mode_label as string | undefined) ?? "").trim();
    if (bonusModeLabel) {
      conditionsRows.push({ label: "Режим начисления", value: bonusModeLabel });
    }
    const levelLines = Array.isArray(conditionsData.level_lines) ? (conditionsData.level_lines as string[]) : [];
    for (const line of levelLines) {
      const sep = line.indexOf(":");
      if (sep > 0) {
        conditionsRows.push({ label: line.slice(0, sep).trim(), value: line.slice(sep + 1).trim() });
      } else {
        conditionsRows.push({ label: "Уровень", value: line });
      }
    }
    const minPayout = Number(conditionsData.min_payout_rub ?? 0);
    if (minPayout > 0) {
      conditionsRows.push({ label: "Мин. вывод", value: `${minPayout.toLocaleString("ru-RU")} ₽` });
    }
    const payoutMethods = Array.isArray(conditionsData.payout_methods) ? (conditionsData.payout_methods as string[]) : [];
    if (payoutMethods.length > 0) {
      conditionsRows.push({ label: "Способы вывода", value: payoutMethods.join(", ") });
    }
    if (conditionsRows.length === 0 && conditionsSummary) {
      conditionsRows.push({ label: conditionsLabel, value: conditionsSummary });
    }
  }

  const summaryRows: CabinetReferralTierRow[] = showProgress || !cfg.showSummary ? [] : [
    { label: referralsLabel, value: String(referralsTotal) },
    { label: activeReferralsLabel, value: String(referralsActive) },
    { label: bonusLabel, value: `${bonus.toLocaleString("ru-RU")} ₽` },
  ];
  const apiRows: CabinetReferralTierRow[] = isPreview ? [] : [...summaryRows, ...conditionsRows];

  const rows: CabinetReferralTierRow[] = isPreview ? fallbackRows : (apiRows.length > 0 ? apiRows : fallbackRows);
  const isLoadingState = !isPreview && (api.summary.isLoading || (showConditions && conditions.conditionsLoading));
  const rowSlots = Math.max(rows.length, fallbackRows.length, showConditions ? 4 : 3);

  const target = Math.max(1, Math.floor(cfg.progressTarget));
  const current = Math.max(0, referralsTotal);
  const remaining = Math.max(0, target - current);
  const pct = Math.max(2, Math.min(100, Math.round((current / target) * 100)));
  const headerText = showProgress ? cfg.progressHeaderFormat.replace("{next}", cfg.nextTierName) : panelHeader;

  return wrap(
    <DefaultPanel
      t={t}
      header={headerText}
      hint={panelHeaderHint || undefined}
      loading={isLoadingState}
      skeleton={{ rows: rowSlots }}
      bodyGap={14}
    >
      {showProgress ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ fontSize: t.font.smPlus, color: t.inkDim }}>
            {cfg.toNextFormat.replace("{n}", String(remaining))}
          </div>
          <div style={{ height: 8, borderRadius: 999, background: hexToRgba(t.ink, 0.08), overflow: "hidden" }}>
            <div data-motion-bar="true" style={{ width: `${pct}%`, height: "100%", borderRadius: 999, background: `linear-gradient(90deg, ${hexToRgba(t.accent, 0.55)}, ${t.accent})` }} />
          </div>
          <div style={{ fontSize: t.font.xs, color: t.inkDim }}>
            {cfg.progressLineFormat.replace("{current}", String(current)).replace("{target}", String(target)).replace("{tier}", cfg.tierName)}
          </div>
        </div>
      ) : null}
      {rows.length > 0 ? (
        <div style={{ marginTop: showProgress ? 6 : 0, paddingTop: showProgress ? 14 : 0, borderTop: showProgress ? `1px solid ${t.line}` : "none" }}>
          <DefaultRows t={t} slots={rowSlots}>
            {rows.map((row, i) => (
              <DefaultRow key={i} t={t} label={row.label ?? ""} value={row.value ?? ""} mono={false} />
            ))}
          </DefaultRows>
        </div>
      ) : null}
    </DefaultPanel>,
    false,
    true,
  );
}
