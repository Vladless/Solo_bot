"use client";

import { useState, type ReactNode } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, btnSm, btnSecondary, CONTROL_R } from "./defaultTheme";
import { DefaultPanel } from "./DefaultPanel";
import { f, parseBlockData } from "@/components/constructor/blockContent/cabinetKit/blockSchema";
import { useBlockApi } from "@/components/constructor/blockContent/cabinetKit/apiHooksRegistry";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import { isApiError } from "@/lib/api";

const SECURITY_PANEL_SCHEMA = {
  panelHeader: f.str("Безопасность"),
  panelHeaderHint: f.str(""),
  sessionsLabel: f.str("Активные сессии"),
  sessionsDesc: f.str("Сколько устройств вошли в аккаунт прямо сейчас."),
  revokeOthersLabel: f.str("Завершить другие"),
  revokeBusyLabel: f.str("Завершаем..."),
  revokeOnlyLabel: f.str("Только это устройство"),
  revokeOkText: f.str("Другие сессии завершены"),
  revokeErrorText: f.str("Не удалось завершить сессии"),
  passwordLabel: f.str("Пароль"),
  passwordSetDesc: f.str("Установлен. Можно сменить в любой момент."),
  passwordEmptyDesc: f.str("Не установлен. Войти можно только через почту или Telegram."),
  changePasswordLabel: f.str("Сменить"),
  setPasswordLabel: f.str("Установить"),
  passwordHref: f.href("/forgot-password"),
  bindingsLabel: f.str("Привязки"),
  bindingsDescFmt: f.str("Почта: {email} · Telegram: {tg}"),
  bindingsManageLabel: f.str("Управлять"),
  bindingsHref: f.href("#bind-account"),
  presentLabel: f.str("✓"),
  absentLabel: f.str("—"),
};

export function DefaultSecurityPanelBlockView({ block, context }: TypedBlockViewProps<"defaultSecurityPanel">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, SECURITY_PANEL_SCHEMA);
  const isPreview = previewMode === true;

  const api = useBlockApi({ needs: ["summary", "sessions"], disabled: isPreview, mock: isPreview });
  const mut = useAccountMutations();
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const sessionsCount = isPreview ? 3 : (api.sessions.data?.length ?? 0);
  const otherSessions = isPreview ? 2 : Math.max(0, sessionsCount - 1);
  const summary = api.summary.data as Record<string, unknown> | undefined;
  const passwordSet = isPreview ? true : Boolean(summary?.password_set);
  const emailLinked = isPreview ? true : Boolean(api.summary.data?.email);
  const tgLinked = isPreview ? true : Boolean(api.summary.data?.linked_telegram);
  const bindingsDesc = cfg.bindingsDescFmt
    .replace("{email}", emailLinked ? cfg.presentLabel : cfg.absentLabel)
    .replace("{tg}", tgLinked ? cfg.presentLabel : cfg.absentLabel);

  const revokeOthers = async () => {
    if (busy || isPreview) return;
    setBusy(true);
    setStatus(null);
    try {
      await mut.revokeOtherSessions();
      setStatus({ kind: "ok", text: cfg.revokeOkText });
    } catch (error) {
      const message = isApiError(error) ? error.message : cfg.revokeErrorText;
      setStatus({ kind: "error", text: message || cfg.revokeErrorText });
    } finally {
      setBusy(false);
    }
  };

  const linkButton = (href: string, label: string) => (
    <a
      href={href}
      onClick={(e) => {
        if (!href.startsWith("#")) return;
        e.preventDefault();
        const el = typeof document !== "undefined" ? document.getElementById(href.slice(1)) : null;
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
      }}
      style={{ ...btnSecondary(t), ...btnSm(t), borderRadius: CONTROL_R, textDecoration: "none", flexShrink: 0 }}
    >
      {label}
    </a>
  );

  const rows: Array<{ key: string; name: string; desc: string; right: ReactNode }> = [
    {
      key: "sessions",
      name: cfg.sessionsLabel,
      desc: cfg.sessionsDesc,
      right: (
        <span style={{ display: "inline-flex", alignItems: "center", gap: t.space.md, flexShrink: 0 }}>
          <span style={{ fontSize: t.font.md, fontWeight: t.weight.bold, color: t.ink, fontVariantNumeric: "tabular-nums" }}>
            {sessionsCount}
          </span>
          {otherSessions > 0 ? (
            <button
              type="button"
              onClick={() => void revokeOthers()}
              disabled={busy || isPreview}
              style={{
                ...btnSecondary(t),
                ...btnSm(t),
                borderRadius: CONTROL_R,
                color: t.error,
                cursor: busy || isPreview ? "default" : "pointer",
                opacity: busy ? 0.6 : 1,
              }}
            >
              {busy ? cfg.revokeBusyLabel : cfg.revokeOthersLabel}
            </button>
          ) : (
            <span style={{ fontSize: t.font.xs, color: t.inkMute }}>{cfg.revokeOnlyLabel}</span>
          )}
        </span>
      ),
    },
    {
      key: "password",
      name: cfg.passwordLabel,
      desc: passwordSet ? cfg.passwordSetDesc : cfg.passwordEmptyDesc,
      right: linkButton(cfg.passwordHref, passwordSet ? cfg.changePasswordLabel : cfg.setPasswordLabel),
    },
    {
      key: "bindings",
      name: cfg.bindingsLabel,
      desc: bindingsDesc,
      right: linkButton(cfg.bindingsHref, cfg.bindingsManageLabel),
    },
  ];

  return wrap(
    <DefaultPanel
      t={t}
      header={cfg.panelHeader}
      hint={cfg.panelHeaderHint || undefined}
      loading={!isPreview && (api.summary.isLoading || api.sessions.isLoading)}
      noBodyPadding
    >
      <div>
        {rows.map((row, i) => (
          <div
            key={row.key}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: t.space.lg,
              padding: `${t.px(14)}px ${t.px(26)}px`,
              borderBottom: i < rows.length - 1 ? `1px solid ${t.line}` : "none",
            }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
              <div style={{ fontSize: t.font.smPlus, fontWeight: t.weight.medium, color: t.ink }}>{row.name}</div>
              <div style={{ fontSize: t.font.xs, color: t.inkMute, lineHeight: 1.45 }}>{row.desc}</div>
            </div>
            {row.right}
          </div>
        ))}
        {status ? (
          <div
            style={{
              padding: `${t.px(10)}px ${t.px(26)}px`,
              fontSize: t.font.xs,
              color: status.kind === "ok" ? t.success : t.error,
            }}
          >
            {status.text}
          </div>
        ) : null}
      </div>
    </DefaultPanel>,
    false,
    true,
  );
}
