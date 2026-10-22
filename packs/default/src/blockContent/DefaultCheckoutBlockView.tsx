"use client";

import { useState } from "react";
import { hexToRgba } from "@/components/constructor/utils";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { SurfaceEffectFrame } from "@/components/constructor/SurfaceEffectFrame";
import { buildSurfaceEffectFrameProps } from "@/components/constructor/visualBindings";
import { useCheckoutFlowContext } from "@/components/constructor/blockContent/checkout/checkoutFlowContext";
import { useDefaultTheme, panelShadow, pickContrast } from "./defaultTheme";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { useBlockNavigate } from "@/lib/block-navigation";
import { AutopayConsent, isAutopayConsentRequired } from "../../../_shared/AutopayConsent";

type PriceRow = {
  label?: string;
  value?: string;
  variant?: "base" | "strike" | "discount";
};

type Coupon = {
  code?: string;
  discountPercent?: number;
};

const DCO_CSS = `
.dco-anim > * { animation: dco-up .55s cubic-bezier(.22,.9,.28,1) both; }
.dco-anim > *:nth-child(2) { animation-delay: 70ms; }
.dco-anim > *:nth-child(3) { animation-delay: 140ms; }
.dco-anim > *:nth-child(4) { animation-delay: 210ms; }
.dco-anim > *:nth-child(5) { animation-delay: 280ms; }
.dco-anim > *:nth-child(6) { animation-delay: 350ms; }
.dco-anim > *:nth-child(7) { animation-delay: 420ms; }
@keyframes dco-up { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: none; } }
.dco-well { transition: border-color .18s, box-shadow .18s; }
.dco-well:focus-within { border-color: var(--dco-accent) !important; box-shadow: 0 0 0 3px var(--dco-accent-ring); }
.dco-pay { transition: transform .18s, box-shadow .18s, background .15s, opacity .15s; }
.dco-pay:not(:disabled):hover { transform: translateY(-2px); box-shadow: 0 12px 26px -12px var(--dco-accent-glow); }
.dco-pay:not(:disabled):active { transform: translateY(0); box-shadow: none; }
.dco-apply { transition: background-color .15s, color .15s; }
.dco-apply:not(:disabled):hover { background-color: var(--dco-accent); color: var(--dco-on-accent); }
`;

