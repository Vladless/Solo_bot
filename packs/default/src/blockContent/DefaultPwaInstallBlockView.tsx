"use client";

import { useEffect, useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, btnSolid, DefaultPanel, f, parseBlockData, useIsMobile, actionsSolo } from ".";
import { isTelegramWebApp, hapticImpact, openTelegramWebAppExternalLink } from "@/lib/telegram-webapp";
import { isIOSDevice, isStandalonePwa } from "@/lib/pwa-env";

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

const SCHEMA = {
  panelHeader: f.str("Приложение"),
  hint: f.str(""),
  description: f.str("Установи кабинет как приложение — быстрый доступ с домашнего экрана, без браузера."),
  installLabel: f.str("Установить приложение"),
  openInBrowserLabel: f.str("Открыть в браузере"),
  telegramHint: f.str("Чтобы установить как отдельное приложение, открой кабинет во внешнем браузере и нажми «Установить»."),
  iosInstructions: f.str("Нажми «Поделиться» внизу Safari и выбери «На экран «Домой»»."),
  installedLabel: f.str("Приложение установлено"),
  unavailableLabel: f.str("Установка недоступна в этом браузере"),
};



export function DefaultPwaInstallBlockView({ block, context }: TypedBlockViewProps<"defaultPwaInstall">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const isMobile = useIsMobile();
  const cfg = parseBlockData(d, SCHEMA);

  const [installed, setInstalled] = useState(false);
  const [deferred, setDeferred] = useState<BeforeInstallPromptEvent | null>(null);
  const [tgInstall, setTgInstall] = useState(false);
  const [ios, setIos] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (previewMode) return;
    setInstalled(isStandalonePwa());
    setTgInstall(isTelegramWebApp());
    setIos(isIOSDevice());
    const early = (window as unknown as { __bip?: BeforeInstallPromptEvent }).__bip;
    if (early) setDeferred(early);
    const onPrompt = (e: Event) => {
      e.preventDefault();
      setDeferred(e as BeforeInstallPromptEvent);
    };
    const onInstalled = () => { setInstalled(true); setDeferred(null); };
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, [previewMode]);

  const canInstall = previewMode ? true : (tgInstall || deferred !== null);
  const showInstalled = !previewMode && installed;
  const showIosManual = !previewMode && !showInstalled && !canInstall && ios;

  const install = async () => {
    if (previewMode || busy) return;
    hapticImpact();
    if (tgInstall) {
      openTelegramWebAppExternalLink(`${window.location.origin}/dashboard`);
      return;
    }
    if (deferred) {
      setBusy(true);
      try {
        await deferred.prompt();
        const choice = await deferred.userChoice;
        if (choice.outcome === "accepted") { setInstalled(true); setDone(true); }
      } catch { /* ignore */ }
      setDeferred(null);
      setBusy(false);
    }
  };

  return wrap(
    <DefaultPanel t={t} header={cfg.panelHeader} hint={cfg.hint || undefined}>
      <div style={{ fontSize: t.font.sm, color: t.inkDim, lineHeight: 1.5 }}>{cfg.description}</div>
      {showInstalled ? (
        <div style={{ display: "flex", alignItems: "center", gap: t.space.sm, fontSize: t.font.sm, color: t.success, fontWeight: t.weight.bold }}>
          <span style={{ width: 8, height: 8, borderRadius: 999, background: t.success }} />
          {cfg.installedLabel}
        </div>
      ) : canInstall ? (
        <div style={{ display: "flex", flexDirection: "column", gap: t.space.sm }}>
          {tgInstall ? (
            <div style={{ fontSize: t.font.xs, color: t.inkMute, lineHeight: 1.5 }}>{cfg.telegramHint}</div>
          ) : null}
          <button
            type="button"
            onClick={() => void install()}
            disabled={busy}
            style={{ ...btnSolid(t), alignSelf: "flex-start", cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1, ...actionsSolo(isMobile) }}
          >
            {tgInstall ? cfg.openInBrowserLabel : done ? cfg.installedLabel : cfg.installLabel}
          </button>
        </div>
      ) : showIosManual ? (
        <div style={{ fontSize: t.font.xs, color: t.inkMute, lineHeight: 1.5 }}>{cfg.iosInstructions}</div>
      ) : (
        <div style={{ fontSize: t.font.xs, color: t.inkMute }}>{cfg.unavailableLabel}</div>
      )}
    </DefaultPanel>,
    false,
    true,
  );
}
