"use client";

import { useState } from "react";
import { useInlineControls } from "./useInlineControls";
import { useBlockNavigate, useBlockPrefetch } from "@/lib/block-navigation";
import { slugToPath } from "@/lib/web-page-registry";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, btnSolid, DefaultPanel, f, parseBlockData, CONTROL_H, CONTROL_R } from ".";

const SCHEMA = {
  panelHeader: f.str("Пополнить баланс"),
  panelHeaderHint: f.str("Выберите или введите сумму"),
  amountPlaceholder: f.str("500"),
  submitLabel: f.str("Пополнить"),
  minAmount: f.num(50),
  checkoutSlug: f.str("checkout"),
};

export function DefaultBalanceTopUpBlockView({ block, context }: TypedBlockViewProps<"defaultBalanceTopUp">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, SCHEMA);
  const { ref: inlineRowRef, stack: stackControls } = useInlineControls(t.space.md);
  const presets: number[] = Array.isArray(d.presetAmounts) ? (d.presetAmounts as number[]) : [100, 300, 500, 1000];

  const navigate = useBlockNavigate();
  const prefetch = useBlockPrefetch();
  const [amount, setAmount] = useState("");
  const parsed = parseFloat(amount);
  const valid = !Number.isNaN(parsed) && parsed >= cfg.minAmount;

  const go = () => {
    if (!valid || previewMode) return;
    const slug = String(cfg.checkoutSlug || "checkout").trim() || "checkout";
    navigate(`${slugToPath(slug)}?flow=balance&amount=${Math.round(parsed)}`);
  };

  return wrap(
    <DefaultPanel t={t} header={cfg.panelHeader} hint={cfg.panelHeaderHint || undefined} noBodyPadding>
      <div style={{ padding: "14px 26px 26px", display: "flex", flexDirection: "column", gap: t.space.smPlus }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: t.space.sm }}>
          {presets.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setAmount(String(p))}
              style={{
                background: t.innerBg,
                border: `1px solid ${parsed === p ? t.accent : t.line}`,
                borderRadius: t.radius.sm,
                color: parsed === p ? t.accent : t.ink,
                fontFamily: t.monoFont,
                padding: "8px 16px",
                fontSize: t.font.sm,
                cursor: "pointer",
              }}
            >
              {p} ₽
            </button>
          ))}
        </div>
        <div
          ref={inlineRowRef}
          style={{ display: "flex", flexDirection: stackControls ? "column" : "row", gap: t.space.md, alignItems: "stretch" }}
        >
          <input
            type="number"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder={cfg.amountPlaceholder}
            min={cfg.minAmount}
            style={{
              flex: 1,
              minWidth: 0,
              background: t.innerBg,
              border: "none",
              borderRadius: CONTROL_R,
              color: t.ink,
              fontFamily: t.monoFont,
              padding: "0 16px",
              minHeight: t.px(CONTROL_H),
              outline: "none",
              fontSize: t.font.smPlus,
            }}
          />
          <button
            type="button"
            onClick={go}
            onPointerEnter={() => { if (!previewMode) prefetch(slugToPath(String(cfg.checkoutSlug || "checkout").trim() || "checkout")); }}
            disabled={!valid}
            style={{ ...btnSolid(t), flexShrink: 0, opacity: valid ? 1 : 0.5, ...(stackControls ? { width: "100%" } : {}) }}
          >
            {cfg.submitLabel}
          </button>
        </div>
      </div>
    </DefaultPanel>,
    false,
    true,
  );
}
