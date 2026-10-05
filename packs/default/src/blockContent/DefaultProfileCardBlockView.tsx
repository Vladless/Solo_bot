"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { formatDayMonthYear} from "@/lib/format-date";
import { mutate } from "swr";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { mergeRefs, useScreenFlip } from "@/components/constructor/blockContent/pageFlip";

const PROFILE_SCREENS = ["card", "email", "telegram"] as const;
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { useBlockApi } from "@/components/constructor/blockContent/cabinetKit/apiHooksRegistry";
import type { AccountSummary } from "@/components/constructor/types";
import { useAppInfo } from "@/app/AppInfoProvider";
import { isApiError } from "@/lib/api";
import { slugToPath } from "@/lib/web-page-registry";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import { AccountAvatar } from "@/components/constructor/blockContent/account/AccountAvatar";
import { hapticSuccess, hapticError } from "@/lib/telegram-webapp";
import { DefaultFocusArea, DefaultFooter, DefaultRow, DefaultRows, btnChip } from "./layout";
import { useDefaultTheme, panelStyle, panelBodyStyle, btnSolid, btnSecondary, pillStyle, avatarGradientStyle } from "./defaultTheme";
import { useLoadedReveal } from "@/components/constructor/blockContent/useLoadedFade";
import { f, parseBlockData } from "@/components/constructor/blockContent/cabinetKit/blockSchema";
import { pickBool } from "@/components/constructor/blockContent/cabinetKit/dataPickers";
import type { CabinetProfileField } from "@/components/constructor/blockData/blocks";
import { avatarInitials } from "@/lib/format-text";

const PROFILE_CARD_SCHEMA = {
  notLinkedText: f.str("не привязан"),
  languageFallback: f.str("Русский"),
  errEmailMissing: f.str("Укажите email"),
  errCodeMissing: f.str("Укажите email и код"),
  errLinkTelegram: f.str("Не удалось привязать Telegram"),
  okSendCode: f.str("Код подтверждения отправлен на почту"),
  okBindTelegram: f.str("Telegram успешно привязан"),
  joinedPrefix: f.str("с"),
  joinedSincePrefix: f.str("С нами"),
  daysSinceSuffix: f.str("дн"),
  verifiedBadgeText: f.str("Подтверждён"),
  loadingText: f.str("Загрузка..."),
  emptyAuthText: f.str("Войдите в аккаунт"),
  bindLabel: f.str("Привязать"),
};

type TelegramAuthPayload = {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
  auth_date: number;
  hash: string;
};


function fieldValue(
  kind: CabinetProfileField["kind"],
  summary: AccountSummary | undefined,
  staticValue: string | undefined,
  labels: { notLinkedText: string; languageFallback: string },
): string {
  if (!summary) return staticValue ?? "—";
  switch (kind) {
    case "email":
      return summary.email ?? staticValue ?? "—";
    case "telegram":
      return summary.linked_telegram && summary.tg_id ? `@${summary.tg_id}` : staticValue ?? labels.notLinkedText;
    case "identity":
      return summary.identity_id ?? staticValue ?? "—";
    case "balance":
      return `${summary.balance.toLocaleString("ru-RU")} ₽`;
    case "phone":
      return staticValue ?? "—";
    case "language":
      return staticValue ?? labels.languageFallback;
    case "static":
    default:
      return staticValue ?? "—";
  }
}

