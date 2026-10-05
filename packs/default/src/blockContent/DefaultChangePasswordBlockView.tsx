"use client";

import type { CSSProperties } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, DefaultPanel, btnSolid, f, parseBlockData, useIsMobile, actionsSolo, CONTROL_H, CONTROL_R } from ".";
import { useChangePasswordForm } from "@/components/constructor/blockContent/account/useChangePasswordForm";

const SCHEMA = {
  panelHeader: f.str("Смена пароля"),
  hint: f.str(""),
  currentLabel: f.str("Текущий пароль"),
  newLabel: f.str("Новый пароль"),
  confirmLabel: f.str("Повторите новый пароль"),
  submitLabel: f.str("Сменить пароль"),
  setSubmitLabel: f.str("Установить пароль"),
  tooShortLabel: f.str("Минимум 8 символов"),
  mismatchLabel: f.str("Пароли не совпадают"),
  successLabel: f.str("Пароль изменён"),
  setSuccessLabel: f.str("Пароль установлен"),
  failureLabel: f.str("Не удалось сохранить пароль"),
};

export function DefaultChangePasswordBlockView({ block, context }: TypedBlockViewProps<"defaultChangePassword">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const isMobile = useIsMobile();
  const cfg = parseBlockData(d, SCHEMA);
  const form = useChangePasswordForm({ preview: previewMode === true });

  const inputStyle: CSSProperties = {
    width: "100%",
    boxSizing: "border-box",
    minHeight: t.px(CONTROL_H),
    padding: "0 16px",
    border: "none",
    borderRadius: CONTROL_R,
    background: t.innerBg,
    color: t.ink,
    fontFamily: t.monoFont,
    fontSize: t.font.smPlus,
    outline: "none",
  };

  const field = (label: string, value: string, onChange: (v: string) => void, autoComplete: string) => (
    <label style={{ display: "flex", flexDirection: "column", gap: t.space.xs }}>
      <span style={{ fontSize: t.font.xs, color: t.inkDim }}>{label}</span>
      <input
        type="password"
        value={value}
        autoComplete={autoComplete}
        disabled={previewMode === true || form.busy}
        onChange={(e) => onChange(e.target.value)}
        style={inputStyle}
      />
    </label>
  );

  const statusColor = form.status.kind === "ok" ? t.success : t.error;

  return wrap(
    <DefaultPanel t={t} header={cfg.panelHeader} hint={cfg.hint || undefined}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void form.submit({
            tooShort: cfg.tooShortLabel,
            mismatch: cfg.mismatchLabel,
            success: form.passwordSet ? cfg.successLabel : cfg.setSuccessLabel,
            failure: cfg.failureLabel,
          });
        }}
        style={{ display: "flex", flexDirection: "column", gap: t.space.md }}
      >
        {form.passwordSet ? field(cfg.currentLabel, form.current, form.setCurrent, "current-password") : null}
        {field(cfg.newLabel, form.next, form.setNext, "new-password")}
        {field(cfg.confirmLabel, form.confirm, form.setConfirm, "new-password")}
        {form.status.kind !== "idle" ? (
          <div style={{ fontSize: t.font.xs, color: statusColor, lineHeight: 1.5 }}>{form.status.text}</div>
        ) : null}
        <button
          type="submit"
          disabled={previewMode === true || form.busy}
          style={{
            ...btnSolid(t),
            alignSelf: "flex-start",
            cursor: form.busy ? "default" : "pointer",
            opacity: form.busy ? 0.6 : 1,
            ...actionsSolo(isMobile),
          }}
        >
          {form.passwordSet ? cfg.submitLabel : cfg.setSubmitLabel}
        </button>
      </form>
    </DefaultPanel>,
    false,
    true,
  );
}
