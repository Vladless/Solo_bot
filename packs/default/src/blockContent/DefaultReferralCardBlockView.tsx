"use client";

import { useState, type CSSProperties } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { useDefaultTheme, pickContrast, blendHex, panelShadow, useIsMobile, actionsRow, actionsItem, CONTROL_R } from "./defaultTheme";
import { DefaultQrModal } from "./DefaultQrModal";
import { hexToRgba } from "@/components/constructor/utils";
import { useBlockApi } from "@/components/constructor/blockContent/cabinetKit/apiHooksRegistry";
import { useAccountReferralsConditions } from "@/components/constructor/blockContent/referrals/useAccountReferrals";
import { f, parseBlockData } from "@/components/constructor/blockContent/cabinetKit/blockSchema";

function buildRefCardSchema(mode: "referral" | "partner") {
  return {
    panelHint: f.str(mode === "partner" ? "Ваша партнёрская ссылка" : "Ваша реферальная ссылка"),
    headlineFormat: f.str("{count} друзей"),
    bonusText: f.str(""),
    rewardText: f.str(mode === "partner" ? "Вы получаете {percent}% с оплат приглашённых" : "{bonus}"),
    linkPrefix: f.str(""),
    copyLabel: f.str("Копировать"),
    copiedLabel: f.str("Скопировано"),
    shareLabel: f.str("Поделиться"),
    qrLabel: f.str("QR-код"),
    qrCloseLabel: f.str("Закрыть"),
    shareTitle: f.str(mode === "partner" ? "Партнёрская ссылка" : "Реферальная ссылка"),
    qrShareHint: f.str(
      mode === "partner"
        ? "Покажите QR другу — после его регистрации вы получите партнёрский бонус."
        : "Покажите QR другу — после его регистрации вы получите бонус."
    ),
  };
}