export function DefaultCheckoutBlockView({ block, context, editMode }: TypedBlockViewProps<"defaultCheckout">) {
  const { wrap } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);
  const cardRadius = `var(--block-visual-radius, ${t.radius.md}px)`;

  const accentColor = t.accent;
  const inkColor = t.ink;
  const inkDim = t.inkDim;
  const inkMute = t.inkMute;
  const lineColor = t.line;
  const lineStrong = t.lineStrong;
  const onAccent = pickContrast(accentColor);
  const successColor = t.success;
  const sansFont = t.monoFont;

  const headerLabel = typeof d.headerLabel === "string" ? d.headerLabel : "К оплате";
  const orderId = typeof d.orderId === "string" ? d.orderId : "";
  const couponPlaceholder = typeof d.couponPlaceholder === "string" ? d.couponPlaceholder : "Промокод";
  const couponApplyLabel = typeof d.couponApplyLabel === "string" ? d.couponApplyLabel : "Применить";
  const couponOkPrefix = typeof d.couponOkPrefix === "string" ? d.couponOkPrefix : "✓";
  const couponOkSuffix = typeof d.couponOkSuffix === "string" ? d.couponOkSuffix : "применён";
  const couponErrorText = typeof d.couponErrorText === "string" ? d.couponErrorText : "Код неверен";
  const couponLineLabel = typeof d.couponLineLabel === "string" ? d.couponLineLabel : "Промокод";
  const balanceLineLabel = typeof d.balanceLineLabel === "string" ? d.balanceLineLabel : "Спишется с баланса";
  const totalLabel = typeof d.totalLabel === "string" ? d.totalLabel : "Итого";
  const totalMeta = typeof d.totalMeta === "string" ? d.totalMeta : "оплата единоразовая";
  const currency = typeof d.currency === "string" ? d.currency : "₽";
  const showSubject = d.showSubject !== false;
  const subjectPeriodLabel = typeof d.subjectPeriodLabel === "string" ? d.subjectPeriodLabel : "Период";
  const subjectDevicesLabel = typeof d.subjectDevicesLabel === "string" ? d.subjectDevicesLabel : "Устройства";
  const subjectTrafficLabel = typeof d.subjectTrafficLabel === "string" ? d.subjectTrafficLabel : "Трафик";
  const subjectPreviewMode = typeof d.subjectPreviewMode === "string" ? d.subjectPreviewMode : "Покупка";
  const subjectPreviewTariff = typeof d.subjectPreviewTariff === "string" ? d.subjectPreviewTariff : "PRIME";
  const checkout = useCheckoutFlowContext();
  const previewMode = context.previewMode === true;
  const live = checkout?.available === true && !editMode && !previewMode;
  const showRenewDevicePicker = live && checkout!.mode === "renew" && checkout!.hasDeviceOption;
  const renewDeviceDraft = checkout?.deviceDraft ?? "";
  const renewDeviceOptions = checkout?.deviceOptions ?? [];
  const hasCurrentDeviceOption = renewDeviceOptions.some((option) => String(option.value) === renewDeviceDraft);
  const subjectKind = live ? checkout!.modeTitle : subjectPreviewMode;
  const subjectTariff = live ? (checkout!.planSummary.tariff ?? "") : subjectPreviewTariff;
  const subjectRows = (
    live
      ? [
          { label: subjectPeriodLabel, value: checkout!.planSummary.period },
          { label: subjectDevicesLabel, value: checkout!.planSummary.devices },
          { label: subjectTrafficLabel, value: checkout!.planSummary.traffic },
        ]
      : [
          { label: subjectPeriodLabel, value: "1 мес" },
          { label: subjectDevicesLabel, value: "3" },
          { label: subjectTrafficLabel, value: "100 ГБ" },
        ]
  ).filter((row): row is { label: string; value: string } => typeof row.value === "string" && row.value.trim() !== "");


  const baseAmount = live ? Math.round(checkout!.basePriceRub) : (typeof d.baseAmount === "number" ? d.baseAmount : 2988);
  const liveFinalAmount = live ? Math.round(checkout!.finalPriceRub) : baseAmount;
  const liveRequiredAmount = live ? Math.round(checkout!.requiredToPayRub) : liveFinalAmount;
  const liveBalanceApplied = live ? Math.round(checkout!.balanceAppliedRub) : 0;
  const liveDiscountAmount = live ? Math.round(checkout!.discountRub) : 0;
  const payLabel = typeof d.payLabel === "string" ? d.payLabel : "Оплатить";
  const termsText = typeof d.termsText === "string" ? d.termsText : "Нажимая «Оплатить», вы принимаете условия покупки и политику.";
  const secureLeft = typeof d.secureLeft === "string" ? d.secureLeft : "Защищённый платёж";
  const secureRight = typeof d.secureRight === "string" ? d.secureRight : "Visa · MC · МИР · USDT";
  const successRedirect = typeof d.successRedirect === "string" && d.successRedirect.startsWith("/") && !d.successRedirect.startsWith("//")
    ? d.successRedirect
    : "/dashboard";

  const fmt = (n: number) => n.toLocaleString("ru-RU").replace(/,/g, " ");
  const fallbackPriceRows: PriceRow[] = Array.isArray(d.priceRows) && (d.priceRows as PriceRow[]).length > 0
    ? (d.priceRows as PriceRow[])
    : [
      { label: "Цена за месяц", value: "399 ₽", variant: "strike" },
      { label: "Цена · 12 мес", value: "2 988 ₽", variant: "base" },
    ];
  const livePriceRows: PriceRow[] = live
    ? [
        ...(baseAmount > 0 && baseAmount !== liveFinalAmount
          ? [{ label: "Цена", value: `${fmt(baseAmount)} ${currency}`, variant: "strike" as const }]
          : []),
        { label: liveFinalAmount !== liveRequiredAmount ? "Стоимость" : "К оплате", value: checkout!.loading ? "…" : `${fmt(liveFinalAmount)} ${currency}`, variant: "base" as const },
      ]
    : [];
  const priceRows: PriceRow[] = live ? livePriceRows : fallbackPriceRows;

  const coupons: Coupon[] = Array.isArray(d.coupons) ? (d.coupons as Coupon[]) : [
    { code: "SOLO20", discountPercent: 20 },
    { code: "FRIEND15", discountPercent: 15 },
    { code: "WELCOME", discountPercent: 10 },
  ];

  const [couponInputLocal, setCouponInputLocal] = useState("");
  const [appliedCouponLocal, setAppliedCouponLocal] = useState<Coupon | null>(null);
  const [couponStatusLocal, setCouponStatusLocal] = useState<{ kind: "ok" | "err" | "idle"; text: string }>({ kind: "idle", text: " " });

  const couponInput = live ? checkout!.couponCode : couponInputLocal;
  const setCouponInput: (v: string) => void = live ? checkout!.setCouponCode : setCouponInputLocal;

  const liveAppliedCode = live ? checkout!.appliedCouponCode : null;
  const couponStatus = live
    ? (checkout!.error
        ? { kind: "err" as const, text: couponErrorText }
        : liveAppliedCode
          ? { kind: "ok" as const, text: `${couponOkPrefix} ${liveAppliedCode} ${couponOkSuffix}${liveDiscountAmount > 0 ? ` -${fmt(liveDiscountAmount)} ${currency}` : ""}` }
          : { kind: "idle" as const, text: " " })
    : couponStatusLocal;

  const discountAmount = live
    ? liveDiscountAmount
    : (appliedCouponLocal && typeof appliedCouponLocal.discountPercent === "number"
        ? Math.round(baseAmount * appliedCouponLocal.discountPercent / 100)
        : 0);
  const totalAmount = live ? (checkout!.paymentGrossAmountRub ?? liveRequiredAmount) : (baseAmount - discountAmount);
  const appliedCoupon: Coupon | null = live
    ? (liveAppliedCode ? { code: liveAppliedCode } : null)
    : appliedCouponLocal;

  const handleApplyCoupon = () => {
    if (editMode) return;
    if (live) {
      checkout!.applyCoupon();
      return;
    }
    const code = couponInputLocal.trim().toUpperCase();
    if (!code) {
      setCouponStatusLocal({ kind: "idle", text: " " });
      return;
    }
    const found = coupons.find((c) => typeof c.code === "string" && c.code.toUpperCase() === code);
    if (found && typeof found.discountPercent === "number") {
      setAppliedCouponLocal(found);
      setCouponStatusLocal({ kind: "ok", text: `${couponOkPrefix} ${found.code} ${couponOkSuffix} -${found.discountPercent}%` });
      setCouponInputLocal("");
    } else {
      setCouponStatusLocal({ kind: "err", text: couponErrorText });
    }
  };

  const navigate = useBlockNavigate();

  const handlePay = () => {
    if (editMode) return;
    if (live) {
      void checkout!.pay();
      return;
    }
    if (successRedirect) navigate(successRedirect);
  };

  const autopayConsentRequired = live && isAutopayConsentRequired(checkout);
  const payButtonDisabled = editMode || (live && (checkout!.paying || checkout!.loading || checkout!.requiresTariffSelection || (autopayConsentRequired && !checkout!.autopayConsent)));

  const node = (
    <div
      className={`${ELEMENT_FILL_CLASS} overflow-y-auto`}
      style={{
        borderRadius: cardRadius,
        background: t.panel,
        boxShadow: panelShadow(t),
        padding: "26px 28px",
        position: "relative",
        display: "flex",
        flexDirection: "column",
        fontFamily: sansFont,
        color: inkColor,
        boxSizing: "border-box",
        ["--dco-accent" as never]: accentColor,
        ["--dco-accent-ring" as never]: hexToRgba(accentColor, 0.14),
        ["--dco-accent-glow" as never]: hexToRgba(accentColor, 0.32),
        ["--dco-on-accent" as never]: onAccent,
        ...decor,
      }}
    >
      <style>{DCO_CSS}</style>
      <div className="dco-anim" style={{ display: "flex", flexDirection: "column", gap: 16, marginTop: "auto", marginBottom: "auto" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 18, fontWeight: 700, letterSpacing: "-0.01em", color: inkColor }}>{headerLabel}</span>
          {orderId ? (
            <span style={{ fontSize: 11, fontWeight: 600, color: inkMute, background: t.innerBg, border: `1px solid ${lineColor}`, borderRadius: 999, padding: "5px 10px" }}>{orderId}</span>
          ) : null}
        </div>

        {showSubject ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  letterSpacing: "0.04em",
                  textTransform: "uppercase",
                  color: accentColor,
                  background: hexToRgba(accentColor, 0.1),
                  borderRadius: 999,
                  padding: "5px 10px",
                }}
              >
                {subjectKind}
              </span>
              {subjectTariff ? (
                <span style={{ fontSize: 15, fontWeight: 700, color: inkColor, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {subjectTariff}
                </span>
              ) : null}
            </div>
            {subjectRows.length > 0 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {subjectRows.map((row) => (
                  <div key={row.label} style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 10 }}>
                    <span style={{ fontSize: 13, color: inkDim }}>{row.label}</span>
                    <span style={{ fontSize: 13, fontWeight: 600, color: inkColor, textAlign: "right" }}>{row.value}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}

        {showRenewDevicePicker ? (
          <label style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <span style={{ fontSize: 13, color: inkDim }}>{subjectDevicesLabel}</span>
            <select
              value={renewDeviceDraft}
              onChange={(event) => checkout!.setDeviceDraft(event.target.value)}
              disabled={checkout!.paying}
              className="dco-well"
              style={{
                width: "100%",
                border: `1px solid ${lineColor}`,
                borderRadius: 14,
                background: t.innerBg,
                color: inkColor,
                fontFamily: sansFont,
                fontSize: 14,
                fontWeight: 500,
                padding: "12px 16px",
                outline: "none",
              }}
            >
              {!hasCurrentDeviceOption ? (
                <option value={renewDeviceDraft} disabled>
                  {renewDeviceDraft ? `${renewDeviceDraft === "0" ? "Без лимита" : renewDeviceDraft} (текущее)` : "Текущее количество"}
                </option>
              ) : null}
              {renewDeviceOptions.map((option) => (
                <option key={option.value} value={String(option.value)}>{option.label}</option>
              ))}
            </select>
          </label>
        ) : null}

        {live && checkout!.mode === "renew" && !checkout!.loading && checkout!.modeDetails.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12, color: inkDim, lineHeight: 1.5 }}>
            {checkout!.modeDetails.map((line, index) => <div key={index}>{line}</div>)}
          </div>
        ) : null}

        <div>
          <div
            className="dco-well"
            style={{
              display: "flex",
              alignItems: "center",
              border: `1px solid ${lineColor}`,
              borderRadius: 14,
              background: t.innerBg,
              padding: "4px 4px 4px 16px",
            }}
          >
            <input
              type="text"
              value={couponInput}
              onChange={(e) => setCouponInput(e.target.value)}
              placeholder={couponPlaceholder}
              maxLength={20}
              disabled={editMode}
              onKeyDown={(e) => { if (e.key === "Enter") handleApplyCoupon(); }}
              style={{
                flex: 1,
                background: "transparent",
                border: "none",
                outline: "none",
                fontFamily: sansFont,
                fontSize: 14,
                color: inkColor,
                fontWeight: 500,
                minWidth: 0,
                height: 36,
              }}
            />
            <button
              type="button"
              onClick={handleApplyCoupon}
              disabled={editMode}
              className="dco-apply"
              style={{
                border: "none",
                borderRadius: 11,
                background: hexToRgba(accentColor, 0.1),
                padding: "0 16px",
                height: 36,
                color: accentColor,
                cursor: editMode ? "default" : "pointer",
                fontFamily: sansFont,
                fontSize: 13,
                fontWeight: 600,
                flexShrink: 0,
              }}
            >
              {couponApplyLabel}
            </button>
          </div>
          <div style={{
            fontSize: 12,
            fontWeight: 500,
            color: couponStatus.kind === "ok" ? successColor : couponStatus.kind === "err" ? t.error : inkMute,
            height: 16,
            marginTop: 4,
          }}>{couponStatus.text}</div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", borderTop: `1px solid ${lineColor}`, paddingTop: 6 }}>
          {priceRows.map((row, i) => {
            const variant = row.variant ?? "base";
            return (
              <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "7px 0", fontSize: 14 }}>
                <span style={{ color: inkDim }}>{row.label ?? ""}</span>
                <span style={{
                  color: variant === "strike" ? inkMute : variant === "discount" ? accentColor : inkColor,
                  textDecoration: variant === "strike" ? "line-through" : "none",
                  fontWeight: 600,
                }}>{row.value ?? ""}</span>
              </div>
            );
          })}
          {appliedCoupon ? (
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "7px 0", fontSize: 14, animation: "dco-up .3s ease-out" }}>
              <span style={{ color: inkDim }}>{couponLineLabel} · {appliedCoupon.code}</span>
              <span style={{ color: successColor, fontWeight: 600 }}>-{fmt(discountAmount)} {currency}</span>
            </div>
          ) : null}
          {live && liveBalanceApplied > 0 ? (
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "7px 0", fontSize: 14 }}>
              <span style={{ color: inkDim }}>{balanceLineLabel}</span>
              <span style={{ color: successColor, fontWeight: 600 }}>-{fmt(liveBalanceApplied)} {currency}</span>
            </div>
          ) : null}
        </div>

        <div style={{
          paddingTop: 14,
          borderTop: `1px solid ${lineStrong}`,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-end",
          gap: 12,
        }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: inkDim }}>{totalLabel}</div>
            {totalMeta && !autopayConsentRequired ? <div style={{ fontSize: 12, color: inkMute, marginTop: 3 }}>{totalMeta}</div> : null}
          </div>
          <div style={{ fontWeight: 700, fontSize: 34, letterSpacing: "-0.02em", lineHeight: 1 }}>
            <span>{live && checkout!.loading ? "…" : fmt(totalAmount)}</span>
            <span style={{ fontSize: 18, color: inkDim, fontWeight: 600, marginLeft: 5 }}>{currency}</span>
          </div>
        </div>

        <AutopayConsent checkout={live ? checkout : null} style={{ color: inkDim }} />

        <button
          type="button"
          disabled={payButtonDisabled}
          onClick={handlePay}
          className="dco-pay"
          style={{
            width: "100%",
            height: 52,
            background: accentColor,
            border: "none",
            borderRadius: 14,
            cursor: payButtonDisabled ? "default" : "pointer",
            color: onAccent,
            fontFamily: sansFont,
            fontSize: 15,
            fontWeight: 700,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 12,
            opacity: payButtonDisabled ? 0.7 : 1,
            flexShrink: 0,
          }}
        >
          <span>{live && checkout!.paying ? "…" : payLabel}</span>
          <span aria-hidden>→</span>
        </button>

        {live && checkout!.error ? (
          <div style={{ fontSize: 13, fontWeight: 500, color: t.error, padding: "10px 14px", borderRadius: 12, background: hexToRgba(t.error, 0.1) }}>{checkout!.error}</div>
        ) : null}
        {live && checkout!.status && !checkout!.error ? (
          <div style={{ fontSize: 13, fontWeight: 500, color: successColor, padding: "10px 14px", borderRadius: 12, background: hexToRgba(successColor, 0.1) }}>{checkout!.status}</div>
        ) : null}

        {termsText ? (
          <div
            style={{
              fontSize: 12,
              color: inkMute,
              lineHeight: 1.55,
              display: "-webkit-box",
              WebkitBoxOrient: "vertical",
              WebkitLineClamp: 2,
              overflow: "hidden",
            }}
          >
            {termsText}
          </div>
        ) : null}

        {(secureLeft || secureRight) ? (
          <div style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 10,
            fontSize: 12,
            fontWeight: 500,
            color: inkMute,
            borderTop: `1px solid ${lineColor}`,
            paddingTop: 12,
          }}>
            {secureLeft ? (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 3l7 3v5c0 4.6-3 8.6-7 10-4-1.4-7-5.4-7-10V6l7-3z" />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9.5 12l1.8 1.8 3.2-3.6" />
                </svg>
                {secureLeft}
              </span>
            ) : <span />}
            {secureRight ? <span>{secureRight}</span> : <span />}
          </div>
        ) : null}
      </div>
    </div>
  );

  const surfaceFrameProps = {
    ...buildSurfaceEffectFrameProps(context, "surface", "transparent", {
      radiusClass: "",
      radiusValue: cardRadius,
    }),
    surfaceStyle: undefined,
  };
  return wrap(
    <SurfaceEffectFrame {...surfaceFrameProps} overflowVisible targetStyle={{ borderRadius: cardRadius }}>
      {node}
    </SurfaceEffectFrame>,
    false,
    true,
  );
}
