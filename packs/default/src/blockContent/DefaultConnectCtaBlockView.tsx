"use client";

import { useMemo, useState } from "react";
import type { CSSProperties } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { useKeyLinkActions } from "@/components/constructor/blockContent/cabinetKit/useKeyLinkActions";
import {
  useDefaultTheme,
  DefaultPanel,
  panelStyle,
  pickContrast,
  blendHex,
  panelShadow,
  btnSolid,
  btnSecondary,
  btnSm,
  f,
  parseBlockData,
  useBlockApi,
  useIsMobile,
  CONTROL_H,
  CONTROL_R,
} from ".";
import { DefaultQrModal } from "./DefaultQrModal";
import { DefaultChipPicker, DefaultStageBar, DefaultStepList, type ChipPalette } from "./DefaultAppInstall";
import { hexToRgba } from "@/components/constructor/utils";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import {
  DEFAULT_APP_CATALOG,
  DEFAULT_CONNECT_STEPS,
  parseApps,
  parseSteps,
  platformLabels,
  resolveImportUrl,
  type AppItem,
  type AppStep,
} from "@/components/constructor/blockContent/appCatalog";
import { useAppInstallFlow } from "@/components/constructor/blockContent/useAppInstallFlow";
import { useAppInfo } from "@/app/AppInfoProvider";
import { useToast } from "@/components/ui/Toast";
import { slugToPath } from "@/lib/web-page-registry";

const CONNECT_CTA_SCHEMA = {
  viewMode: f.enum(["all", "steps"] as const, "all"),
  panelHint: f.str("Подключение к VPN"),
  fallbackTitle: f.str("VPN"),
  noKeysText: f.str("Нет активной подписки"),
  noKeysCtaLabel: f.str("Оформить подписку"),
  noKeysCtaHref: f.str(""),
  loadingText: f.str("Загрузка..."),
  connectLabel: f.str("Подключить"),
  copyLabel: f.str("Копировать"),
  copiedLabel: f.str("Скопировано"),
  shareLabel: f.str("Поделиться"),
  shareTitle: f.str("Подключение к VPN"),
  qrLabel: f.str("QR-код"),
  qrCloseLabel: f.str("Закрыть QR"),
  qrErrorText: f.str("Не удалось загрузить QR"),
  qrShareHint: f.str("Если хотите поделиться подпиской — на устройстве друга откройте такое же приложение и через него наведите на этот QR."),
  showConnect: f.bool(true),
  showCopy: f.bool(true),
  showLink: f.bool(true),
  showShare: f.bool(true),
  showQr: f.bool(true),
  showRename: f.bool(true),
  renamePlaceholder: f.str("Имя подписки"),
  renameErrorText: f.str("Не удалось переименовать. Попробуйте ещё раз."),
  renameSuccessText: f.str("Имя подписки обновлено"),
  subtitleText: f.str("Ключ доступа к подписке"),
  showAppPicker: f.bool(true),
  platformTitle: f.str("Ваше устройство"),
  clientTitle: f.str("Приложение"),
  connectTitle: f.str("Подключение"),
  stepsTitle: f.str("Что делать"),
  downloadLabel: f.str("Скачать"),
  backLabel: f.str("Назад"),
  counterFormat: f.str("Шаг {n} из {total}"),
  noClientsText: f.str("Для этой платформы приложений пока нет"),
  labelIos: f.str("iPhone / iPad"),
  labelAndroid: f.str("Android"),
  labelWindows: f.str("Windows"),
  labelMacos: f.str("macOS"),
  labelLinux: f.str("Linux"),
  apps: f.array<AppItem>(DEFAULT_APP_CATALOG),
  steps: f.array<AppStep>(DEFAULT_CONNECT_STEPS),
};

