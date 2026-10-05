"use client";

import React, { useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, btnSolid, DefaultPanel, f, parseBlockData, CONTROL_H, CONTROL_R } from ".";
import { DefaultBody, DefaultFooter } from "./layout";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import { isApiError } from "@/lib/api";

const GIFT_REDEEM_SCHEMA = {
  panelHeader: f.str("Активировать подарок"),
  panelHeaderHint: f.str(""),
  description: f.str("Введите код подарка от другого пользователя — после активации новый ключ появится в вашем кабинете."),
  placeholder: f.str("Ссылка или код подарка"),
  submitLabel: f.str("Активировать"),
  submittingLabel: f.str("Активируем..."),
  successText: f.str("✓ Подарок активирован"),
  notFoundText: f.str("✕ Код не найден или уже использован"),
  errorText: f.str("✕ Не удалось активировать"),
  invalidFormatText: f.str("✕ Только латинские буквы, цифры, дефис"),
  minLength: f.num(3),
};

const GIFT_FORMAT_RE = /^[A-Za-z0-9_-]+$/;

export function DefaultGiftRedeemBlockView({ block, context }: TypedBlockViewProps<"defaultGiftRedeem">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, GIFT_REDEEM_SCHEMA);

  const [code, setCode] = useState("");
  const [status, setStatus] = useState<{ kind: "idle" | "ok" | "err" | "loading"; text: string }>({ kind: "idle", text: " " });
  const mut = useAccountMutations();

  const onSubmit = async (e: React.SyntheticEvent) => {
    e.preventDefault();
    if (previewMode) return;
    const trimmed = code.trim();
    if (!trimmed) return;
    if (trimmed.length < cfg.minLength || !GIFT_FORMAT_RE.test(trimmed)) {
      setStatus({ kind: "err", text: cfg.invalidFormatText });
      return;
    }
    setStatus({ kind: "loading", text: cfg.submittingLabel });
    try {
      const res = await mut.redeemGift(trimmed);
      const successMsg = res?.message?.trim() || cfg.successText;
      setStatus({ kind: "ok", text: successMsg });
      setCode("");
    } catch (err) {
      if (isApiError(err) && err.status === 404) {
        setStatus({ kind: "err", text: err.message || cfg.notFoundText });
      } else if (isApiError(err) && err.status === 400) {
        setStatus({ kind: "err", text: err.message || cfg.errorText });
      } else {
        setStatus({ kind: "err", text: cfg.errorText });
      }
    }
  };

  const statusColor =
    status.kind === "ok" ? t.success : status.kind === "err" ? t.error : t.inkDim;

  return wrap(
    <DefaultPanel t={t} header={cfg.panelHeader} hint={cfg.panelHeaderHint || undefined} noBodyPadding>
      <DefaultBody t={t}>
        {cfg.description ? (
          <div style={{ fontSize: t.font.sm, color: t.inkDim, lineHeight: 1.5 }}>{cfg.description}</div>
        ) : null}
        <DefaultFooter t={t} stack>
          <input
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            placeholder={cfg.placeholder}
            onKeyDown={(e) => { if (e.key === "Enter") void onSubmit(e); }}
            style={{
              minWidth: 0,
              minHeight: t.px(CONTROL_H),
              boxSizing: "border-box",
              background: t.innerBg,
              border: "none",
              borderRadius: CONTROL_R,
              color: t.ink,
              fontFamily: t.monoFont,
              padding: "0 16px",
              outline: "none",
              fontSize: t.font.smPlus,
            }}
          />
          <button
            type="button"
            onClick={(e) => void onSubmit(e)}
            disabled={status.kind === "loading"}
            style={{ ...btnSolid(t), width: "100%", justifyContent: "center", cursor: "pointer" }}
          >
            {status.kind === "loading" ? cfg.submittingLabel : cfg.submitLabel}
          </button>
          {status.text ? (
            <div style={{ fontSize: t.font.xs, color: statusColor }}>{status.text}</div>
          ) : null}
        </DefaultFooter>
      </DefaultBody>
    </DefaultPanel>,
    false,
    true,
  );
}