export function DefaultReferralCardBlockView({ block, context }: TypedBlockViewProps<"defaultReferralCard">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const isMobile = useIsMobile();
  const decor = usePanelDecor(t.panel);

  const mode: "referral" | "partner" = d.mode === "partner" ? "partner" : "referral";
  const cfg = parseBlockData(d, buildRefCardSchema(mode));
  const { panelHint, headlineFormat, bonusText, rewardText, copyLabel, copiedLabel, shareLabel, qrLabel, qrCloseLabel, shareTitle, qrShareHint } = cfg;
  const adminLinkPrefix = cfg.linkPrefix;

  const api = useBlockApi({
    needs: mode === "partner" ? ["summary", "partnersQr"] : ["summary", "referralsQr"],
    disabled: previewMode === true,
    mock: previewMode === true,
  });
  const qr = mode === "partner" ? api.partnersQr : api.referralsQr;
  const refConditions = useAccountReferralsConditions({ disabled: previewMode === true || mode !== "referral" });
  const partnerPercent = previewMode ? 40 : (api.summary.data?.partner_percent ?? 0);
  const fmtPct = (n: number) => n.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
  const bonusMode = previewMode
    ? "Бонус за каждую успешную оплату реферала"
    : (refConditions.conditions?.bonus_mode_label || "");
  const fillCond = (s: string) => s.replace("{percent}", fmtPct(partnerPercent)).replace("{bonus}", bonusMode);
  const resolvedBonus = fillCond(bonusText);
  const resolvedReward = fillCond(rewardText);
  const referralCode = (api.summary.data?.referral_code ?? "SK-9F3A").toUpperCase();
  const partnerCode = (api.summary.data?.partner_code ?? "P-DEMO").toUpperCase();
  const code = mode === "partner" ? partnerCode : referralCode;

  const count = previewMode
    ? 7
    : (mode === "partner" ? api.summary.data?.partner_referred_total : api.summary.data?.referrals_total) ?? 0;
  const headline = headlineFormat.replace("{count}", String(count));

  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const slug = mode === "partner" ? "partner" : "referral";
  const link = qr.qr?.link
    || (adminLinkPrefix ? `${adminLinkPrefix}${code}` : (origin ? `${origin}/${slug}/${code}` : `/${slug}/${code}`));

  const [copied, setCopied] = useState(false);
  const [qrOpen, setQrOpen] = useState(false);

  const onCopy = () => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(link).catch(() => { });
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  const onShare = async () => {
    if (typeof navigator !== "undefined" && (navigator as Navigator & { share?: (data: ShareData) => Promise<void> }).share) {
      try {
        await (navigator as Navigator & { share: (data: ShareData) => Promise<void> }).share({ title: shareTitle, url: link });
        return;
      } catch {
      }
    }
    onCopy();
  };

  const onAccent = pickContrast(t.accent);
  const onAccentDim = hexToRgba(onAccent, 0.78);
  const gradient = `linear-gradient(135deg, ${blendHex(t.accent, "#FFFFFF", 0.08)}, ${blendHex(t.accent, "#000000", 0.16)})`;

  const ghostBtn: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    minHeight: 36,
    padding: "0 14px",
    borderRadius: CONTROL_R,
    background: "transparent",
    color: onAccent,
    border: `1px solid ${hexToRgba(onAccent, 0.45)}`,
    fontFamily: t.monoFont,
    fontWeight: t.weight.bold,
    fontSize: t.font.xs,
    cursor: "pointer",
  };

  return wrap(
    <div
      className={ELEMENT_FILL_CLASS}
      style={{
        position: "relative",
        overflow: "hidden",
        padding: "26px 26px",
        borderRadius: t.radius.md,
        background: gradient,
        boxShadow: panelShadow(t),
        fontFamily: t.monoFont,
        color: onAccent,
        display: "flex",
        flexDirection: "column",
        height: "100%",
        ...decor,
      }}
    >
      <div aria-hidden style={{ position: "absolute", bottom: -80, right: -30, width: 220, height: 220, borderRadius: 999, background: hexToRgba(onAccent, 0.08), pointerEvents: "none" }} />
      <div style={{ position: "relative", zIndex: 1, display: "flex", flexDirection: "column", height: "100%" }}>
        <div style={{ fontSize: t.font.smPlus, color: onAccentDim, marginBottom: 10 }}>{panelHint}</div>
        <div style={{ fontSize: t.font.hero, fontWeight: t.weight.bold, letterSpacing: "-0.02em", lineHeight: 1 }}>{headline}</div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            borderRadius: CONTROL_R,
            background: hexToRgba(onAccent, 0.16),
            padding: "8px 8px 8px 16px",
            marginTop: 18,
            justifyContent: "space-between",
            gap: t.space.md,
          }}
        >
          <code className="showcase-sensitive" style={{ color: onAccent, fontFamily: t.monoFont, fontSize: t.font.sm, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{link}</code>
          <button
            onClick={onCopy}
            style={{
              display: "inline-flex",
              alignItems: "center",
              minHeight: 38,
              padding: "0 18px",
              borderRadius: CONTROL_R,
              border: "none",
              background: onAccent,
              color: t.accent,
              fontFamily: t.monoFont,
              fontWeight: t.weight.bold,
              fontSize: t.font.sm,
              cursor: "pointer",
              flexShrink: 0,
            }}
          >
            {copied ? copiedLabel : copyLabel}
          </button>
        </div>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: t.space.md, marginTop: 16, flexWrap: "wrap" }}>
          <div style={{ display: "flex", gap: t.space.lg, fontSize: t.font.sm, color: onAccentDim }}>
            {resolvedBonus ? <span>{resolvedBonus}</span> : null}
            {resolvedReward ? <span>{resolvedReward}</span> : null}
          </div>
          <div style={{ display: "flex", gap: t.space.sm, flexWrap: "wrap", ...actionsRow(isMobile) }}>
            {d.showShare !== false && shareLabel ? (
              <button onClick={onShare} style={{ ...ghostBtn, ...actionsItem(isMobile) }}>{shareLabel}</button>
            ) : null}
            {d.showQr !== false && qrLabel ? (
              <button onClick={() => setQrOpen((v) => !v)} style={{ ...ghostBtn, ...actionsItem(isMobile) }}>{qrOpen ? qrCloseLabel : qrLabel}</button>
            ) : null}
          </div>
        </div>
      </div>
      <DefaultQrModal
        t={t}
        open={qrOpen}
        onClose={() => setQrOpen(false)}
        qrImg={qr.qr?.image_data_url ?? null}
        loading={!qr.qr?.image_data_url}
        caption={qrShareHint}
        title={qrLabel}
        closeLabel={qrCloseLabel}
      />
    </div>,
    false,
    true,
  );
}