export function DefaultConnectCtaBlockView({ block, context }: TypedBlockViewProps<"defaultConnectCta">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const isMobile = useIsMobile();
  const decor = usePanelDecor(t.panel);
  const cfg = parseBlockData(d, CONNECT_CTA_SCHEMA);

  const isPreview = previewMode === true;
  const api = useBlockApi({
    needs: ["activeSubscription"],
    disabled: isPreview,
    mock: isPreview,
  });
  const mut = useAccountMutations();
  const appInfo = useAppInfo();
  const toast = useToast();

  const activeKey = api.activeKey;
  const remnawaveLink = activeKey?.remnawave_link?.trim() || "";
  const link = (remnawaveLink || activeKey?.key?.trim() || "").trim();
  const titleText = activeKey?.alias?.trim() || activeKey?.email?.split("@")[0] || activeKey?.client_id?.slice(0, 8) || cfg.fallbackTitle;

  const apps = useMemo(() => parseApps(cfg.apps), [cfg.apps]);
  const steps = useMemo(() => parseSteps(cfg.steps), [cfg.steps]);
  const labels = useMemo(() => platformLabels(d), [d]);
  const stepper = cfg.viewMode === "steps";

  const flow = useAppInstallFlow({
    apps,
    steps,
    labels,
    stepper,
    previewMode: isPreview,
    counterFormat: cfg.counterFormat,
  });

  const { copied, copy: onCopy, share: onShare, qrOpen, qrImg, qrLoading, qrError, toggleQr: onToggleQr, closeQr } = useKeyLinkActions({
    link,
    clientId: activeKey?.client_id,
    isPreview,
    shareTitle: cfg.shareTitle,
    qrErrorText: cfg.qrErrorText,
  });
  const [editing, setEditing] = useState(false);
  const [aliasDraft, setAliasDraft] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameError, setRenameError] = useState("");

  const showRename = !isPreview && !!activeKey && cfg.showRename;
  const startRename = () => { setAliasDraft(activeKey?.alias ?? ""); setRenameError(""); setEditing(true); };
  const saveRename = async () => {
    if (!activeKey || renameBusy) return;
    const v = aliasDraft.trim();
    setRenameBusy(true);
    setRenameError("");
    try {
      if (v) {
        await mut.renameKey(activeKey.client_id, v);
        if (!isPreview) toast.success(cfg.renameSuccessText);
      }
      setEditing(false);
    } catch {
      setRenameError(cfg.renameErrorText);
    }
    finally { setRenameBusy(false); }
  };

  const connectHref =
    link && !isPreview
      ? !flow.selected && remnawaveLink
        ? remnawaveLink
        : resolveImportUrl(link, flow.selected, flow.platform, appInfo.connect)
      : "";
  const connectExternal = /^https?:/i.test(connectHref);




  const qrModal = (
    <DefaultQrModal
      t={t}
      open={qrOpen}
      onClose={closeQr}
      qrImg={qrImg}
      loading={qrLoading}
      error={qrError}
      caption={cfg.qrShareHint}
      title={cfg.qrLabel}
      closeLabel={cfg.qrCloseLabel}
      loadingText={cfg.loadingText}
    />
  );

  if (!isPreview && !activeKey && api.keys.isLoading) {
    return wrap(
      <div
        className={ELEMENT_FILL_CLASS}
        style={{ ...panelStyle(t), padding: t.space.xl, color: t.inkDim, display: "flex", alignItems: "center", justifyContent: "center", fontSize: t.font.sm, ...decor }}
      >
        {cfg.loadingText}
      </div>,
      false,
      true,
    );
  }

  if (!isPreview && !activeKey && !api.keys.isLoading) {
    const noKeysCtaHref = cfg.noKeysCtaHref.trim() || slugToPath("tariffs");
    return wrap(
      <div
        className={ELEMENT_FILL_CLASS}
        style={{ ...panelStyle(t), padding: t.space.xl, color: t.inkDim, display: "flex", flexDirection: "column", gap: t.space.lg, alignItems: "center", justifyContent: "center", fontSize: t.font.sm, textAlign: "center", ...decor }}
      >
        <span>{cfg.noKeysText}</span>
        {cfg.noKeysCtaLabel.trim() ? (
          <a
            href={noKeysCtaHref}
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              padding: "10px 20px",
              borderRadius: t.radius.sm,
              background: t.accent,
              color: pickContrast(t.accent),
              fontWeight: t.weight.bold,
              fontSize: t.font.smPlus,
              textDecoration: "none",
              cursor: "pointer",
            }}
          >
            {cfg.noKeysCtaLabel}
          </a>
        ) : null}
      </div>,
      false,
      true,
    );
  }

  if (stepper) {
    return wrap(
      <>
        <DefaultPanel t={t} header={cfg.panelHint || undefined} bodyGap={t.space.lg}>
          {flow.stage === "platform" ? (
            <>
              <DefaultStageBar t={t} title={cfg.platformTitle} counter={flow.counter("platform")} backLabel="" onBack={null} />
              <DefaultChipPicker t={t} options={flow.platformOptions} value={flow.platform} onChange={flow.pickPlatform} />
            </>
          ) : null}

          {flow.stage === "app" ? (
            <>
              <DefaultStageBar
                t={t}
                title={cfg.clientTitle}
                counter={flow.counter("app")}
                backLabel={cfg.backLabel}
                onBack={() => flow.goTo("platform")}
              />
              {flow.appOptions.length === 0 ? (
                <div style={{ fontSize: t.font.smPlus, color: t.inkMute }}>{cfg.noClientsText}</div>
              ) : (
                <DefaultChipPicker t={t} options={flow.appOptions} value={flow.selected?.id ?? ""} onChange={flow.pickApp} />
              )}
              {flow.downloadUrl && cfg.downloadLabel ? (
                <a
                  href={isPreview ? undefined : flow.downloadUrl}
                  target="_blank"
                  rel="noreferrer"
                  style={{ ...btnSecondary(t), ...btnSm(t), borderRadius: CONTROL_R, alignSelf: "flex-start", textDecoration: "none", color: t.accent }}
                >
                  {cfg.downloadLabel}
                </a>
              ) : null}
            </>
          ) : null}

          {flow.stage === "final" ? (
            <>
              <DefaultStageBar
                t={t}
                title={cfg.connectTitle}
                counter={flow.counter("final")}
                backLabel={cfg.backLabel}
                onBack={() => flow.goTo("app")}
              />
              {cfg.showConnect && cfg.connectLabel ? (
                <a
                  href={connectHref || undefined}
                  {...(connectExternal ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                  onClick={(e) => { if (!connectHref) e.preventDefault(); }}
                  aria-disabled={!link}
                  style={{
                    ...btnSolid(t),
                    minHeight: t.px(CONTROL_H),
                    borderRadius: CONTROL_R,
                    width: isMobile ? "100%" : undefined,
                    alignSelf: isMobile ? "stretch" : "flex-start",
                    padding: `0 ${t.space.xl}px`,
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    textDecoration: "none",
                    opacity: link ? 1 : 0.5,
                    cursor: link ? "pointer" : "default",
                  }}
                >
                  {cfg.connectLabel}
                </a>
              ) : null}

              {flow.steps.length > 0 ? (
                <div style={{ display: "flex", flexDirection: "column", gap: t.space.sm }}>
                  {cfg.stepsTitle ? <div style={{ fontSize: t.font.xs, color: t.inkDim }}>{cfg.stepsTitle}</div> : null}
                  <DefaultStepList t={t} steps={flow.steps} />
                </div>
              ) : null}

              {link && cfg.showLink ? (
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: t.space.md,
                    justifyContent: "space-between",
                    borderRadius: CONTROL_R,
                    background: t.innerBg,
                    padding: `${t.space.sm}px ${t.space.sm}px ${t.space.sm}px ${t.space.mdPlus}px`,
                  }}
                >
                  <code className="showcase-sensitive" style={{ color: t.inkDim, fontFamily: t.monoFont, fontSize: t.font.xs, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{link}</code>
                  {cfg.showCopy ? (
                    <button type="button" onClick={onCopy} style={{ ...btnSecondary(t), ...btnSm(t), flexShrink: 0, borderRadius: CONTROL_R, color: t.accent }}>
                      {copied ? cfg.copiedLabel : cfg.copyLabel}
                    </button>
                  ) : null}
                </div>
              ) : null}

              <div style={{ display: "flex", gap: t.space.sm, flexWrap: "wrap" }}>
                {cfg.showShare && cfg.shareLabel ? (
                  <button type="button" onClick={onShare} disabled={!link} style={{ ...btnSecondary(t), ...btnSm(t), borderRadius: CONTROL_R, opacity: link ? 1 : 0.5 }}>
                    {cfg.shareLabel}
                  </button>
                ) : null}
                {cfg.showQr && cfg.qrLabel ? (
                  <button type="button" onClick={onToggleQr} disabled={!activeKey} style={{ ...btnSecondary(t), ...btnSm(t), borderRadius: CONTROL_R, opacity: activeKey ? 1 : 0.5 }}>
                    {qrOpen ? cfg.qrCloseLabel : cfg.qrLabel}
                  </button>
                ) : null}
              </div>
            </>
          ) : null}
        </DefaultPanel>
        {qrModal}
      </>,
      false,
      true,
    );
  }

  const onAccent = pickContrast(t.accent);
  const onAccentDim = hexToRgba(onAccent, 0.78);
  const gradient = `linear-gradient(135deg, ${blendHex(t.accent, "#FFFFFF", 0.08)}, ${blendHex(t.accent, "#000000", 0.16)})`;
  const heroPalette: ChipPalette = {
    idleBg: hexToRgba(onAccent, 0.16),
    idleInk: onAccentDim,
    activeBg: onAccent,
    activeInk: t.accent,
  };
  const heroBtn = (bg: string, color: string, border?: string): CSSProperties => ({
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    minHeight: 44,
    padding: "0 20px",
    borderRadius: CONTROL_R,
    background: bg,
    color,
    border: border ?? "none",
    fontFamily: t.monoFont,
    fontWeight: t.weight.bold,
    fontSize: t.font.sm,
    cursor: "pointer",
  });

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
      <div style={{ position: "relative", zIndex: 1, display: "flex", flexDirection: "column", justifyContent: "space-between", height: "100%", gap: t.space.lg }}>
        <div>
          <div style={{ fontSize: t.font.smPlus, color: onAccentDim, marginBottom: 12 }}>{cfg.panelHint}</div>
          {editing ? (
            <div style={{ display: "flex", alignItems: "center", gap: t.space.sm }}>
              <input
                value={aliasDraft}
                onChange={(e) => setAliasDraft(e.target.value)}
                maxLength={10}
                autoFocus
                placeholder={cfg.renamePlaceholder}
                onKeyDown={(e) => { if (e.key === "Enter") void saveRename(); if (e.key === "Escape") setEditing(false); }}
                style={{ flex: "1 1 auto", minWidth: 0, height: 44, boxSizing: "border-box", background: hexToRgba(onAccent, 0.16), border: "none", borderRadius: t.radius.sm, color: onAccent, fontFamily: t.monoFont, fontSize: t.font.xl, fontWeight: t.weight.bold, letterSpacing: "-0.02em", padding: "0 14px", outline: "none" }}
              />
              <button type="button" onClick={() => void saveRename()} disabled={renameBusy} aria-label="Сохранить" style={{ flexShrink: 0, width: 44, height: 44, borderRadius: t.radius.sm, background: onAccent, color: t.accent, border: "none", cursor: "pointer", fontSize: 18, fontWeight: t.weight.bold, opacity: renameBusy ? 0.6 : 1 }}>✓</button>
              <button type="button" onClick={() => setEditing(false)} aria-label="Отмена" style={{ flexShrink: 0, width: 44, height: 44, borderRadius: t.radius.sm, background: "transparent", color: onAccent, border: `1px solid ${hexToRgba(onAccent, 0.45)}`, cursor: "pointer", fontSize: 16 }}>✕</button>
            </div>
          ) : null}
          {editing && renameError ? (
            <div style={{ fontSize: t.font.sm, color: onAccent, marginTop: 8 }}>{renameError}</div>
          ) : null}
          {!editing ? (
            <div style={{ display: "flex", alignItems: "center", gap: t.space.md, minWidth: 0 }}>
              <div style={{ fontSize: t.font.giant, fontWeight: t.weight.bold, letterSpacing: "-0.02em", lineHeight: 1.05, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {titleText}
              </div>
              {showRename ? (
                <button
                  type="button"
                  onClick={startRename}
                  aria-label="Переименовать"
                  title="Переименовать"
                  style={{ flexShrink: 0, width: 38, height: 38, borderRadius: t.radius.sm, background: hexToRgba(onAccent, 0.14), border: "none", color: onAccent, cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center" }}
                >
                  <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                    <path d="M12 20h9" />
                    <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z" />
                  </svg>
                </button>
              ) : null}
            </div>
          ) : null}
          {cfg.subtitleText ? (
            <div style={{ fontSize: t.font.sm, color: onAccentDim, marginTop: 10 }}>{cfg.subtitleText}</div>
          ) : null}
        </div>

        {cfg.showAppPicker && flow.platformOptions.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: t.space.sm }}>
            {cfg.platformTitle ? <div style={{ fontSize: t.font.xs, color: onAccentDim }}>{cfg.platformTitle}</div> : null}
            <DefaultChipPicker t={t} options={flow.platformOptions} value={flow.platform} onChange={flow.pickPlatform} palette={heroPalette} />
            {flow.available.length > 0 ? (
              <>
                {cfg.clientTitle ? <div style={{ fontSize: t.font.xs, color: onAccentDim, marginTop: t.space.xs }}>{cfg.clientTitle}</div> : null}
                <DefaultChipPicker
                  t={t}
                  options={flow.appOptions}
                  value={flow.selected?.id ?? ""}
                  onChange={flow.pickApp}
                  palette={heroPalette}
                />
              </>
            ) : (
              <div style={{ fontSize: t.font.xs, color: onAccentDim }}>{cfg.noClientsText}</div>
            )}
            {flow.downloadUrl && cfg.downloadLabel ? (
              <a
                href={isPreview ? undefined : flow.downloadUrl}
                target="_blank"
                rel="noreferrer"
                style={{ fontSize: t.font.xs, color: onAccent, textDecoration: "underline", alignSelf: "flex-start" }}
              >
                {cfg.downloadLabel}
              </a>
            ) : null}
          </div>
        ) : null}

        {link && cfg.showLink ? (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                borderRadius: CONTROL_R,
                background: hexToRgba(onAccent, 0.16),
                padding: "8px 8px 8px 16px",
                justifyContent: "space-between",
                gap: t.space.md,
              }}
            >
              <code className="showcase-sensitive" style={{ color: onAccent, fontFamily: t.monoFont, fontSize: t.font.sm, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{link}</code>
              {cfg.showCopy ? (
                <button type="button" onClick={onCopy} disabled={!link} style={{ ...heroBtn(onAccent, t.accent), minHeight: 38, padding: "0 18px", flexShrink: 0 }}>
                  {copied ? cfg.copiedLabel : cfg.copyLabel}
                </button>
              ) : null}
            </div>
          ) : null}

          <div style={{ display: "flex", gap: t.space.smPlus, flexWrap: "nowrap" }}>
            {cfg.showConnect && cfg.connectLabel ? (
              <a href={connectHref || undefined} {...(connectExternal ? { target: "_blank", rel: "noopener noreferrer" } : {})} onClick={(e) => { if (!connectHref) e.preventDefault(); }} aria-disabled={!link} style={{ ...heroBtn(onAccent, t.accent), flex: "1 1 0", minWidth: 0, padding: "0 12px", whiteSpace: "nowrap", textDecoration: "none", opacity: link ? 1 : 0.5, cursor: link ? "pointer" : "default" }}>
                {cfg.connectLabel}
              </a>
            ) : null}
            {cfg.showShare && cfg.shareLabel ? (
              <button type="button" onClick={onShare} disabled={!link} style={{ ...heroBtn("transparent", onAccent, `1px solid ${hexToRgba(onAccent, 0.45)}`), flex: "1 1 0", minWidth: 0, padding: "0 12px", whiteSpace: "nowrap", opacity: link ? 1 : 0.5, cursor: link ? "pointer" : "default" }}>
                {cfg.shareLabel}
              </button>
            ) : null}
            {cfg.showQr && cfg.qrLabel ? (
              <button type="button" onClick={onToggleQr} disabled={!activeKey} style={{ ...heroBtn("transparent", onAccent, `1px solid ${hexToRgba(onAccent, 0.45)}`), flex: "1 1 0", minWidth: 0, padding: "0 12px", whiteSpace: "nowrap", opacity: activeKey ? 1 : 0.5, cursor: activeKey ? "pointer" : "default" }}>
                {qrOpen ? cfg.qrCloseLabel : cfg.qrLabel}
              </button>
            ) : null}
          </div>
      </div>
      {qrModal}
    </div>,
    false,
    true,
  );
}
