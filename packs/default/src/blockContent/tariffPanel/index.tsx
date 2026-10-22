"use client";

import { pluralize } from "@/lib/plural";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { mergeRefs } from "@/components/constructor/blockContent/pageFlip";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { hexToRgba } from "@/components/constructor/utils";
import { panelStyle, panelHeaderStyle, panelBodyStyle, btnRed, btnGhost, btnSm, pillStyle, pickContrast, blendHex, actionsSolo, CONTROL_R } from "..";
import { DefaultQrModal } from "../DefaultQrModal";
import { DefaultAddonsScreen } from "../DefaultAddonsScreen";
import { DefaultEmptyState } from "../layout";
import { PickOptionsView } from "./PickOptionsView";
import { SwitchView } from "./SwitchView";
import { useTariffPanel } from "./useTariffPanel";
import { useBlockPrefetch } from "@/lib/block-navigation";
import { AccountKeyAutopay } from "../../../../_shared/AccountKeyAutopay";



export function DefaultTariffPanelBlockView({ block, context, editMode }: TypedBlockViewProps<"defaultTariffPanel">) {
  const {
    wrap,
    t,
    decor,
    cfg,
    isPreview,
    list,
    activeKey,
    details,
    isLoadingKeys,
    hasKeys,
    setSelectedClientId,
    tariff,
    isMobile,
    copied,
    qrOpen,
    setQrOpen,
    qrImg,
    qrLoading,
    qrError,
    editingAlias,
    setEditingAlias,
    aliasDraft,
    setAliasDraft,
    renameBusy,
    renameError,
    connectLink,
    keyTitle,
    startRename,
    saveRename,
    onConnect,
    onCopy,
    onShare,
    onToggleQr,
    headerHint,
    pricePrimary,
    pricePeriodLabel,
    renewalText,
    features,
    resolvedSecondaryLabel,
    renewHref,
    tariffDeviceOptions,
    tariffTrafficOptions,
    hasAnyAddonOption,
    addonsHref,
    showPrimaryButton,
    renewBusy,
    primaryLabelDisplay,
    onRenewClick,
    panelMode,
    screenFlip,
    revealRef,
    panelModeFadeClass,
    pickedTariff,
    pickDeviceDraft,
    setPickDeviceDraft,
    pickTrafficDraft,
    setPickTrafficDraft,
    submitBusy,
    pickPriceText,
    onAddonsClick,
    closeAddons,
    switchTariffsList,
    switchListLoading,
    switchLoadedFade,
    switchSortedTariffs,
    switchPaged,
    switchCards,
    switchCompact,
    switchPager,
    setSwitchGrid,
    onSwitchClick,
    switchHoverId,
    setSwitchHoverId,
    closeSwitch,
    onPickTariff,
    closePickOptions,
    onPickOptionsSubmit,
  } = useTariffPanel({ block, context });
  const prefetch = useBlockPrefetch();

  const {
    panelHeader, noKeysText, buyTariffLabel, labelDevices, checkoutSlug,
    addonsDialogTitle, addonsDialogHint, addonsDevicesLabel, addonsTrafficLabel,
    addonsTotalLabel, addonsSubmitLabel, addonsCancelLabel, addonsUnavailableText,
    addonsDeviceUnitFormat, addonsDeviceUnlimitedText, addonsTrafficUnitFormat, addonsTrafficUnlimitedText,
    switchTariffLabel, switchTariffHref, switchTariffMode, switchTariffBackLabel, switchTariffEmptyText,
    switchTariffCurrentBadge, switchTariffSelectLabel, switchTariffUpgradeLabel, switchTariffPerDayLabel,
    switchTariffPageLabel,
  } = cfg;

  const tabsBar = !isPreview && list.length > 1 ? (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 4 }}>
      {list.map((k) => {
        const isActive = activeKey?.client_id === k.client_id;
        const label = k.alias || k.email.split("@")[0] || k.client_id.slice(0, 6);
        return (
          <button   key={k.client_id}
            type="button"
            onClick={() => setSelectedClientId(k.client_id)}
            style={{
              ...btnGhost(t),
              ...btnSm(t),
              borderColor: isActive ? t.accent : t.line,
              color: isActive ? t.accent : t.inkDim,
              cursor: "pointer",
            }}
          >
            {label}
          </button>
        );
      })}
    </div>
  ) : null;

  const renderPickOptionsView = () => (
    <PickOptionsView
      t={t}
      pickedTariff={pickedTariff}
      pickDeviceDraft={pickDeviceDraft}
      setPickDeviceDraft={setPickDeviceDraft}
      pickTrafficDraft={pickTrafficDraft}
      setPickTrafficDraft={setPickTrafficDraft}
      pickPriceText={pickPriceText}
      submitBusy={submitBusy}
      addonsSubmitLabel={addonsSubmitLabel}
      addonsCancelLabel={addonsCancelLabel}
      addonsDevicesLabel={addonsDevicesLabel}
      addonsTrafficLabel={addonsTrafficLabel}
      switchTariffBackLabel={switchTariffBackLabel}
      closePickOptions={closePickOptions}
      onPickOptionsSubmit={onPickOptionsSubmit}
    />
  );

  const renderSwitchView = () => (
    <SwitchView
      t={t}
      activeKey={activeKey}
      isPreview={isPreview}
      switchSortedTariffs={switchSortedTariffs}
      switchTariffsList={switchTariffsList}
      switchListLoading={switchListLoading}
      switchLoadedFade={switchLoadedFade}
      switchCompact={switchCompact}
      listCols={switchPaged.cols}
      cardCols={switchCards.cols}
      pageFlipRef={switchPager.flipRef}
      pageItems={switchPager.pageItems}
      perPage={switchPager.perPage}
      page={switchPager.page}
      totalPages={switchPager.totalPages}
      setPage={switchPager.setPage}
      switchHoverId={switchHoverId}
      setSwitchHoverId={setSwitchHoverId}
      setSwitchGrid={setSwitchGrid}
      switchTariffPageLabel={switchTariffPageLabel}
      switchTariffBackLabel={switchTariffBackLabel}
      switchTariffEmptyText={switchTariffEmptyText}
      switchTariffCurrentBadge={switchTariffCurrentBadge}
      switchTariffSelectLabel={switchTariffSelectLabel}
      switchTariffUpgradeLabel={switchTariffUpgradeLabel}
      switchTariffPerDayLabel={switchTariffPerDayLabel}
      closeSwitch={closeSwitch}
      onPickTariff={onPickTariff}
    />
  );

  const body = (() => {
    if (isLoadingKeys) {
      return <div style={{ color: t.inkDim, fontSize: t.font.sm }}>Загрузка...</div>;
    }
    if (!isPreview && !hasKeys && panelMode === "main") {
      return (
        <DefaultEmptyState
          t={t}
          text={noKeysText}
          action={
            <a
              href={switchTariffMode === "page" ? (switchTariffHref || "/tariffs") : "#"}
              onClick={onSwitchClick}
              style={{ ...btnRed(t), textDecoration: "none", cursor: "pointer", ...actionsSolo(isMobile) }}
            >
              {buyTariffLabel}
            </a>
          }
        />
      );
    }
    if (panelMode === "addons") {
      return (
        <DefaultAddonsScreen
          t={t}
          focus="both"
          activeKey={activeKey}
          details={details}
          deviceOptions={tariffDeviceOptions}
          trafficOptions={tariffTrafficOptions}
          checkoutSlug={checkoutSlug}
          labels={{
            title: addonsDialogTitle,
            hint: addonsDialogHint,
            devicesLabel: addonsDevicesLabel,
            trafficLabel: addonsTrafficLabel,
            totalLabel: addonsTotalLabel,
            submitLabel: addonsSubmitLabel,
            cancelLabel: addonsCancelLabel,
            unavailableText: addonsUnavailableText,
            deviceUnitFormat: addonsDeviceUnitFormat,
            deviceUnlimitedText: addonsDeviceUnlimitedText,
            trafficUnitFormat: addonsTrafficUnitFormat,
            trafficUnlimitedText: addonsTrafficUnlimitedText,
          }}
          onClose={closeAddons}
          isPreview={isPreview}
        />
      );
    }
    if (panelMode === "pickOptions") {
      return renderPickOptionsView();
    }
    if (panelMode === "switch") {
      return renderSwitchView();
    }
    return (
      <>
        {tabsBar}
        <div style={{ fontSize: t.font.giant, fontWeight: t.weight.bold, letterSpacing: "-0.02em", lineHeight: 1, color: t.ink, fontFamily: t.monoFont }}>
          {pricePrimary} <span style={{ fontSize: t.font.xl, color: t.inkDim }}>{pricePeriodLabel}</span>
        </div>
        {renewalText ? (
          <div style={{ fontSize: t.font.xsPlus, color: t.inkDim, letterSpacing: t.tracking.wide, textTransform: "none" }}>{renewalText}</div>
        ) : null}
        <div style={{ height: 1, background: t.line }} />
        <ul style={{ listStyle: "none", padding: 0, margin: 0, fontSize: t.font.sm, display: "flex", flexDirection: "column", gap: t.space.sm, color: t.inkDim }}>
          {features.map((f, i) => (
            <li key={i} style={{ display: "flex", justifyContent: "space-between", gap: t.space.md }}>
              <span>{f.label ?? ""}</span>
              <b style={{ color: t.ink, fontWeight: t.weight.bold, textAlign: "right", overflow: "hidden", textOverflow: "ellipsis" }}>{f.value ?? ""}</b>
            </li>
          ))}
        </ul>
        <div
          onPointerEnter={() => { prefetch(renewHref); if (switchTariffMode === "page") prefetch(switchTariffHref || "/tariffs"); }}
          onFocusCapture={() => { prefetch(renewHref); }}
          style={{ display: "flex", gap: t.space.smPlus, marginTop: "auto", paddingTop: 8, flexWrap: "wrap" }}
        >
          {showPrimaryButton ? (
            <a href={renewHref} onClick={onRenewClick} style={{ ...btnRed(t), textDecoration: "none", opacity: renewBusy ? 0.7 : 1 }}>
              {primaryLabelDisplay}
            </a>
          ) : null}
          {(hasAnyAddonOption || isPreview) && (hasKeys || isPreview) ? (
            <a     href={addonsHref}
              onClick={onAddonsClick}
              style={{ ...btnGhost(t), textDecoration: "none", cursor: "pointer" }}
            >
              {resolvedSecondaryLabel || "Докупить"}
            </a>
          ) : null}
          {(switchTariffLabel && (hasKeys || isPreview)) ? (
            <a     href={switchTariffMode === "page" ? (switchTariffHref || "/tariffs") : "#"}
              onClick={onSwitchClick}
              style={{ ...btnGhost(t), textDecoration: "none", cursor: "pointer" }}
            >
              {switchTariffLabel}
            </a>
          ) : null}
        </div>
      </>
    );
  })();

  const heroMode = panelMode === "main" && (hasKeys || isPreview);
  if (heroMode) {
    const onAccent = pickContrast(t.accent);
    const onAccentDim = hexToRgba(onAccent, 0.72);
    const gradient = `linear-gradient(135deg, ${blendHex(t.accent, "#FFFFFF", 0.06)}, ${blendHex(t.accent, "#000000", 0.22)})`;

    const periodWords = (days: number): string => {
      if (!Number.isFinite(days) || days <= 0) return "";
      if (days % 365 === 0) { const y = days / 365; return y === 1 ? "1 год" : `${y} года`; }
      if (days % 30 === 0) { const m = days / 30; return `${m} мес`; }
      return `${days} дн`;
    };
    const subParts: string[] = [];
    const subDuration = isPreview ? 365 : (tariff?.duration_days ?? 0);
    if (subDuration > 0) subParts.push(periodWords(subDuration));
    const subDevices = isPreview ? 5 : (details?.device_limit ?? null);
    if (typeof subDevices === "number" && subDevices > 0) subParts.push(`${subDevices} ${labelDevices.toLowerCase()}`);
    const subTraffic = isPreview ? null : (details?.traffic_limit_gb ?? null);
    if (typeof subTraffic === "number" && subTraffic > 0) subParts.push(`${subTraffic} ГБ`);
    const subline = subParts.length > 0
      ? subParts.join(" · ")
      : features.map((ft) => ft.value).filter((v): v is string => typeof v === "string" && v.trim() !== "").slice(0, 3).join(" · ");

    const totalDays = isPreview ? 365 : (tariff?.duration_days ?? 0);
    const expiry = activeKey?.expiry_time ?? null;
    const daysWord = (n: number): string => pluralize(n, cfg.dayForms);
    const fillProgressFormat = (used: number, total: number, left: number): string => cfg.progressActiveFormat
      .replace("{used}", String(used))
      .replace("{total}", String(total))
      .replace("{left}", String(left))
      .replace("{leftDays}", `${left} ${daysWord(left)}`);
    let progressLabel = "";
    let progressPct = 0;
    let progressLowLeft = false;
    if (isPreview) {
      progressPct = 72;
      progressLabel = fillProgressFormat(263, 365, 102);
    } else if (expiry && totalDays > 0) {
      const daysLeft = Math.max(0, Math.ceil((expiry - Date.now()) / 86_400_000));
      const used = Math.max(0, Math.min(totalDays, totalDays - daysLeft));
      progressPct = Math.max(2, Math.min(100, Math.round((used / totalDays) * 100)));
      progressLowLeft = daysLeft / totalDays <= 0.1;
      progressLabel = fillProgressFormat(used, totalDays, daysLeft);
    }

    const heroBtn = (bg: string, color: string, border?: string): React.CSSProperties => ({
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      minHeight: 44,
      padding: "0 22px",
      borderRadius: CONTROL_R,
      background: bg,
      color,
      border: border ?? "none",
      fontFamily: t.monoFont,
      fontWeight: t.weight.bold,
      fontSize: t.font.sm,
      textDecoration: "none",
      cursor: "pointer",
    });

    const showConnectSection = cfg.showConnectSection && (isPreview || connectLink !== "");
    const heroDivider = <div aria-hidden style={{ width: 1, alignSelf: "stretch", background: hexToRgba(onAccent, 0.16), flexShrink: 0 }} />;

    const chipsRow = list.length > 1 && !isPreview ? (
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {list.map((k) => {
          const isActive = activeKey?.client_id === k.client_id;
          const label = k.alias || k.email.split("@")[0] || k.client_id.slice(0, 6);
          return (
            <button
              key={k.client_id}
              type="button"
              onClick={() => setSelectedClientId(k.client_id)}
              style={{ ...heroBtn(isActive ? hexToRgba(onAccent, 0.22) : hexToRgba(onAccent, 0.1), onAccent), minHeight: 30, padding: "0 12px", fontSize: t.font.xs }}
            >
              {label}
            </button>
          );
        })}
      </div>
    ) : null;

    const connectHeader = (
      <div>
        <div style={{ fontSize: t.font.smPlus, color: onAccentDim, marginBottom: 10 }}>{cfg.connectHint}</div>
        {editingAlias ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ display: "flex", alignItems: "center", gap: t.space.sm }}>
              <input
                value={aliasDraft}
                onChange={(e) => setAliasDraft(e.target.value)}
                maxLength={10}
                autoFocus
                placeholder={cfg.renamePlaceholder}
                onKeyDown={(e) => { if (e.key === "Enter") void saveRename(); if (e.key === "Escape") setEditingAlias(false); }}
                style={{ flex: "1 1 auto", minWidth: 0, height: 40, boxSizing: "border-box", background: hexToRgba(onAccent, 0.16), border: "none", borderRadius: t.radius.sm, color: onAccent, fontFamily: t.monoFont, fontSize: t.font.xl, fontWeight: t.weight.bold, letterSpacing: "-0.02em", padding: "0 14px", outline: "none" }}
              />
              <button type="button" onClick={() => void saveRename()} disabled={renameBusy} aria-label="Сохранить" style={{ flexShrink: 0, width: 40, height: 40, borderRadius: t.radius.sm, background: onAccent, color: t.accent, border: "none", cursor: "pointer", fontSize: 17, fontWeight: t.weight.bold, opacity: renameBusy ? 0.6 : 1 }}>✓</button>
              <button type="button" onClick={() => setEditingAlias(false)} aria-label="Отмена" style={{ flexShrink: 0, width: 40, height: 40, borderRadius: t.radius.sm, background: "transparent", color: onAccent, border: `1px solid ${hexToRgba(onAccent, 0.45)}`, cursor: "pointer", fontSize: 15 }}>✕</button>
            </div>
            {renameError ? <div style={{ fontSize: t.font.sm, color: onAccent }}>{renameError}</div> : null}
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: t.space.md, minWidth: 0 }}>
            <div style={{ fontSize: t.font.giant, fontWeight: t.weight.bold, letterSpacing: "-0.02em", lineHeight: 1.05, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {keyTitle}
            </div>
            {!isPreview && !!activeKey && cfg.showRename ? (
              <button
                type="button"
                onClick={startRename}
                aria-label="Переименовать"
                title="Переименовать"
                style={{ flexShrink: 0, width: 36, height: 36, borderRadius: t.radius.sm, background: hexToRgba(onAccent, 0.14), border: "none", color: onAccent, cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center" }}
              >
                <svg width={15} height={15} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M12 20h9" />
                  <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z" />
                </svg>
              </button>
            ) : null}
          </div>
        )}
        {cfg.connectSubtitleText ? (
          <div style={{ fontSize: t.font.sm, color: onAccentDim, marginTop: 8 }}>{cfg.connectSubtitleText}</div>
        ) : null}
      </div>
    );

    const linkWell = connectLink && cfg.showLink ? (
      <div style={{ display: "flex", alignItems: "center", borderRadius: CONTROL_R, background: hexToRgba(onAccent, 0.14), padding: "8px 8px 8px 16px", justifyContent: "space-between", gap: t.space.md }}>
        <code className="showcase-sensitive" style={{ color: onAccent, fontFamily: t.monoFont, fontSize: t.font.sm, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{connectLink}</code>
        {cfg.showCopy ? (
          <button type="button" onClick={onCopy} style={{ ...heroBtn(onAccent, t.accent), minHeight: 36, padding: "0 16px", flexShrink: 0 }}>
            {copied ? cfg.copiedLabel : cfg.copyLabel}
          </button>
        ) : null}
      </div>
    ) : null;

    const connectBtn = cfg.showConnect && cfg.connectLabel ? (
      <button
        type="button"
        onClick={onConnect}
        disabled={!connectLink}
        style={{ ...heroBtn(onAccent, t.accent), minHeight: isMobile ? 52 : 44, flex: isMobile ? "0 0 auto" : "1 1 0", width: isMobile ? "100%" : undefined, minWidth: 0, padding: "0 12px", whiteSpace: "nowrap", fontSize: isMobile ? t.font.md : t.font.sm, opacity: connectLink || isPreview ? 1 : 0.5, cursor: connectLink || isPreview ? "pointer" : "default" }}
      >
        {cfg.connectLabel}
      </button>
    ) : null;
    const shareBtn = cfg.showShare && cfg.shareLabel ? (
      <button type="button" onClick={onShare} disabled={!connectLink} style={{ ...heroBtn("transparent", onAccent, `1px solid ${hexToRgba(onAccent, 0.45)}`), flex: "1 1 0", minWidth: 0, padding: "0 12px", whiteSpace: "nowrap", opacity: connectLink || isPreview ? 1 : 0.5, cursor: connectLink || isPreview ? "pointer" : "default" }}>
        {cfg.shareLabel}
      </button>
    ) : null;
    const qrBtn = cfg.showQr && cfg.qrLabel ? (
      <button type="button" onClick={onToggleQr} disabled={!activeKey && !isPreview} style={{ ...heroBtn("transparent", onAccent, `1px solid ${hexToRgba(onAccent, 0.45)}`), flex: "1 1 0", minWidth: 0, padding: "0 12px", whiteSpace: "nowrap", opacity: activeKey || isPreview ? 1 : 0.5, cursor: activeKey || isPreview ? "pointer" : "default" }}>
        {qrOpen ? cfg.qrCloseLabel : cfg.qrLabel}
      </button>
    ) : null;

    const tariffPill = (
      <span style={{ ...pillStyle(t, onAccent), background: hexToRgba(onAccent, 0.18), color: onAccent, alignSelf: "flex-start" }}>
        ★ {cfg.currentTariffLabel}
      </span>
    );
    const tariffTitle = <div style={{ fontSize: t.font.giant, fontWeight: t.weight.bold, letterSpacing: "-0.02em", lineHeight: 1 }}>{headerHint}</div>;
    const sublineEl = subline ? <div style={{ fontSize: t.font.smPlus, color: onAccentDim }}>{subline}</div> : null;
    const progressEl = progressLabel ? (
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 4 }}>
        <div style={{ height: 8, borderRadius: 999, background: hexToRgba(onAccent, 0.22), overflow: "hidden" }}>
          <div style={{ width: `${progressPct}%`, height: "100%", borderRadius: 999, background: progressLowLeft ? t.error : onAccent, transition: "background 200ms ease" }} />
        </div>
        <div style={{ fontSize: t.font.xs, color: onAccentDim }}>{progressLabel}</div>
      </div>
    ) : null;
    const tariffButtons = (
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: "auto", paddingTop: 8 }}>
      {(hasAnyAddonOption || isPreview) && (hasKeys || isPreview) && cfg.renewalUpsellHint ? (
        <span style={{ fontSize: t.font.xs, color: onAccentDim }}>{cfg.renewalUpsellHint}</span>
      ) : null}
      <div style={{ display: "flex", gap: t.space.smPlus, flexWrap: "nowrap" }}>
        {showPrimaryButton ? (
          <a href={renewHref} onClick={onRenewClick} style={{ ...heroBtn(onAccent, t.accent), flex: "1 1 0", minWidth: 0, padding: "0 12px", whiteSpace: "nowrap", opacity: renewBusy ? 0.7 : 1 }}>{primaryLabelDisplay}</a>
        ) : null}
        {(switchTariffLabel && (hasKeys || isPreview)) ? (
          <a
            href={switchTariffMode === "page" ? (switchTariffHref || "/tariffs") : "#"}
            onClick={onSwitchClick}
            style={{ ...heroBtn("transparent", onAccent, `1px solid ${hexToRgba(onAccent, 0.45)}`), flex: "1 1 0", minWidth: 0, padding: "0 12px", whiteSpace: "nowrap" }}
          >
            {switchTariffLabel}
          </a>
        ) : null}
        {(hasAnyAddonOption || isPreview) && (hasKeys || isPreview) ? (
          <a
            href={addonsHref}
            onClick={onAddonsClick}
            style={{ ...heroBtn("transparent", onAccent, `1px solid ${hexToRgba(onAccent, 0.45)}`), flex: "1 1 0", minWidth: 0, padding: "0 12px", whiteSpace: "nowrap" }}
          >
            {resolvedSecondaryLabel || "Докупить"}
          </a>
        ) : null}
      </div>
      </div>
    );

    const mobileKeyRow = showConnectSection ? (
      editingAlias ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", alignItems: "center", gap: t.space.sm }}>
            <input
              value={aliasDraft}
              onChange={(e) => setAliasDraft(e.target.value)}
              maxLength={10}
              autoFocus
              placeholder={cfg.renamePlaceholder}
              onKeyDown={(e) => { if (e.key === "Enter") void saveRename(); if (e.key === "Escape") setEditingAlias(false); }}
              style={{ flex: "1 1 auto", minWidth: 0, height: 36, boxSizing: "border-box", background: hexToRgba(onAccent, 0.16), border: "none", borderRadius: t.radius.sm, color: onAccent, fontFamily: t.monoFont, fontSize: t.font.md, fontWeight: t.weight.bold, padding: "0 12px", outline: "none" }}
            />
            <button type="button" onClick={() => void saveRename()} disabled={renameBusy} aria-label="Сохранить" style={{ flexShrink: 0, width: 36, height: 36, borderRadius: t.radius.sm, background: onAccent, color: t.accent, border: "none", cursor: "pointer", fontSize: 15, fontWeight: t.weight.bold, opacity: renameBusy ? 0.6 : 1 }}>✓</button>
            <button type="button" onClick={() => setEditingAlias(false)} aria-label="Отмена" style={{ flexShrink: 0, width: 36, height: 36, borderRadius: t.radius.sm, background: "transparent", color: onAccent, border: `1px solid ${hexToRgba(onAccent, 0.45)}`, cursor: "pointer", fontSize: 14 }}>✕</button>
          </div>
          {renameError ? <div style={{ fontSize: t.font.sm, color: onAccent }}>{renameError}</div> : null}
        </div>
      ) : (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: t.space.sm, minWidth: 0 }}>
          {tariffPill}
          <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
            <span style={{ fontSize: t.font.md, fontWeight: t.weight.bold, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{keyTitle}</span>
            {!isPreview && !!activeKey && cfg.showRename ? (
              <button
                type="button"
                onClick={startRename}
                aria-label="Переименовать"
                title="Переименовать"
                style={{ flexShrink: 0, width: 28, height: 28, borderRadius: 8, background: hexToRgba(onAccent, 0.14), border: "none", color: onAccent, cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center" }}
              >
                <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M12 20h9" />
                  <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z" />
                </svg>
              </button>
            ) : null}
          </div>
        </div>
      )
    ) : tariffPill;

    const heroBody = !showConnectSection ? (
      <>
        {chipsRow}
        {tariffPill}
        {tariffTitle}
        {sublineEl}
        {progressEl}
        {tariffButtons}
      </>
    ) : isMobile ? (
      <>
        {chipsRow}
        {mobileKeyRow}
        {tariffTitle}
        {sublineEl}
        {progressEl}
        {connectBtn}
        {shareBtn || qrBtn ? <div style={{ display: "flex", gap: t.space.smPlus }}>{shareBtn}{qrBtn}</div> : null}
        {tariffButtons}
      </>
    ) : (
      <>
        <div style={{ display: "flex", gap: t.space.xl, flex: "1 1 auto", minHeight: 0 }}>
          <div style={{ flex: "1.15 1 0", minWidth: 0, display: "flex", flexDirection: "column", justifyContent: "space-between", gap: t.space.md }}>
            <div style={{ display: "flex", flexDirection: "column", gap: t.space.md }}>
              {chipsRow}
              {connectHeader}
            </div>
            {linkWell}
            <div style={{ display: "flex", gap: t.space.smPlus, flexWrap: "nowrap" }}>
              {connectBtn}
              {shareBtn}
              {qrBtn}
            </div>
          </div>
          {heroDivider}
          <div style={{ flex: "1 1 0", minWidth: 0, display: "flex", flexDirection: "column", gap: t.space.md }}>
            {tariffPill}
            {tariffTitle}
            {sublineEl}
            {progressEl}
            {tariffButtons}
          </div>
        </div>
      </>
    );

    const expiryUrgentBanner = (() => {
      const exp = activeKey?.expiry_time;
      if (isPreview || !exp) return null;
      const dleft = Math.ceil((exp - Date.now()) / 86_400_000);
      if (dleft < 0 || dleft > cfg.expiryUrgentDays) return null;
      const a = dleft % 100;
      const b = dleft % 10;
      const w = a > 10 && a < 20 ? "дней" : b === 1 ? "день" : b > 1 && b < 5 ? "дня" : "дней";
      const text = cfg.expiryUrgentFormat.replace("{leftDays}", `${dleft} ${w}`);
      return (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "10px 14px",
            borderRadius: t.radius.sm,
            background: hexToRgba(onAccent, 0.14),
            border: `1px solid ${hexToRgba(onAccent, 0.28)}`,
            color: onAccent,
            fontFamily: t.monoFont,
            fontWeight: t.weight.bold,
            fontSize: t.font.xs,
            lineHeight: 1.35,
          }}
        >
          {text}
        </div>
      );
    })();

    return wrap(
      <div key="hero-main" ref={mergeRefs(revealRef, screenFlip)} className={`${ELEMENT_FILL_CLASS}${panelModeFadeClass}`} style={{ ...panelStyle(t), background: gradient, color: onAccent, position: "relative", ...decor }}>
        <div aria-hidden style={{ position: "absolute", top: -60, right: -40, width: 200, height: 200, borderRadius: 999, background: hexToRgba(onAccent, 0.08), pointerEvents: "none" }} />
        <div aria-hidden style={{ position: "absolute", bottom: -70, right: 40, width: 160, height: 160, borderRadius: 999, background: hexToRgba(onAccent, 0.07), pointerEvents: "none" }} />
        <div style={{ ...panelBodyStyle(t), position: "relative", zIndex: 1, height: "100%", display: "flex", flexDirection: "column", gap: t.space.md, overflowY: "auto" }}>
          {expiryUrgentBanner}
          <div style={{ display: "flex", flexDirection: "column", gap: t.space.md, flex: "1 0 auto" }}>{heroBody}</div>
          <AccountKeyAutopay clientId={activeKey?.client_id} disabled={isPreview || editMode} embedded style={{ color: onAccent, fontFamily: t.monoFont, fontSize: t.font.sm, flexShrink: 0 }} />
        </div>
        <DefaultQrModal
          t={t}
          open={qrOpen}
          onClose={() => setQrOpen(false)}
          qrImg={qrImg}
          loading={qrLoading}
          error={qrError}
          caption={cfg.qrShareHint}
          title={cfg.qrLabel}
          closeLabel={cfg.qrCloseLabel}
          loadingText={cfg.qrLoadingText}
        />
      </div>,
      false,
      true,
    );
  }

  return wrap(
    <div ref={mergeRefs(revealRef, screenFlip)} className={ELEMENT_FILL_CLASS} style={{ ...panelStyle(t), ...decor }}>
      <div style={panelHeaderStyle(t)}>
        <b style={{ color: t.ink, fontWeight: t.weight.bold }}>{panelHeader}</b>
        <span style={{ color: t.accent }}>{headerHint}</span>
      </div>
      <div className={panelModeFadeClass.trim() || undefined} style={{ ...panelBodyStyle(t), display: "flex", flexDirection: "column", gap: t.space.mdPlus }}>{body}</div>
    </div>,
    false,
    true,
  );
}
