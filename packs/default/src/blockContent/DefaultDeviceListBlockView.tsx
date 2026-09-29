"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, iconSquareStyle, btnSolid, DefaultPanel, f, parseBlockData, useBlockApi, apiFetch, useIsMobile } from ".";
import { usePagedItems, DefaultPagination } from "./DefaultPagination";
import { DefaultBody, DefaultFooter, DefaultListItem, DefaultListRows, btnChip } from "./layout";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import { DefaultAddonsScreen, DEFAULT_ADDONS_LABELS } from "./DefaultAddonsScreen";
import { formatDateFull } from "@/lib/format-date";

type Device = {
  hwid: string;
  device_model: string;
  platform: string;
  os_version: string;
  user_agent: string;
  created_at: string;
};
type DevicesPayload = { devices: Device[]; total: number };

const SCHEMA = {
  panelHeader: f.str("Устройства"),
  hintFormat: f.str("{count} / {limit}"),
  emptyText: f.str("Нет подключённых устройств"),
  errorText: f.str("Не удалось загрузить устройства"),
  loadingText: f.str("Загрузка..."),
  noKeysText: f.str("Нет активной подписки"),
  deleteLabel: f.str("Отвязать"),
  deleteBusyLabel: f.str("…"),
  deleteSuccessText: f.str("Устройство отвязано"),
  deleteErrorText: f.str("Не удалось отвязать устройство"),
  showReset: f.bool(true),
  resetLabel: f.str("Сбросить все"),
  resetConfirmLabel: f.str("Точно сбросить?"),
  resetBusyLabel: f.str("Сброс…"),
  resetSuccessFormat: f.str("Сброшено устройств: {n}"),
  resetErrorText: f.str("Не удалось сбросить устройства"),
  pageSize: f.num(4),
  addonLabel: f.str("Докупить устройства"),
  addonsHint: f.str(""),
  addonsDevicesLabel: f.str("Устройства"),
  addonsDeviceUnitFormat: f.str("{n} устр."),
  addonsDeviceUnlimitedText: f.str("∞ устр."),
  addonsUnavailableText: f.str("Для текущего тарифа нет доступных опций"),
  addonsTotalLabel: f.str("Итого к оплате"),
  addonsSubmitLabel: f.str("Оформить"),
  addonsCancelLabel: f.str("Отмена"),
  deviceFallbackName: f.str("Устройство"),
};

const MOCK_DEVICES: Device[] = [
  { hwid: "a1b2c3d4e5f6", device_model: "iPhone 14 Pro", platform: "iOS", os_version: "17.4", user_agent: "Streisand/1.2", created_at: "2026-05-01T10:00:00Z" },
  { hwid: "f6e5d4c3b2a1", device_model: "MacBook Air", platform: "macOS", os_version: "14.3", user_agent: "V2Box/2.0", created_at: "2026-04-20T09:00:00Z" },
  { hwid: "112233445566", device_model: "Xiaomi 13", platform: "Android", os_version: "14", user_agent: "v2rayNG", created_at: "2026-04-12T18:30:00Z" },
];

function platformGlyph(platform: string): "phone" | "laptop" {
  const p = platform.toLowerCase();
  if (p.includes("ios") || p.includes("android") || p.includes("phone")) return "phone";
  return "laptop";
}


