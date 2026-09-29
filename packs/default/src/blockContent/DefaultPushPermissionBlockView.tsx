"use client";

import { useLayoutEffect, useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, btnSolid, DefaultPanel, f, parseBlockData, useIsMobile, actionsSolo } from ".";
import { isPushSupported, requestPushPermissionAndSubscribe } from "@/lib/notifications";
import { hapticImpact, isTelegramWebApp } from "@/lib/telegram-webapp";
import { isIOSDevice, isStandalonePwa } from "@/lib/pwa-env";

const SCHEMA = {
  panelHeader: f.str("Уведомления"),
  hint: f.str(""),
  description: f.str("Разреши push-уведомления, чтобы не пропустить окончание подписки, оплаты и важные события."),
  enableLabel: f.str("Разрешить уведомления"),
  grantedLabel: f.str("Уведомления разрешены"),
  deniedLabel: f.str("Уведомления заблокированы в настройках браузера"),
  unsupportedLabel: f.str("Уведомления не поддерживаются в этом браузере"),
  subscribeErrorLabel: f.str("Разрешение получено, но подписку не удалось сохранить. Попробуйте ещё раз."),
  iosInstallFirstLabel: f.str("На iPhone сначала установи приложение на экран «Домой» (кнопка «Поделиться» → «На экран «Домой»»), потом включи уведомления здесь."),
  telegramLabel: f.str("Push-уведомления доступны только в приложении, установленном через сайт: открой кабинет в браузере, добавь на экран «Домой» — и включи уведомления там. В Telegram их шлёт сам бот."),
};

type Perm = "default" | "granted" | "denied" | "unsupported" | "ios" | "telegram";



export function DefaultPushPermissionBlockView({ block, context }: TypedBlockViewProps<"defaultPushPermission">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const isMobile = useIsMobile();
  const cfg = parseBlockData(d, SCHEMA);

  const [perm, setPerm] = useState<Perm>("default");
  const [busy, setBusy] = useState(false);
  const [subscribeFailed, setSubscribeFailed] = useState(false);

  useLayoutEffect(() => {
    if (previewMode) return;
    if (isTelegramWebApp()) {
      setPerm("telegram");
      return;
    }
    if (!isPushSupported()) {
      setPerm(isIOSDevice() && !isStandalonePwa() ? "ios" : "unsupported");
      return;
    }
    setPerm(Notification.permission as Perm);
  }, [previewMode]);

  const enable = async () => {
    if (previewMode || busy) return;
    hapticImpact();
    setBusy(true);
    let ok = false;
    try {
      ok = await requestPushPermissionAndSubscribe();
    } catch {
      ok = false;
    }
    const permission = (typeof Notification !== "undefined" ? Notification.permission : "default") as Perm;
    setPerm(permission);
    setSubscribeFailed(!ok && permission === "granted");
    setBusy(false);
  };

  const dotColor = perm === "granted" ? t.success : perm === "denied" || perm === "unsupported" ? t.error : t.accent;

  return wrap(
    <DefaultPanel t={t} header={cfg.panelHeader} hint={cfg.hint || undefined}>
      <div style={{ fontSize: t.font.sm, color: t.inkDim, lineHeight: 1.5 }}>{cfg.description}</div>
      {perm === "granted" && !subscribeFailed ? (
        <div style={{ display: "flex", alignItems: "center", gap: t.space.sm, fontSize: t.font.sm, color: t.success, fontWeight: t.weight.bold }}>
          <span style={{ width: 8, height: 8, borderRadius: 999, background: dotColor }} />
          {cfg.grantedLabel}
        </div>
      ) : perm === "denied" ? (
        <div style={{ fontSize: t.font.xs, color: t.inkMute }}>{cfg.deniedLabel}</div>
      ) : perm === "unsupported" ? (
        <div style={{ fontSize: t.font.xs, color: t.inkMute }}>{cfg.unsupportedLabel}</div>
      ) : perm === "ios" ? (
        <div style={{ fontSize: t.font.xs, color: t.inkMute, lineHeight: 1.5 }}>{cfg.iosInstallFirstLabel}</div>
      ) : perm === "telegram" ? (
        <div style={{ fontSize: t.font.xs, color: t.inkMute, lineHeight: 1.5 }}>{cfg.telegramLabel}</div>
      ) : (
        <>
          {subscribeFailed ? (
            <div style={{ fontSize: t.font.xs, color: t.error, lineHeight: 1.5 }}>{cfg.subscribeErrorLabel}</div>
          ) : null}
          <button
            type="button"
            onClick={() => void enable()}
            disabled={busy}
            style={{ ...btnSolid(t), alignSelf: "flex-start", cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1, ...actionsSolo(isMobile) }}
          >
            {cfg.enableLabel}
          </button>
        </>
      )}
    </DefaultPanel>,
    false,
    true,
  );
}