export function DefaultProfileCardBlockView({ block, context }: TypedBlockViewProps<"defaultProfileCard">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);

  const api = useBlockApi({ needs: ["summary"], disabled: previewMode === true, mock: previewMode === true });
  const summary = { data: api.summary.data, isLoading: api.summary.isLoading, isAuthenticated: api.summary.isAuthenticated };
  const data = summary.data;
  const { botUsername, auth } = useAppInfo();
  const mut = useAccountMutations();

  const cfg = parseBlockData(d, PROFILE_CARD_SCHEMA);
  const { joinedPrefix, joinedSincePrefix, daysSinceSuffix, verifiedBadgeText, loadingText, emptyAuthText, bindLabel } = cfg;
  const showAvatar = pickBool(d, "showAvatar", true);
  const showJoinedSince = pickBool(d, "showJoinedSince", true);

  const fields: CabinetProfileField[] = Array.isArray(d.fields) && (d.fields as CabinetProfileField[]).length > 0
    ? (d.fields as CabinetProfileField[])
    : [
        { kind: "email", label: "E-mail", showVerifiedBadge: true },
        { kind: "telegram", label: "Telegram" },
        { kind: "identity", label: "ID" },
      ];

  const login = data?.email?.split("@")[0] ?? (data?.tg_id ? `tg · ${data.tg_id}` : "Аккаунт");
  const avatar = avatarInitials(data?.email, login);

  const isLoading = !previewMode && summary.isLoading;
  const notAuthed = !previewMode && !summary.isAuthenticated && !summary.isLoading;

  const { revealRef, fadeClass: loadedFade } = useLoadedReveal(isLoading);
  const [bindMode, setBindMode] = useState<"email" | "telegram" | null>(null);
  const screenFlip = useScreenFlip(bindMode ?? "card", PROFILE_SCREENS);
  const [email, setEmail] = useState("");
  const [emailCode, setEmailCode] = useState("");
  const [emailCodeRequestedFor, setEmailCodeRequestedFor] = useState("");
  const [emailResendLeftSec, setEmailResendLeftSec] = useState(0);
  const [busyEmail, setBusyEmail] = useState(false);
  const [busyEmailConfirm, setBusyEmailConfirm] = useState(false);
  const [busyTelegram, setBusyTelegram] = useState(false);
  const [status, setStatus] = useState("");
  const [statusKind, setStatusKind] = useState<"neutral" | "ok" | "error">("neutral");
  const widgetRef = useRef<HTMLDivElement | null>(null);
  const callbackName = useMemo(() => `__soloBindTg_${Math.random().toString(36).slice(2)}`, []);
  const telegramClientId = auth.telegram_client_id ?? "";
  const hasTelegramOIDC = Boolean(telegramClientId);
  const canUseTelegramWidget = !previewMode && auth.telegramLoginEnabled && !!botUsername && !hasTelegramOIDC;

  useEffect(() => {
    if (emailResendLeftSec <= 0) return;
    const timer = window.setInterval(() => {
      setEmailResendLeftSec((prev) => (prev > 1 ? prev - 1 : 0));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [emailResendLeftSec]);

  useEffect(() => {
    if (bindMode !== "telegram" || !canUseTelegramWidget) return;
    const handler = async (payload: TelegramAuthPayload) => {
      if (!payload || typeof payload.id !== "number") return;
      setBusyTelegram(true);
      setStatus("");
      try {
        await mut.linkTelegram(payload as unknown as Record<string, unknown>);
        setStatus(cfg.okBindTelegram);
        setStatusKind("ok");
        await mutate((key) => Array.isArray(key) && (key[0] === "account-summary" || key[0] === "auth-me"));
        setBindMode(null);
      } catch (error) {
        const fallback = cfg.errLinkTelegram;
        const message = isApiError(error) ? error.message : fallback;
        setStatus(message || fallback);
        setStatusKind("error");
      } finally {
        setBusyTelegram(false);
      }
    };
    const w = window as unknown as Record<string, unknown>;
    w[callbackName] = handler;
    return () => { delete w[callbackName]; };
  }, [bindMode, canUseTelegramWidget, callbackName, cfg.okBindTelegram, cfg.errLinkTelegram, mut]);

  useEffect(() => {
    if (bindMode !== "telegram" || !canUseTelegramWidget || !widgetRef.current) return;
    if (widgetRef.current.querySelector("iframe")) return;
    widgetRef.current.innerHTML = "";
    const script = document.createElement("script");
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.async = true;
    script.setAttribute("data-telegram-login", String(botUsername).replace(/^@+/, ""));
    script.setAttribute("data-size", "large");
    script.setAttribute("data-userpic", "false");
    script.setAttribute("data-request-access", "write");
    script.setAttribute("data-onauth", `${callbackName}(user)`);
    widgetRef.current.appendChild(script);
  }, [bindMode, botUsername, callbackName, canUseTelegramWidget]);

  async function handleSendEmailCode() {
    const value = email.trim().toLowerCase();
    if (!value) {
      setStatus(cfg.errEmailMissing);
      setStatusKind("error");
      return;
    }
    setBusyEmail(true);
    setStatus("");
    try {
      await mut.linkEmailSendCode(value);
      setEmailCodeRequestedFor(value);
      setEmailResendLeftSec(60);
      setStatus(cfg.okSendCode);
      setStatusKind("ok");
    } catch (error) {
      const fallback = "Не удалось отправить код";
      const message = isApiError(error) ? error.message : fallback;
      setStatus(message || fallback);
      setStatusKind("error");
    } finally {
      setBusyEmail(false);
    }
  }

  async function handleConfirmEmailCode() {
    const value = email.trim().toLowerCase();
    const code = emailCode.trim();
    if (!value || !code) {
      setStatus(cfg.errCodeMissing);
      setStatusKind("error");
      return;
    }
    setBusyEmailConfirm(true);
    setStatus("");
    try {
      await mut.linkEmailConfirm({ email: value, code });
      hapticSuccess();
      setStatus(`Email ${value} успешно привязан`);
      setStatusKind("ok");
      setEmailCode("");
      setEmailResendLeftSec(0);
      await mutate((key) => Array.isArray(key) && key[0] === "account-summary");
      setBusyEmailConfirm(false);
      setTimeout(() => {
        setEmail("");
        setEmailCodeRequestedFor("");
        setBindMode(null);
      }, 1800);
      return;
    } catch (error) {
      hapticError();
      const fallback = "Не удалось подтвердить код";
      const message = isApiError(error) ? error.message : fallback;
      setStatus(message || fallback);
      setStatusKind("error");
    }
    setBusyEmailConfirm(false);
  }

  function openBind(kind: "email" | "telegram") {
    setStatus("");
    setStatusKind("neutral");
    setBindMode(kind);
  }

  function closeBind() {
    setBindMode(null);
    setStatus("");
    setStatusKind("neutral");
  }

  const inputStyle = {
    width: "100%",
    padding: "12px 14px",
    background: t.innerBg,
    border: "none",
    borderRadius: t.radius.sm,
    color: t.ink,
    fontFamily: t.monoFont,
    fontSize: t.font.smPlus,
    outline: "none",
  };

  const statusColor = statusKind === "ok" ? t.success : statusKind === "error" ? t.error : t.inkDim;

  function renderBindEmail() {
    const codeRequested = emailCodeRequestedFor && emailCodeRequestedFor === email.trim().toLowerCase();
    return (
      <>
        <div style={{ fontSize: t.font.smPlus, fontWeight: t.weight.bold, color: t.ink }}>Привязка email</div>
        <DefaultFocusArea t={t}>
        <input
          type="email"
          inputMode="email"
          autoComplete="email"
          placeholder="you@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={busyEmail || busyEmailConfirm}
          style={inputStyle}
        />
        <button
          type="button"
          onClick={handleSendEmailCode}
          disabled={busyEmail || emailResendLeftSec > 0}
          style={{ ...btnSecondary(t), color: t.accent, opacity: busyEmail || emailResendLeftSec > 0 ? 0.5 : 1, cursor: busyEmail || emailResendLeftSec > 0 ? "not-allowed" : "pointer" }}
        >
          {busyEmail ? "Отправка..." : emailResendLeftSec > 0 ? `Повтор через ${emailResendLeftSec}с` : codeRequested ? "Отправить код повторно" : "Отправить код"}
        </button>
        {codeRequested ? (
          <>
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="Код из письма"
              value={emailCode}
              onChange={(e) => setEmailCode(e.target.value.replace(/\s+/g, ""))}
              disabled={busyEmailConfirm}
              style={inputStyle}
            />
            <button
              type="button"
              onClick={handleConfirmEmailCode}
              disabled={busyEmailConfirm || !emailCode.trim()}
              style={{ ...btnSolid(t), opacity: busyEmailConfirm || !emailCode.trim() ? 0.5 : 1, cursor: busyEmailConfirm || !emailCode.trim() ? "not-allowed" : "pointer" }}
            >
              {busyEmailConfirm ? "Подтверждение..." : "Подтвердить и привязать"}
            </button>
          </>
        ) : null}
        {status ? <div style={{ fontSize: t.font.xsPlus, color: statusColor }}>{status}</div> : null}
        </DefaultFocusArea>
      </>
    );
  }

  function renderBindTelegram() {
    return (
      <>
        <div style={{ fontSize: t.font.smPlus, fontWeight: t.weight.bold, color: t.ink }}>Привязка Telegram</div>
        <DefaultFocusArea t={t}>
        {previewMode ? (
          <div style={{ fontSize: t.font.sm, color: t.inkDim }}>Превью: привязка недоступна</div>
        ) : !auth.telegramLoginEnabled ? (
          <div style={{ fontSize: t.font.sm, color: t.inkDim }}>Привязка Telegram отключена администратором</div>
        ) : hasTelegramOIDC ? (
          <button
            type="button"
            style={{ ...btnSolid(t), cursor: busyTelegram ? "wait" : "pointer" }}
            disabled={busyTelegram}
            onClick={() => {
              setBusyTelegram(true);
              void import("@/lib/telegram-oidc")
                .then(({ startTelegramOIDCLink }) => startTelegramOIDCLink(telegramClientId, slugToPath("dashboard")))
                .catch(() => {
                  setBusyTelegram(false);
                  setStatus(cfg.errLinkTelegram);
                  setStatusKind("error");
                });
            }}
          >
            {busyTelegram ? "Подключение..." : "Войти через Telegram"}
          </button>
        ) : canUseTelegramWidget ? (
          <div ref={widgetRef} style={{ display: "flex", justifyContent: "flex-start" }} />
        ) : (
          <div style={{ fontSize: t.font.sm, color: t.inkDim }}>Привязка Telegram недоступна на этом домене</div>
        )}
        {status ? <div style={{ fontSize: t.font.xsPlus, color: statusColor }}>{status}</div> : null}
        </DefaultFocusArea>
      </>
    );
  }

  const joinedLine = (() => {
    if (!showJoinedSince || !data?.created_at) return null;
    const created = new Date(data.created_at);
    if (Number.isNaN(created.getTime())) return null;
    const dateLabel = formatDayMonthYear(created);
    const daysSince = Math.max(0, Math.floor((Date.now() - created.getTime()) / (1000 * 60 * 60 * 24)));
    return `${joinedSincePrefix} ${daysSince} ${daysSinceSuffix} · ${joinedPrefix} ${dateLabel}`;
  })();

  const body = (() => {
    if (isLoading) return <div style={{ color: t.inkDim, fontSize: t.font.sm }}>{loadingText}</div>;
    if (notAuthed) return <div style={{ color: t.inkDim, fontSize: t.font.sm }}>{emptyAuthText}</div>;
    if (bindMode === "email") return renderBindEmail();
    if (bindMode === "telegram") return renderBindTelegram();
    return (
      <>
        <div style={{ display: "flex", gap: t.space.lg, alignItems: "center" }}>
          {showAvatar ? (
            <AccountAvatar enabled={!previewMode} fallback={avatar} style={{ ...avatarGradientStyle(t, 60), fontSize: t.font.lg }} />
          ) : null}
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: t.font.xxl, fontWeight: t.weight.bold, color: t.ink, lineHeight: 1.2, letterSpacing: "-0.01em" }}>{login}</div>
            {joinedLine ? (
              <div style={{ fontSize: t.font.sm, color: t.inkDim, marginTop: 4 }}>{joinedLine}</div>
            ) : null}
          </div>
        </div>
        <DefaultRows t={t}>
          {fields.map((fld, i) => {
            const value = fieldValue(fld.kind, data, fld.value, { notLinkedText: cfg.notLinkedText, languageFallback: cfg.languageFallback });
            const isVerified =
              fld.showVerifiedBadge === true &&
              ((fld.kind === "email" && Boolean(data?.email)) ||
                (fld.kind === "telegram" && Boolean(data?.linked_telegram)));
            const isEmpty = !data ? false : (fld.kind === "email" ? !data.email : fld.kind === "telegram" ? !data.linked_telegram : false);
            const canBind = isEmpty && (fld.kind === "email" || fld.kind === "telegram") && !previewMode;
            return (
              <DefaultRow
                key={i}
                t={t}
                label={fld.label ?? fld.kind ?? "—"}
                value={canBind ? undefined : value}
                aside={
                  <>
                    {canBind ? (
                      <button
                        type="button"
                        onClick={() => openBind(fld.kind as "email" | "telegram")}
                        style={btnChip(t, t.accent)}
                      >
                        {bindLabel}
                      </button>
                    ) : null}
                    {isVerified ? <span style={pillStyle(t, t.success)}>{verifiedBadgeText}</span> : null}
                  </>
                }
              />
            );
          })}
        </DefaultRows>
      </>
    );
  })();

  const backButton = bindMode ? (
    <DefaultFooter t={t}>
      <button
        type="button"
        onClick={closeBind}
        style={{ ...btnSecondary(t), width: "100%", justifyContent: "center", cursor: "pointer" }}
      >
        ← Назад
      </button>
    </DefaultFooter>
  ) : null;

  return wrap(
    <div ref={mergeRefs(revealRef, screenFlip)} className={ELEMENT_FILL_CLASS} style={{ ...panelStyle(t), ...decor }}>
      <div className={loadedFade} style={{ ...panelBodyStyle(t), display: "flex", flexDirection: "column", gap: t.space.lg, flex: "1 1 auto", minHeight: 0 }}>
        {body}
        {backButton}
      </div>
    </div>,
    false,
    true,
  );
}