export function DefaultDeviceListBlockView({ block, context }: TypedBlockViewProps<"defaultDeviceList">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, SCHEMA);
  const isPreview = previewMode === true;

  const api = useBlockApi({ needs: ["activeSubscription", "activeTariff", "keyActions"], disabled: isPreview });
  const mut = useAccountMutations();
  const clientId = api.activeKey?.client_id ?? "";
  const limit = isPreview ? 5 : (api.activeDetails?.device_limit ?? 0);

  const { data, mutate, isLoading, error } = useSWR<DevicesPayload>(
    !isPreview && clientId ? `/api/keys/${encodeURIComponent(clientId)}/devices` : null,
    (url: string) => apiFetch<DevicesPayload>(url),
    { revalidateOnFocus: false, refreshInterval: 60_000 },
  );

  const devices: Device[] = isPreview ? MOCK_DEVICES : (data?.devices ?? []);
  const paged = usePagedItems(devices, cfg.pageSize);
  const [busyHwid, setBusyHwid] = useState<string | null>(null);
  const [status, setStatus] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [expandedHwid, setExpandedHwid] = useState<string | null>(null);
  const [resetArmed, setResetArmed] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const resetTimer = useRef<number | null>(null);
  const isMobile = useIsMobile();

  useEffect(() => () => {
    if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
  }, []);

  const noKey = !isPreview && !api.keys.isLoading && api.activeKey === null;
  const loadingState = !isPreview && !noKey && ((clientId === "" && api.keys.isLoading) || (clientId !== "" && isLoading && data === undefined));
  const isError = !isPreview && !noKey && clientId !== "" && !isLoading && data === undefined && error != null;
  const isEmpty = !isPreview && !noKey && clientId !== "" && !isLoading && !isError && devices.length === 0;

  const hint = (clientId !== "" || isPreview)
    ? cfg.hintFormat.replace("{count}", String(devices.length)).replace("{limit}", limit > 0 ? String(limit) : "∞")
    : undefined;

  const addonsEnabled = isPreview ? true : Boolean(api.activeDetails?.addons_devices_enabled);
  const showAddon = addonsEnabled && limit > 0 && devices.length >= limit;
  const deviceOptions: number[] = Array.isArray(api.activeTariff?.addon_device_options)
    ? api.activeTariff!.addon_device_options!
    : (Array.isArray(api.activeTariff?.device_options) ? api.activeTariff!.device_options : []);
  const [addonsOpen, setAddonsOpen] = useState(false);
  const openAddons = () => {
    if (isPreview) return;
    setAddonsOpen(true);
  };

  const resetAllowed = isPreview ? true : api.keyActions.data?.hwid_reset_enabled === true;
  const showReset = cfg.showReset && resetAllowed && devices.length > 0;

  const armReset = () => {
    if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
    setResetArmed(true);
    resetTimer.current = window.setTimeout(() => {
      resetTimer.current = null;
      setResetArmed(false);
    }, 5000);
  };

  const onReset = async () => {
    if (isPreview || resetBusy) return;
    if (!resetArmed) {
      armReset();
      return;
    }
    if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
    setResetArmed(false);
    setResetBusy(true);
    setStatus(null);
    try {
      const result = await mut.resetHwid(clientId);
      await mutate();
      setStatus({ kind: "ok", text: cfg.resetSuccessFormat.replace("{n}", String(result.reset_devices ?? 0)) });
    } catch (e) {
      setStatus({ kind: "error", text: e instanceof Error && e.message ? e.message : cfg.resetErrorText });
    } finally {
      setResetBusy(false);
    }
  };

  const onDelete = async (hwid: string) => {
    if (isPreview || busyHwid) return;
    setBusyHwid(hwid);
    setStatus(null);
    try {
      await mut.deleteDevice(clientId, hwid, { forceWeb: true });
      await mutate();
      setStatus({ kind: "ok", text: cfg.deleteSuccessText });
    } catch (e) {
      setStatus({ kind: "error", text: e instanceof Error && e.message ? e.message : cfg.deleteErrorText });
    } finally {
      setBusyHwid(null);
    }
  };

  return wrap(
    <DefaultPanel
      t={t}
      header={cfg.panelHeader}
      hint={hint}
      loading={false}
      skeleton={false}
      loadingText={cfg.loadingText}
      empty={noKey || isEmpty || isError}
      emptyText={noKey ? cfg.noKeysText : isError ? cfg.errorText : cfg.emptyText}
      noBodyPadding
    >
      {addonsOpen ? (
        <DefaultBody t={t}>
          <DefaultAddonsScreen
            t={t}
            focus="device"
            activeKey={api.activeKey}
            details={api.activeDetails}
            deviceOptions={deviceOptions}
            trafficOptions={[]}
            checkoutSlug="checkout"
            labels={{
              ...DEFAULT_ADDONS_LABELS,
              title: cfg.addonLabel,
              hint: cfg.addonsHint,
              devicesLabel: cfg.addonsDevicesLabel,
              deviceUnitFormat: cfg.addonsDeviceUnitFormat,
              deviceUnlimitedText: cfg.addonsDeviceUnlimitedText,
              totalLabel: cfg.addonsTotalLabel,
              submitLabel: cfg.addonsSubmitLabel,
              cancelLabel: cfg.addonsCancelLabel,
              unavailableText: cfg.addonsUnavailableText,
            }}
            onClose={() => setAddonsOpen(false)}
            isPreview={isPreview}
          />
        </DefaultBody>
      ) : (
      <DefaultBody t={t}>
        <DefaultListRows t={t} slots={paged.pageSize} flipRef={paged.flipRef} loading={loadingState}>
          {(loadingState ? [] : paged.pageItems).map((dev, i) => {
            const subtitle = [dev.platform, dev.os_version].filter(Boolean).join(" ").trim() || dev.user_agent || dev.hwid;
            const deviceName = dev.device_model || dev.platform || cfg.deviceFallbackName;
            const expanded = expandedHwid === dev.hwid;
            const textWrapStyle = expanded
              ? { whiteSpace: "normal" as const, overflowWrap: "anywhere" as const }
              : { overflow: "hidden" as const, textOverflow: "ellipsis" as const, whiteSpace: "nowrap" as const };
            const toggleExpand = () => setExpandedHwid((cur) => (cur === dev.hwid ? null : dev.hwid));
            return (
              <DefaultListItem
                key={dev.hwid || i}
                t={t}
                leading={
                  <span style={iconSquareStyle(t, t.accent, 40)}>
                    {platformGlyph(dev.platform) === "phone" ? (
                      <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                        <rect x="6" y="2" width="12" height="20" rx="2.5" />
                        <line x1="10" y1="18.5" x2="14" y2="18.5" />
                      </svg>
                    ) : (
                      <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                        <rect x="3" y="4" width="18" height="12" rx="1.6" />
                        <line x1="2" y1="20" x2="22" y2="20" />
                      </svg>
                    )}
                  </span>
                }
                title={
                  <span
                    onClick={isMobile ? toggleExpand : undefined}
                    role={isMobile ? "button" : undefined}
                    tabIndex={isMobile ? 0 : undefined}
                    onKeyDown={isMobile ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleExpand(); } } : undefined}
                    style={{ display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0, cursor: isMobile ? "pointer" : "default", ...textWrapStyle }}
                    title={!isMobile || expanded ? undefined : deviceName}
                  >
                    {deviceName}
                    {isMobile ? (
                      <svg
                        width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden
                        style={{ flexShrink: 0, color: t.inkMute, transform: expanded ? "rotate(180deg)" : "none", transition: "transform 160ms ease" }}
                      >
                        <polyline points="6 9 12 15 18 9" />
                      </svg>
                    ) : null}
                  </span>
                }
                meta={
                  <span style={{ ...textWrapStyle, display: "inline-block", maxWidth: "100%" }}>
                    {subtitle}
                    {expanded && isMobile && dev.created_at ? ` · ${formatDateFull(dev.created_at)}` : ""}
                  </span>
                }
                value={!isMobile && dev.created_at ? formatDateFull(dev.created_at) : undefined}
                valueColor={t.inkMute}
                action={
                  <button
                    type="button"
                    onClick={() => onDelete(dev.hwid)}
                    disabled={busyHwid === dev.hwid || isPreview}
                    style={{ ...btnChip(t, t.error), cursor: busyHwid === dev.hwid ? "wait" : "pointer", opacity: busyHwid === dev.hwid ? 0.5 : 1 }}
                  >
                    {busyHwid === dev.hwid ? cfg.deleteBusyLabel : cfg.deleteLabel}
                  </button>
                }
              />
            );
          })}
        </DefaultListRows>
        {status ? (
          <div style={{ fontSize: t.font.xs, lineHeight: 1.5, color: status.kind === "ok" ? t.success : t.error }}>
            {status.text}
          </div>
        ) : null}
        <DefaultFooter t={t} align={showAddon || showReset ? "between" : "stretch"}>
          <DefaultPagination t={t} page={paged.page} totalPages={paged.totalPages} onPrev={paged.prev} onNext={paged.next} />
          {showReset ? (
            <button
              type="button"
              onClick={() => { void onReset(); }}
              disabled={resetBusy}
              style={{ ...btnChip(t, t.error), flex: "0 0 auto", cursor: resetBusy ? "wait" : "pointer", opacity: resetBusy ? 0.5 : 1 }}
            >
              {resetBusy ? cfg.resetBusyLabel : resetArmed ? cfg.resetConfirmLabel : cfg.resetLabel}
            </button>
          ) : null}
          {showAddon ? (
            <button type="button" onClick={openAddons} style={{ ...btnSolid(t), flex: paged.totalPages > 1 || showReset ? "0 0 auto" : "1 1 auto", justifyContent: "center", cursor: "pointer" }}>
              {cfg.addonLabel}
            </button>
          ) : null}
        </DefaultFooter>
      </DefaultBody>
      )}
    </DefaultPanel>,
    false,
    true,
  );
}
