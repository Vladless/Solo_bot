"use client";

import type { CSSProperties } from "react";
import type { CheckoutFlowContextValue } from "@/components/constructor/blockContent/checkout/flow/types";

type ConsentCheckout = Pick<CheckoutFlowContextValue, "providerId" | "requiredToPayRub" | "mode" | "autopayAmountRub" | "autopayPeriodDays" | "autopayConsent" | "setAutopayConsent" | "paying" | "paymentQuoteMessage" | "autopayQuoteMessage" | "autopayGrossAmountRub">;

/** Проверяет необходимость согласия на сохранение способа оплаты. */
export function isAutopayConsentRequired(checkout: ConsentCheckout | null | undefined): boolean {
  return checkout?.providerId === "YOOKASSA_AUTOPAY" && (checkout.requiredToPayRub ?? 0) > 0;
}

/** Показывает согласие на автопродление в оформлении набора. */
export function AutopayConsent({ checkout, style }: { checkout: ConsentCheckout | null; style?: CSSProperties }) {
  if (!checkout) return null;
  return <>
    {checkout.paymentQuoteMessage ? <div style={{ fontSize: 13, lineHeight: 1.5, ...style }}>{checkout.paymentQuoteMessage}</div> : null}
    {isAutopayConsentRequired(checkout) ? (
    <>
    {checkout.autopayQuoteMessage ? <div style={{ fontSize: 13, lineHeight: 1.5, ...style }}>{checkout.autopayQuoteMessage}</div> : null}
    <label style={{ display: "flex", alignItems: "flex-start", gap: 8, width: "100%", fontSize: 13, lineHeight: 1.5, ...style }}>
      <input
        type="checkbox"
        checked={checkout.autopayConsent}
        onChange={(event) => checkout.setAutopayConsent(event.target.checked)}
        disabled={checkout.paying}
        style={{ marginTop: 3, flexShrink: 0 }}
      />
      <span>
        {(checkout.mode === "purchase" || checkout.mode === "renew") && checkout.autopayAmountRub > 0 && checkout.autopayPeriodDays > 0
          ? `Согласен сохранить способ оплаты и включить автопродление этой подписки: до ${checkout.autopayGrossAmountRub ?? checkout.autopayAmountRub} ₽ за ${checkout.autopayPeriodDays} дн., перед истечением срока. Сначала используется баланс, недостающая сумма списывается с сохранённого способа оплаты. Автопродление можно отключить.`
          : "Согласен сохранить способ оплаты. Автопродление подписки включается отдельно; сохранение карты само по себе не разрешает списания."}
      </span>
    </label>
    </>
    ) : null}
  </>;
}
