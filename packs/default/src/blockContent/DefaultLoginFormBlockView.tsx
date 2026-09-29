"use client";

import React, { useState, useEffect, useMemo, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { mutate as swrMutate } from "swr";
import { apiFetchPublic, isApiError } from "@/lib/api";
import { useSiteInitState } from "@/lib/useSiteInitState";
import { setIdentityId } from "@/lib/auth";
import { applyPendingPostLoginActions } from "@/lib/pendingPostLogin";
import { useFlowPage } from "@/lib/flow-page-provider";
import { useAppInfo } from "@/app/AppInfoProvider";
import type { IdentityMe } from "@/components/constructor";
import { slugToPath } from "@/lib/web-page-registry";
import { useTelegramLoginWidget } from "@/components/constructor/blockContent/useTelegramLoginWidget";
import { GOOGLE_AUTHORIZE_PATH, YANDEX_AUTHORIZE_PATH, oauthButtonVisible, startOAuthLogin, useOAuthLoginAvailability } from "@/components/constructor/blockContent/oauthLogin";
import { useDefaultTheme, panelShadow, btnSolid } from "./defaultTheme";
import { createPortal } from "react-dom";
import { PENDING_GIFT_CODE_KEY, PENDING_PARTNER_CODE_KEY, PENDING_REFERRAL_CODE_KEY } from "@/lib/inviteStorage";
import { hexToRgba } from "@/components/constructor/utils";
import { Turnstile, isTurnstileEnabled } from "@/components/Turnstile";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { SurfaceEffectFrame } from "@/components/constructor/SurfaceEffectFrame";
import { buildSurfaceEffectFrameProps } from "@/components/constructor/visualBindings";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { pickContrastInk } from "@/lib/visual-settings/color-math";


type Mode = "password" | "code";

const FORM_CSS = `
.dlf-anim > * { animation: dlf-up .55s cubic-bezier(.22,.9,.28,1) both; }
.dlf-anim > *:nth-child(2) { animation-delay: 70ms; }
.dlf-anim > *:nth-child(3) { animation-delay: 140ms; }
.dlf-anim > *:nth-child(4) { animation-delay: 210ms; }
.dlf-anim > *:nth-child(5) { animation-delay: 280ms; }
.dlf-anim > *:nth-child(6) { animation-delay: 350ms; }
.dlf-anim > *:nth-child(7) { animation-delay: 420ms; }
@keyframes dlf-up { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: none; } }
.dlf-tab-ind { transition: transform .24s cubic-bezier(.22,.9,.28,1); }
.dlf-field { transition: border-color .18s, box-shadow .18s; }
.dlf-field:focus-within { border-color: var(--dlf-accent) !important; box-shadow: 0 0 0 3px var(--dlf-accent-ring); }
.dlf-submit { transition: transform .18s, box-shadow .18s, background .15s, opacity .15s; }
.dlf-submit:not(:disabled):hover { transform: translateY(-2px); box-shadow: 0 12px 26px -12px var(--dlf-accent-glow); }
.dlf-submit:not(:disabled):active { transform: translateY(0); box-shadow: none; }
.dlf-oauth { transition: background-color .15s, color .15s; }
.dlf-oauth:not(:disabled):hover { background-color: var(--dlf-hover-bg); }
`;

export function DefaultLoginFormBlockView({ block, context, editMode }: TypedBlockViewProps<"defaultLoginForm">) {
  const { wrap } = context;
  const previewMode = context.previewMode === true;
  const d = block.data as Record<string, unknown>;
  const router = useRouter();
  const flowPage = useFlowPage();
  const searchParams = useSearchParams();
  const { botUsername, auth } = useAppInfo();
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);
  const cardRadius = `var(--block-visual-radius, ${t.radius.md}px)`;

  const showPasswordLogin = d.showPasswordLogin !== false;
  const showCodeLogin = d.showCodeLogin !== false;
  const showTelegramLogin = d.showTelegramLogin !== false;
  const oauthAvailable = useOAuthLoginAvailability();
  const googleHref = typeof d.googleHref === "string" && d.googleHref.trim() !== ""
    ? d.googleHref.trim()
    : GOOGLE_AUTHORIZE_PATH;
  const yandexHref = typeof d.yandexHref === "string" && d.yandexHref.trim() !== ""
    ? d.yandexHref.trim()
    : YANDEX_AUTHORIZE_PATH;
  const showGoogleLogin = d.showGoogleLogin !== false
    && oauthButtonVisible(googleHref, GOOGLE_AUTHORIZE_PATH, oauthAvailable.google, editMode === true);
  const showYandexLogin = d.showYandexLogin !== false
    && oauthButtonVisible(yandexHref, YANDEX_AUTHORIZE_PATH, oauthAvailable.yandex, editMode === true);

  const { data: siteInitData } = useSiteInitState();
  const firstInstall = siteInitData?.initialized === false;

  const initialMode: Mode = useMemo(() => {
    if (firstInstall && showPasswordLogin) return "password";
    if (showCodeLogin) return "code";
    if (showPasswordLogin) return "password";
    return "code";
  }, [firstInstall, showCodeLogin, showPasswordLogin]);

  const [mode, setMode] = useState<Mode>(initialMode);
  useEffect(() => {
    if (firstInstall && showPasswordLogin && mode !== "password") setMode("password");
  }, [firstInstall, showPasswordLogin, mode]);
  useEffect(() => {
    if (mode === "password" && !showPasswordLogin && showCodeLogin) setMode("code");
    if (mode === "code" && !showCodeLogin && showPasswordLogin) setMode("password");
  }, [mode, showPasswordLogin, showCodeLogin]);

  const showTabs = showPasswordLogin && showCodeLogin;
  const hasEmailForm = showPasswordLogin || showCodeLogin;
  const hasOAuth = showTelegramLogin || showGoogleLogin || showYandexLogin;

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);

  const telegramClientId = auth.telegram_client_id ?? "";
  const hasTelegramOIDC = Boolean(telegramClientId);
  const telegramLoginEnabled = !previewMode && showTelegramLogin && auth.telegramLoginEnabled && !!botUsername;
  const showTelegramButton = showTelegramLogin && (telegramLoginEnabled || editMode);

  const sansFont = (typeof d.sansFontFamily === "string" && d.sansFontFamily.trim())
    ? d.sansFontFamily.trim()
    : t.monoFont;
  const accentColor = t.accent;
  const inkColor = t.ink;
  const inkDim = t.inkDim;
  const inkMute = t.inkMute;
  const lineColor = t.line;
  const onAccentText = pickContrastInk(accentColor);
  const successBg = t.success;
  const onSuccessText = pickContrastInk(successBg);

  const title = typeof d.title === "string" ? d.title : "Вход";

  const errEmailRequired = typeof d.errEmailRequired === "string" ? d.errEmailRequired : "Введите e-mail";

  const errPasswordRequired = typeof d.errPasswordRequired === "string" ? d.errPasswordRequired : "Введите пароль";

  const errCodeRequired = typeof d.errCodeRequired === "string" ? d.errCodeRequired : "Введите код";

  const errCaptchaRequired = typeof d.errCaptchaRequired === "string" ? d.errCaptchaRequired : "Подтвердите, что вы не робот";

  const errWrongCredentials = typeof d.errWrongCredentials === "string" ? d.errWrongCredentials : "Неверный e-mail или пароль";

  const errSendCodeFailed = typeof d.errSendCodeFailed === "string" ? d.errSendCodeFailed : "Не удалось отправить код";

  const errWrongCode = typeof d.errWrongCode === "string" ? d.errWrongCode : "Неверный код";
  const subtitle = typeof d.subtitle === "string" ? d.subtitle : "Войдите, чтобы продолжить";
  const tabPasswordLabel = typeof d.tabPasswordLabel === "string" ? d.tabPasswordLabel : "Пароль";
  const tabCodeLabel = typeof d.tabCodeLabel === "string" ? d.tabCodeLabel : "Код на e-mail";
  const rawEmailLabel = typeof d.emailLabel === "string" ? d.emailLabel : null;
  const emailLabel = (rawEmailLabel === null || rawEmailLabel === "E-mail или Telegram") ? "E-mail" : rawEmailLabel;
  const rawEmailPlaceholder = typeof d.emailPlaceholder === "string" ? d.emailPlaceholder : null;
  const emailPlaceholder = (rawEmailPlaceholder === null || rawEmailPlaceholder === "you@solonet.io · @username") ? "you@solonet.io" : rawEmailPlaceholder;
  const passwordLabel = typeof d.passwordLabel === "string" ? d.passwordLabel : "Пароль";
  const passwordPlaceholder = typeof d.passwordPlaceholder === "string" ? d.passwordPlaceholder : "••••••••••••";
  const codeLabel = typeof d.codeLabel === "string" ? d.codeLabel : "Код из письма";
  const codePlaceholder = typeof d.codePlaceholder === "string" ? d.codePlaceholder : "123456";
  const showPwLabel = typeof d.showPwLabel === "string" ? d.showPwLabel : "показать";
  const hidePwLabel = typeof d.hidePwLabel === "string" ? d.hidePwLabel : "скрыть";
  const rememberLabel = typeof d.rememberLabel === "string" ? d.rememberLabel : "Запомнить";
  const forgotLabel = typeof d.forgotLabel === "string" ? d.forgotLabel : "Забыли пароль?";
  const forgotHref = (typeof d.forgotHref === "string" && d.forgotHref.trim()) || slugToPath("forgot-password");
  const submitLabel = typeof d.submitLabel === "string" ? d.submitLabel : "Войти";
  const sendCodeLabel = typeof d.sendCodeLabel === "string" ? d.sendCodeLabel : "Получить код";
  const requestNewCodeLabel = typeof d.requestNewCodeLabel === "string" ? d.requestNewCodeLabel : "Запросить новый код";
  const orLabel = typeof d.orLabel === "string" ? d.orLabel : "или";
  const telegramLabel = typeof d.telegramLabel === "string" ? d.telegramLabel : "Telegram";
  const googleLabel = typeof d.googleLabel === "string" ? d.googleLabel : "Google";
  const yandexLabel = typeof d.yandexLabel === "string" ? d.yandexLabel : "Yandex";
  const successLabel = typeof d.successLabel === "string" ? d.successLabel : "Готово";
  const legalText = typeof d.legalText === "string" ? d.legalText : "";
  const inviteGiftTitle = typeof d.inviteGiftTitle === "string" ? d.inviteGiftTitle : "Вам отправили подарок";
  const inviteGiftText = typeof d.inviteGiftText === "string" ? d.inviteGiftText : "Войдите или зарегистрируйтесь в этой вкладке — подарок активируется автоматически после входа.";
  const inviteReferralTitle = typeof d.inviteReferralTitle === "string" ? d.inviteReferralTitle : "Вы пришли по приглашению";
  const inviteReferralText = typeof d.inviteReferralText === "string" ? d.inviteReferralText : "Войдите или зарегистрируйтесь в этой вкладке — приглашение привяжется к вашему аккаунту автоматически.";
  const invitePartnerTitle = typeof d.invitePartnerTitle === "string" ? d.invitePartnerTitle : "Партнёрская ссылка активна";
  const invitePartnerText = typeof d.invitePartnerText === "string" ? d.invitePartnerText : "Войдите или зарегистрируйтесь в этой вкладке — партнёрство привяжется автоматически.";
  const inviteOkLabel = typeof d.inviteOkLabel === "string" ? d.inviteOkLabel : "Понятно";

  const [inviteKind, setInviteKind] = useState<"gift" | "referral" | "partner" | null>(null);
  const [inviteShown, setInviteShown] = useState(false);
  const inviteCloseTimer = useRef<number | null>(null);
  useEffect(() => {
    if (previewMode || editMode || typeof window === "undefined") return;
    try {
      const gift = window.localStorage.getItem(PENDING_GIFT_CODE_KEY);
      const referral = window.localStorage.getItem(PENDING_REFERRAL_CODE_KEY);
      const partner = window.localStorage.getItem(PENDING_PARTNER_CODE_KEY);
      const kind = gift ? "gift" : referral ? "referral" : partner ? "partner" : null;
      if (!kind) return;
      const seenKey = `invite-notice-seen:${kind}:${gift ?? referral ?? partner ?? ""}`;
      if (sessionStorage.getItem(seenKey)) return;
      sessionStorage.setItem("invite-notice-seen-key", seenKey);
      setInviteKind(kind);
    } catch {}
  }, [previewMode, editMode]);
  useEffect(() => {
    if (!inviteKind) return;
    let r2 = 0;
    const r1 = requestAnimationFrame(() => {
      r2 = requestAnimationFrame(() => setInviteShown(true));
    });
    return () => {
      cancelAnimationFrame(r1);
      cancelAnimationFrame(r2);
    };
  }, [inviteKind]);
  useEffect(() => () => {
    if (inviteCloseTimer.current != null) window.clearTimeout(inviteCloseTimer.current);
  }, []);
  const closeInvite = () => {
    setInviteShown(false);
    try {
      const seenKey = sessionStorage.getItem("invite-notice-seen-key");
      if (seenKey) sessionStorage.setItem(seenKey, "1");
    } catch {}
    if (inviteCloseTimer.current != null) window.clearTimeout(inviteCloseTimer.current);
    inviteCloseTimer.current = window.setTimeout(() => setInviteKind(null), 260);
  };
  const inviteTitle = inviteKind === "gift" ? inviteGiftTitle : inviteKind === "referral" ? inviteReferralTitle : invitePartnerTitle;
  const inviteText = inviteKind === "gift" ? inviteGiftText : inviteKind === "referral" ? inviteReferralText : invitePartnerText;

  const requestedRedirect = searchParams.get("from");
  const adminRedirect = typeof d.successRedirect === "string" && d.successRedirect.startsWith("/") && !d.successRedirect.startsWith("//")
    ? d.successRedirect
    : null;
  const redirectPath = requestedRedirect && requestedRedirect.startsWith("/") && !requestedRedirect.startsWith("//")
    ? requestedRedirect
    : (adminRedirect ?? slugToPath("dashboard"));
  const { widgetRef, telegramWidgetReady } = useTelegramLoginWidget({
    botUsername,
    redirectPath,
    previewMode,
    editMode,
    telegramLoginEnabled,
    hasTelegramOIDC,
  });

  const setFormError = (err: unknown, fallback: string) => {
    if (isApiError(err)) {
      setError(err.message);
      return;
    }
    setError(err instanceof Error ? err.message : fallback);
  };

  const onSuccess = (data: { identity_id: string; identity?: IdentityMe }) => {
    setIdentityId(data.identity_id);
    if (data.identity) void swrMutate("/api/auth/me", data.identity, { revalidate: false });
    void applyPendingPostLoginActions();
    if (flowPage?.active) {
      flowPage.updateData({ authenticated: true, email: email.trim().toLowerCase(), identityId: data.identity_id });
      flowPage.next();
      return;
    }
    setSuccess(successLabel);
    router.replace(redirectPath);
  };

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (editMode) return;
    if (!email.trim()) { setError(errEmailRequired); return; }
    if (!password) { setError(errPasswordRequired); return; }
    setLoading(true); setError(null); setSuccess(null);
    try {
      const data = await apiFetchPublic<{ identity_id: string; identity: IdentityMe }>("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
      });
      onSuccess(data);
    } catch (err) {
      setFormError(err, errWrongCredentials);
    } finally {
      setLoading(false);
    }
  };

  const handleSendCode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (editMode) return;
    if (!email.trim()) { setError(errEmailRequired); return; }
    if (isTurnstileEnabled() && !turnstileToken) { setError(errCaptchaRequired); return; }
    setLoading(true); setError(null); setSuccess(null);
    try {
      await apiFetchPublic<{ ok: boolean }>("/api/auth/send-login-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim().toLowerCase(), allow_register: true, ...(turnstileToken ? { turnstile_token: turnstileToken } : {}) }),
      });
      setCodeSent(true);
      setCode("");
    } catch (err) {
      setFormError(err, errSendCodeFailed);
    } finally {
      setLoading(false);
    }
  };

  const handleCodeSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (editMode) return;
    if (!code.trim()) { setError(errCodeRequired); return; }
    setLoading(true); setError(null); setSuccess(null);
    try {
      const data = await apiFetchPublic<{ identity_id: string; identity: IdentityMe }>("/api/auth/login-by-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim().toLowerCase(), code: code.trim() }),
      });
      onSuccess(data);
    } catch (err) {
      setFormError(err, errWrongCode);
    } finally {
      setLoading(false);
    }
  };

  const navigateOAuth = (href: string) => {
    if (editMode) return;
    startOAuthLogin(href);
  };

  const handleTelegramClick = () => {
    if (editMode) return;
    if (hasTelegramOIDC) {
      void import("@/lib/telegram-oidc").then(({ startTelegramOIDCLogin }) => {
        startTelegramOIDCLogin(telegramClientId, redirectPath !== slugToPath("dashboard") ? redirectPath : undefined);
      });
    }
  };

  const monoSmall: React.CSSProperties = {
    fontFamily: sansFont,
    fontSize: "12px",
    fontWeight: 500,
    color: inkDim,
  };

  const FIELD_HEIGHT = 60;
  const fieldWrap: React.CSSProperties = {
    border: `1px solid ${lineColor}`,
    borderRadius: 14,
    padding: "9px 16px",
    background: t.innerBg,
    position: "relative",
    height: FIELD_HEIGHT,
    minHeight: FIELD_HEIGHT,
    maxHeight: FIELD_HEIGHT,
    flexShrink: 0,
    flexGrow: 0,
    boxSizing: "border-box",
    display: "block",
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    background: "transparent",
    border: "none",
    outline: "none",
    fontFamily: sansFont,
    fontSize: "15px",
    color: inkColor,
    fontWeight: 500,
    padding: 0,
    height: 22,
    lineHeight: "22px",
  };

  const tabStyle = (active: boolean): React.CSSProperties => ({
    flex: 1,
    padding: "9px 12px",
    textAlign: "center",
    cursor: editMode ? "default" : "pointer",
    fontFamily: sansFont,
    fontSize: "13px",
    color: active ? onAccentText : inkDim,
    fontWeight: 600,
    background: "transparent",
    border: "none",
    borderRadius: 999,
    position: "relative",
    zIndex: 1,
    transition: "color .2s",
  });

  const altButtonStyle: React.CSSProperties = {
    background: "transparent",
    border: "none",
    padding: "11px 12px",
    cursor: editMode ? "default" : "pointer",
    color: inkDim,
    fontFamily: sansFont,
    fontSize: "13px",
    fontWeight: 600,
    width: "100%",
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "center",
    justifyContent: "center",
    gap: "10px",
    transition: "background-color .15s, color .15s",
  };

  const SUBMIT_HEIGHT = 48;
  const submitButtonStyle: React.CSSProperties = {
    width: "100%",
    marginTop: "16px",
    height: SUBMIT_HEIGHT,
    minHeight: SUBMIT_HEIGHT,
    maxHeight: SUBMIT_HEIGHT,
    padding: "0 20px",
    background: success ? successBg : accentColor,
    border: "none",
    borderRadius: 14,
    cursor: editMode || loading ? "default" : "pointer",
    color: success ? onSuccessText : onAccentText,
    fontFamily: sansFont,
    fontSize: "14px",
    fontWeight: 700,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "12px",
    transition: "background .15s",
    opacity: loading ? 0.7 : 1,
    flexShrink: 0,
    flexGrow: 0,
    boxSizing: "border-box",
  };

  const iconTelegram = (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden>
      <path fill="#229ED9" d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.6 0 12 0zm5.6 8.2L15.7 17c-.1.7-.5.8-1.1.5l-3-2.2-1.4 1.4c-.2.2-.3.3-.6.3l.2-3.2 5.7-5.2c.2-.2 0-.3-.4-.1L7.9 13l-3-1c-.6-.2-.7-.6.1-.9L17 6.7c.5-.2 1 .1.6 1.5z" />
    </svg>
  );
  const iconGoogle = (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden>
      <path fill="#4285F4" d="M21.6 12.2c0-.7-.1-1.3-.2-2H12v3.9h5.4c-.2 1.2-1 2.3-2.1 3v2.5h3.4c2-1.8 3.1-4.5 3.1-7.4z" />
      <path fill="#34A853" d="M12 22c2.8 0 5.2-.9 7-2.5l-3.4-2.5c-.9.6-2.1 1-3.6 1-2.8 0-5.1-1.9-5.9-4.4H2.6v2.6C4.4 19.7 7.9 22 12 22z" />
      <path fill="#FBBC05" d="M6.1 13.6c-.2-.6-.3-1.2-.3-1.6s.1-1.1.3-1.6V7.8H2.6C1.9 9.1 1.5 10.5 1.5 12s.4 2.9 1.1 4.2l3.5-2.6z" />
      <path fill="#EA4335" d="M12 6c1.5 0 2.9.5 4 1.5l3-3C17.2 2.9 14.8 2 12 2 7.9 2 4.4 4.3 2.6 7.8l3.5 2.6C7 7.9 9.3 6 12 6z" />
    </svg>
  );
  const iconYandex = (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden>
      <circle cx="12" cy="12" r="11" fill="#FC3F1D" />
      <text x="12" y="16.5" textAnchor="middle" fontFamily="Inter, system-ui, sans-serif" fontSize="13" fontWeight="800" fill="#fff">Я</text>
    </svg>
  );

  const oauthBtns: React.ReactNode[] = [];
  if (showTelegramButton) {
    if (telegramLoginEnabled && !hasTelegramOIDC && !editMode) {
      oauthBtns.push(
        <div key="tg-widget" style={{ ...altButtonStyle, padding: "12px", justifyContent: "center" }}>
          <div ref={widgetRef} style={{ minHeight: 24 }} />
          {!telegramWidgetReady ? <span style={{ fontSize: "12px" }}>{telegramLabel}…</span> : null}
        </div>
      );
    } else {
      oauthBtns.push(
        <button
          key="tg-btn"
          className="dlf-oauth"
          type="button"
          disabled={editMode}
          onClick={handleTelegramClick}
          style={altButtonStyle}
          onMouseEnter={(e) => { if (!editMode) e.currentTarget.style.color = inkColor; }}
          onMouseLeave={(e) => { e.currentTarget.style.color = inkDim; }}
        >
          {iconTelegram}
          {telegramLabel}
        </button>
      );
    }
  }
  if (showGoogleLogin) {
    oauthBtns.push(
      <button
        key="google"
        className="dlf-oauth"
        type="button"
        disabled={editMode}
        onClick={() => navigateOAuth(googleHref)}
        style={altButtonStyle}
        onMouseEnter={(e) => { if (!editMode) e.currentTarget.style.color = inkColor; }}
        onMouseLeave={(e) => { e.currentTarget.style.color = inkDim; }}
      >
        {iconGoogle}
        {googleLabel}
      </button>
    );
  }
  if (showYandexLogin) {
    oauthBtns.push(
      <button
        key="yandex"
        className="dlf-oauth"
        type="button"
        disabled={editMode}
        onClick={() => navigateOAuth(yandexHref)}
        style={altButtonStyle}
        onMouseEnter={(e) => { if (!editMode) e.currentTarget.style.color = inkColor; }}
        onMouseLeave={(e) => { e.currentTarget.style.color = inkDim; }}
      >
        {iconYandex}
        {yandexLabel}
      </button>
    );
  }

  const renderEmailForm = () => {
    if (mode === "password") {
      return (
        <form onSubmit={handlePasswordSubmit} autoComplete="on">
          <div className="dlf-field" style={{ ...fieldWrap, marginBottom: 10 }}>
            <label style={{ ...monoSmall, fontSize: "12px", display: "block", marginBottom: "4px" }}>{emailLabel}</label>
            <input
              type="email"
              aria-label={emailLabel}
              placeholder={emailPlaceholder}
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={editMode}
              style={inputStyle}
            />
          </div>
          <div className="dlf-field" style={fieldWrap}>
            <label style={{ ...monoSmall, fontSize: "12px", display: "block", marginBottom: "4px" }}>{passwordLabel}</label>
            <input
              type={showPassword ? "text" : "password"}
              aria-label={passwordLabel}
              placeholder={passwordPlaceholder}
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={editMode}
              style={{ ...inputStyle, paddingRight: "70px" }}
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              disabled={editMode}
              style={{
                position: "absolute",
                right: "18px",
                top: "50%",
                transform: "translateY(-50%)",
                fontFamily: sansFont,
                fontSize: "12px",
                color: inkDim,
                letterSpacing: "0.12em",
                cursor: editMode ? "default" : "pointer",
                background: "transparent",
                border: "none",
                padding: "6px 8px",
                textTransform: "none",
              }}
            >
              {showPassword ? hidePwLabel : showPwLabel}
            </button>
          </div>

          <div style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginTop: "14px",
            ...monoSmall,
            fontSize: "11px",
          }}>
            <label style={{ display: "flex", alignItems: "center", gap: "10px", cursor: editMode ? "default" : "pointer", color: inkDim }}>
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                disabled={editMode}
                style={{ position: "absolute", opacity: 0, pointerEvents: "none" }}
              />
              <span style={{
                width: "16px",
                height: "16px",
                borderRadius: 5,
                border: `1.5px solid ${remember ? accentColor : inkMute}`,
                background: remember ? accentColor : "transparent",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
                transition: ".15s",
                color: onAccentText,
                fontSize: "9px",
                fontWeight: 700,
              }}>{remember ? "✓" : ""}</span>
              <span>{rememberLabel}</span>
            </label>
            {forgotLabel.trim() ? (
              <a
                href={forgotHref}
                onClick={(event) => { if (editMode) event.preventDefault(); }}
                style={{ color: inkDim, textDecoration: "none", borderBottom: `1px solid ${inkMute}` }}
              >
                {forgotLabel}
              </a>
            ) : null}
          </div>

          {error ? <ErrorBox text={error} accentColor={t.error} monoFont={sansFont} /> : null}

          {(() => {
            const passwordReady = email.trim().length > 0 && password.length > 0;
            return (
              <button
                className="dlf-submit"
                type="submit"
                disabled={editMode || loading || !passwordReady}
                style={{ ...submitButtonStyle, opacity: (!passwordReady || loading) ? 0.55 : 1 }}
              >
                <span>{success || (loading ? "…" : submitLabel)}</span>
                {!success && !loading ? <span>→</span> : null}
              </button>
            );
          })()}
        </form>
      );
    }

    const codeFieldDisabled = editMode || !codeSent;
    const sendCodeBtnLabel = codeSent ? requestNewCodeLabel : sendCodeLabel;
    return (
      <form onSubmit={handleCodeSubmit} autoComplete="on">
        <div className="dlf-field" style={{ ...fieldWrap, marginBottom: 10 }}>
          <label style={{ ...monoSmall, fontSize: "12px", display: "block", marginBottom: "4px" }}>{emailLabel}</label>
          <input
            type="email"
            aria-label={emailLabel}
            placeholder={emailPlaceholder}
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={editMode}
            style={inputStyle}
          />
        </div>
        <div className="dlf-field" style={fieldWrap}>
          <label style={{ ...monoSmall, fontSize: "12px", display: "block", marginBottom: "4px" }}>{codeLabel}</label>
          <input
            type="text"
            inputMode="numeric"
            aria-label={codeLabel}
            placeholder={codePlaceholder}
            autoComplete="one-time-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            disabled={codeFieldDisabled}
            maxLength={6}
            style={{ ...inputStyle, paddingRight: "110px", opacity: codeFieldDisabled ? 0.55 : 1 }}
          />
          <button
            type="button"
            onClick={(e) => {
              if (editMode || loading) return;
              handleSendCode(e as unknown as React.FormEvent);
            }}
            disabled={editMode || loading || !email.trim()}
            style={{
              position: "absolute",
              right: "18px",
              top: "50%",
              transform: "translateY(-50%)",
              fontFamily: sansFont,
              fontSize: "12px",
              color: codeSent ? inkDim : accentColor,
              letterSpacing: "0.12em",
              cursor: editMode || loading || !email.trim() ? "default" : "pointer",
              background: "transparent",
              border: "none",
              padding: "6px 8px",
              textTransform: "none",
              fontWeight: 600,
            }}
          >
            {loading ? "…" : sendCodeBtnLabel}
          </button>
        </div>

        {!editMode && isTurnstileEnabled() ? (
          <div style={{ marginTop: 12, display: "flex", justifyContent: "center" }}>
            <Turnstile onVerify={setTurnstileToken} onExpire={() => setTurnstileToken(null)} onError={() => setTurnstileToken(null)} />
          </div>
        ) : null}

        <div style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginTop: "14px",
          ...monoSmall,
          fontSize: "11px",
        }}>
          <label style={{ display: "flex", alignItems: "center", gap: "10px", cursor: editMode ? "default" : "pointer", color: inkDim }}>
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              disabled={editMode}
              style={{ position: "absolute", opacity: 0, pointerEvents: "none" }}
            />
            <span style={{
              width: "16px",
              height: "16px",
              borderRadius: 5,
              border: `1.5px solid ${remember ? accentColor : inkMute}`,
              background: remember ? accentColor : "transparent",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
              transition: ".15s",
              color: onAccentText,
              fontSize: "9px",
              fontWeight: 700,
            }}>{remember ? "✓" : ""}</span>
            <span>{rememberLabel}</span>
          </label>
        </div>

        {error ? <ErrorBox text={error} accentColor={t.error} monoFont={sansFont} /> : null}

        <button className="dlf-submit" type="submit" disabled={editMode || loading || !codeSent || !code.trim()} style={{ ...submitButtonStyle, opacity: (!codeSent || !code.trim() || loading) ? 0.55 : 1 }}>
          <span>{success || (loading ? "…" : submitLabel)}</span>
          {!success && !loading ? <span>→</span> : null}
        </button>
      </form>
    );
  };

  const formNode = (
    <div
      className={`${ELEMENT_FILL_CLASS} overflow-y-auto`}
      style={{
        fontFamily: sansFont,
        color: inkColor,
        paddingTop: 22,
        paddingBottom: 22,
        display: "flex",
        flexDirection: "column",
        borderRadius: cardRadius,
        background: t.panel,
        boxShadow: panelShadow(t),
        ["--dlf-accent" as never]: accentColor,
        ["--dlf-accent-ring" as never]: hexToRgba(accentColor, 0.14),
        ["--dlf-accent-glow" as never]: hexToRgba(accentColor, 0.32),
        ["--dlf-hover-bg" as never]: hexToRgba(inkColor, 0.04),
        ...decor,
      }}
    >
      <style>{FORM_CSS}</style>
      <div className="dlf-anim max-w-[460px] mx-auto px-6" style={{ width: "100%", boxSizing: "border-box", marginTop: "auto", marginBottom: "auto" }}>
        <h1 style={{
          fontFamily: sansFont,
          fontWeight: 700,
          fontSize: "26px",
          lineHeight: 1.08,
          letterSpacing: "-0.02em",
          marginBottom: "10px",
          color: inkColor,
        }}>
          {title}
        </h1>
        {subtitle ? <div style={{ ...monoSmall, fontSize: "13px", marginBottom: "18px" }}>{subtitle}</div> : null}

        {showTabs && hasEmailForm ? (
          <div style={{ position: "relative", display: "flex", gap: 4, padding: 4, background: t.innerBg, border: `1px solid ${lineColor}`, borderRadius: 999, marginBottom: "18px" }}>
            <span
              aria-hidden
              className="dlf-tab-ind"
              style={{
                position: "absolute",
                top: 4,
                bottom: 4,
                left: 4,
                width: "calc(50% - 6px)",
                borderRadius: 999,
                background: accentColor,
                transform: mode === "code" ? "translateX(calc(100% + 4px))" : "translateX(0)",
              }}
            />
            <button
              type="button"
              onClick={() => { if (!editMode) { setMode("password"); setError(null); } }}
              style={tabStyle(mode === "password")}
              disabled={editMode}
            >
              {tabPasswordLabel}
            </button>
            <button
              type="button"
              onClick={() => { if (!editMode) { setMode("code"); setError(null); setCodeSent(false); } }}
              style={tabStyle(mode === "code")}
              disabled={editMode}
            >
              {tabCodeLabel}
            </button>
          </div>
        ) : null}

        {hasEmailForm ? renderEmailForm() : null}

        {hasOAuth && oauthBtns.length > 0 ? (
          <>
            {hasEmailForm ? (
              <div style={{
                margin: "18px 0 12px",
                display: "flex",
                alignItems: "center",
                gap: "14px",
                fontFamily: sansFont,
                fontSize: "12px",
                fontWeight: 500,
                color: inkMute,
              }}>
                <div style={{ flex: 1, height: "1px", background: lineColor }} />
                <span>{orLabel}</span>
                <div style={{ flex: 1, height: "1px", background: lineColor }} />
              </div>
            ) : null}
            <div style={{
              display: "grid",
              gridTemplateColumns: `repeat(${oauthBtns.length}, 1fr)`,
              background: t.innerBg,
              border: `1px solid ${lineColor}`,
              borderRadius: 14,
              overflow: "hidden",
            }}>
              {oauthBtns.map((btn, i) => (
                <div key={i} style={{ borderRight: i < oauthBtns.length - 1 ? `1px solid ${lineColor}` : "none" }}>
                  {btn}
                </div>
              ))}
            </div>
          </>
        ) : null}

        {legalText ? (
          <div style={{
            marginTop: "16px",
            fontFamily: sansFont,
            fontSize: "11px",
            color: inkMute,
            lineHeight: 1.55,
          }}>{legalText}</div>
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
  const inviteModal = inviteKind && typeof document !== "undefined"
    ? createPortal(
      <div
        onClick={closeInvite}
        style={{
          position: "fixed",
          inset: 0,
          zIndex: 100000,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 20,
          background: "rgba(8,10,16,0.62)",
          backdropFilter: "blur(6px)",
          WebkitBackdropFilter: "blur(6px)",
          opacity: inviteShown ? 1 : 0,
          transition: "opacity 220ms ease",
          pointerEvents: inviteShown ? "auto" : "none",
        }}
      >
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            width: "100%",
            maxWidth: 360,
            background: t.panel,
            color: t.ink,
            borderRadius: t.radius.md,
            boxShadow: panelShadow(t),
            fontFamily: t.monoFont,
            padding: "26px 24px 22px",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 14,
            textAlign: "center",
            opacity: inviteShown ? 1 : 0,
            transform: inviteShown ? "scale(1) translateY(0)" : "scale(0.92) translateY(8px)",
            transition: "opacity 220ms ease, transform 240ms cubic-bezier(0.2,0.8,0.2,1)",
          }}
        >
          <span
            style={{
              width: 52,
              height: 52,
              borderRadius: 16,
              display: "grid",
              placeItems: "center",
              background: hexToRgba(t.accent, 0.12),
              color: t.accent,
            }}
          >
            {inviteKind === "gift" ? (
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="8" width="18" height="4" rx="1" />
                <path d="M5 12v8a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-8" />
                <path d="M12 8v13" />
                <path d="M12 8c-1.5-3.5-6-3.5-6-1s4.5 2.5 6 1Z" />
                <path d="M12 8c1.5-3.5 6-3.5 6-1s-4.5 2.5-6 1Z" />
              </svg>
            ) : (
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="9" cy="8" r="3.2" />
                <path d="M3.5 19c.7-3 3-4.5 5.5-4.5s4.8 1.5 5.5 4.5" />
                <path d="M17 8h5" />
                <path d="M19.5 5.5v5" />
              </svg>
            )}
          </span>
          <div style={{ fontSize: t.font.lg, fontWeight: t.weight.bold, color: t.ink }}>{inviteTitle}</div>
          <p style={{ margin: 0, color: t.inkDim, fontSize: t.font.sm, lineHeight: 1.55 }}>{inviteText}</p>
          <button type="button" onClick={closeInvite} style={{ ...btnSolid(t), alignSelf: "stretch", cursor: "pointer", marginTop: 4 }}>
            {inviteOkLabel}
          </button>
        </div>
      </div>,
      document.body,
    )
    : null;

  return wrap(
    <SurfaceEffectFrame {...surfaceFrameProps} overflowVisible targetStyle={{ borderRadius: cardRadius }}>
      {formNode}
      {inviteModal}
    </SurfaceEffectFrame>,
    false,
    true,
  );
}

function ErrorBox({ text, accentColor, monoFont }: { text: string; accentColor: string; monoFont: string }) {
  return (
    <div style={{
      marginTop: "16px",
      padding: "11px 14px",
      borderRadius: 12,
      background: hexToRgba(accentColor, 0.1),
      fontFamily: monoFont,
      fontSize: "13px",
      fontWeight: 500,
      color: accentColor,
    }}>{text}</div>
  );
}
