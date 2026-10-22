"use client";

import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { getCachedIdentityId } from "@/lib/api";
import { useDefaultTheme, btnSecondary, btnSm, pickContrast, DefaultPanel, f, parseBlockData, useBlockApi } from ".";
import { hexToRgba } from "@/components/constructor/utils";
import type { CabinetPaymentMethodAlt } from "@/components/constructor/blockData/blocks";
import { formatMoney } from "@/lib/format-number";

const PAYMENT_METHOD_SCHEMA = {
  panelHeader: f.str("Способ оплаты"),
  panelHeaderHint: f.str(""),
  cardMask: f.str("•••• •••• •••• ••••"),
  cardMeta: f.str(""),
  changeLabel: f.str("Сменить"),
  changeHref: f.href("/tariffs"),
  altMethodsLabel: f.str("Другие методы"),
  altMethods: f.array<CabinetPaymentMethodAlt>(),
  noPaymentsText: f.str("Платежей пока нет"),
  totalSpentLabel: f.str("Всего оплачено"),
  paymentsCountLabel: f.str("Платежей"),
};

const PROVIDER_LABEL: Record<string, string> = {
  yookassa: "ЮKassa",
  yookassa_sbp: "ЮKassa · СБП",
  cryptobot: "CryptoBot",
  cryptocloud: "CryptoCloud",
  freekassa: "Free-Kassa",
  robokassa: "Robokassa",
  stars: "Telegram Stars",
  tribute: "Tribute",
  wata: "WATA",
};

function providerLabel(p: string): string {
  const key = (p || "").toLowerCase();
  return PROVIDER_LABEL[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : "—");
}


export function DefaultPaymentMethodBlockView({ block, context }: TypedBlockViewProps<"defaultPaymentMethod">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);

  const cfg = parseBlockData(d, PAYMENT_METHOD_SCHEMA);
  const { panelHeader, panelHeaderHint, changeLabel, changeHref, altMethodsLabel, noPaymentsText, totalSpentLabel, paymentsCountLabel } = cfg;
  const fallbackCardMask = cfg.cardMask;
  const fallbackCardMeta = cfg.cardMeta;
  const fallbackAlt = cfg.altMethods;

  const identityId = previewMode ? null : getCachedIdentityId();
  const api = useBlockApi({ needs: ["payments"], disabled: previewMode === true || !identityId });

  const isPreview = previewMode === true;
  const isLoading = api.payments.isLoading;
  const payments = api.payments.data ?? [];
  const completed = payments.filter((p) => String(p.status).toLowerCase() === "completed");
  const lastPayment = completed[0] ?? null;
  const totalSpent = completed.reduce((sum, p) => sum + (Number.isFinite(p.amount) ? Number(p.amount) : 0), 0);
  const usedProviders = Array.from(new Set(completed.map((p) => providerLabel(p.provider)))).filter(Boolean);

  const cardMask = isPreview
    ? fallbackCardMask
    : lastPayment
      ? providerLabel(lastPayment.provider)
      : fallbackCardMask;
  const cardMeta = isPreview
    ? fallbackCardMeta
    : lastPayment
      ? `${formatMoney(lastPayment.amount)} ${(lastPayment.currency || "RUB").toUpperCase()} · ${(lastPayment.created_at ?? "").slice(0, 10)}`
      : "";

  const altMethods: CabinetPaymentMethodAlt[] = isPreview
    ? fallbackAlt
    : usedProviders.slice(1, 6).map((label) => ({ label }));

  return wrap(
    <DefaultPanel
      t={t}
      header={panelHeader}
      hint={panelHeaderHint || undefined}
      loading={!isPreview && isLoading}
      empty={!isPreview && !isLoading && completed.length === 0}
      emptyText={noPaymentsText}
      bodyGap={t.space.lg}
    >
      <>
            <div
              style={{
                background: t.innerBg,
                borderRadius: t.radius.sm,
                padding: "16px 18px",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: t.space.md,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: t.space.md, minWidth: 0 }}>
                <div style={{ width: 46, height: 32, borderRadius: 8, background: t.ink, position: "relative", flexShrink: 0 }}>
                  <div style={{ position: "absolute", left: 7, right: 7, top: 12, height: 4, borderRadius: 2, background: hexToRgba(pickContrast(t.ink), 0.65) }} />
                </div>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: t.font.smPlus, fontWeight: t.weight.bold, color: t.ink, fontVariantNumeric: "tabular-nums" }}>{cardMask}</div>
                  {cardMeta ? <div style={{ fontSize: t.font.xs, color: t.inkDim, marginTop: 3 }}>{cardMeta}</div> : null}
                </div>
              </div>
              <a href={changeHref} style={{ ...btnSecondary(t), ...btnSm(t), textDecoration: "none", flexShrink: 0 }}>
                {changeLabel}
              </a>
            </div>
            {!isPreview ? (
              <div style={{ display: "flex", gap: 18, fontSize: t.font.sm, color: t.inkDim }}>
                <span>{totalSpentLabel}: <b style={{ color: t.ink, fontWeight: t.weight.bold }}>{formatMoney(totalSpent)} ₽</b></span>
                <span>{paymentsCountLabel}: <b style={{ color: t.ink, fontWeight: t.weight.bold }}>{completed.length}</b></span>
              </div>
            ) : null}
            {altMethodsLabel && altMethods.length > 0 ? (
              <div style={{ fontSize: t.font.sm, color: t.inkDim }}>{altMethodsLabel}</div>
            ) : null}
            <div style={{ display: "flex", gap: t.space.sm, flexWrap: "wrap" }}>
              {altMethods.map((alt, i) => (
                <span
                  key={i}
                  style={{
                    padding: "8px 14px",
                    borderRadius: 999,
                    background: t.innerBg,
                    fontSize: t.font.xs,
                    fontWeight: t.weight.medium,
                    fontFamily: t.monoFont,
                    color: t.ink,
                  }}
                >
                  {alt.label ?? ""}
                </span>
              ))}
            </div>
      </>
    </DefaultPanel>,
    false,
    true,
  );
}
