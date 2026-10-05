"use client";

import React, { useState } from "react";
import { useInlineControls } from "./useInlineControls";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, btnSolid, DefaultPanel, f, parseBlockData, CONTROL_H, CONTROL_R } from ".";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import { isApiError } from "@/lib/api";

const PROMO_SCHEMA = {
  panelHeader: f.str("Активировать промокод"),
  panelHeaderHint: f.str(""),
  placeholder: f.str("PROMO-XXXX-XXXX"),
  submitLabel: f.str("Активировать"),
  submittingLabel: f.str("Активируем..."),
  successText: f.str("✓ Промокод применён"),
  errorText: f.str("✕ Неверный или истёкший код"),
  invalidFormatText: f.str("✕ Только латинские буквы, цифры, дефис"),
  minLength: f.num(3),
};

const PROMO_FORMAT_RE = /^[A-Z0-9_-]+$/;

export function DefaultPromoActivateBlockView({ block, context }: TypedBlockViewProps<"defaultPromoActivate">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, PROMO_SCHEMA);
  const { ref: inlineRowRef, stack: stackControls } = useInlineControls(t.space.md);

  const [code, setCode] = useState("");
  const [status, setStatus] = useState<{ kind: "idle" | "ok" | "err" | "loading"; text: string }>({ kind: "idle", text: " " });
  const mut = useAccountMutations();

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (previewMode) return;
    const trimmed = code.trim();
    if (!trimmed) return;
    if (trimmed.length < cfg.minLength || !PROMO_FORMAT_RE.test(trimmed)) {
      setStatus({ kind: "err", text: cfg.invalidFormatText });
      return;
    }
    setStatus({ kind: "loading", text: cfg.submittingLabel });
    try {
      const res = await mut.applyCoupon(trimmed);
      const delta = typeof res?.balance_delta_rub === "number" ? res.balance_delta_rub : 0;
      const base = res?.message?.trim() || cfg.successText;
      setStatus({ kind: "ok", text: delta > 0 ? `${base} +${delta} ₽` : base });
      setCode("");
    } catch (err) {
      setStatus({ kind: "err", text: isApiError(err) && err.message ? err.message : cfg.errorText });
    }
  };

  const statusColor =
    status.kind === "ok" ? t.success : status.kind === "err" ? t.error : t.inkDim;

  return wrap(
    <DefaultPanel t={t} header={cfg.panelHeader} hint={cfg.panelHeaderHint || undefined} noBodyPadding>
      <form onSubmit={onSubmit} style={{ padding: "14px 26px 26px", display: "flex", flexDirection: "column", gap: t.space.smPlus }}>
        <div
          ref={inlineRowRef}
          style={{ display: "flex", flexDirection: stackControls ? "column" : "row", gap: t.space.md, alignItems: "stretch" }}
        >
          <input
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            placeholder={cfg.placeholder}
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
            type="submit"
            disabled={status.kind === "loading"}
            style={{ ...btnSolid(t), flexShrink: 0, ...(stackControls ? { width: "100%" } : {}) }}
          >
            {status.kind === "loading" ? cfg.submittingLabel : cfg.submitLabel}
          </button>
        </div>
        <div style={{ minHeight: t.space.lg, fontSize: t.font.xs, color: statusColor }}>{status.text}</div>
      </form>
    </DefaultPanel>,
    false,
    true,
  );
}
