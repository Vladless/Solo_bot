"use client";

import { useState } from "react";
import useSWR from "swr";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import {
  useDefaultTheme,
  DefaultPanel,
  f,
  parseBlockData,
  useBlockApi,
  apiFetch,
  iconSquareStyle,
  btnSm,
  btnSolid,
  btnSecondary,
} from ".";
import { usePagedItems, DefaultPagination } from "./DefaultPagination";
import { DefaultListRows, LIST_ROW_H } from "./layout";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import { useSectionScreen } from "@/app/(marketing)/landing/SectionScreenContext";
import { hexToRgba } from "@/components/constructor/utils";
import { formatGigabytes } from "@/lib/format-number";
import { formatDateFull } from "@/lib/format-date";

const SCHEMA = {
  panelHeader: f.str("Трафик и устройства"),
  trafficLabel: f.str("Трафик"),
  trafficUnitFormat: f.str("из {limit} ГБ"),
  trafficUnlimitedText: f.str("Безлимит"),
  devicesLabel: f.str("Устройства"),
  devicesSubLabel: f.str("подключено"),
  devicesUnlimitedText: f.str("∞"),
  unitGb: f.str("ГБ"),
  devicesListTitle: f.str("Подключённые устройства"),
  emptyDevicesText: f.str("Нет подключённых устройств"),
  deleteLabel: f.str("Отвязать"),
  deleteSuccessText: f.str("Устройство отвязано"),
  deleteErrorText: f.str("Не удалось отвязать устройство"),
  deleteBusyLabel: f.str("…"),
  noKeysText: f.str("Нет активной подписки"),
  loadingText: f.str("Загрузка..."),
  lowWarnPercent: f.num(90),
  pageSize: f.num(3),
  addonTrafficLabel: f.str("Докупить трафик"),
  addonDevicesLabel: f.str("Докупить устройства"),
};

type Device = {
  hwid: string;
  device_model: string;
  platform: string;
  os_version: string;
  user_agent: string;
  created_at: string;
};
type DevicesPayload = { devices: Device[]; total: number };

const MOCK_DEVICES: Device[] = [
  { hwid: "a1b2c3d4e5f6", device_model: "iPhone 14 Pro", platform: "iOS", os_version: "17.4", user_agent: "Streisand/1.2", created_at: "2026-05-01T10:00:00Z" },
  { hwid: "f6e5d4c3b2a1", device_model: "MacBook Air", platform: "macOS", os_version: "14.3", user_agent: "V2Box/2.0", created_at: "2026-04-20T09:00:00Z" },
];


function platformGlyph(platform: string): "phone" | "laptop" {
  const p = platform.toLowerCase();
  if (p.includes("ios") || p.includes("android") || p.includes("phone")) return "phone";
  return "laptop";
}


function Ring({
  pct,
  color,
  track,
  centerMain,
  centerSub,
  label,
  caption,
  inkDim,
  inkMute,
  ink,
  weightBold,
}: {
  pct: number;
  color: string;
  track: string;
  centerMain: string;
  centerSub: string;
  label: string;
  caption: string;
  inkDim: string;
  inkMute: string;
  ink: string;
  weightBold: number;
}) {
  const size = 116;
  const stroke = 11;
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ * (1 - Math.max(0, Math.min(100, pct)) / 100);
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10, flex: "1 1 0", minWidth: 0 }}>
      <div style={{ position: "relative", width: size, height: size }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ display: "block" }}>
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={track} strokeWidth={stroke} />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={color}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={circ}
            strokeDashoffset={offset}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
            style={{ transition: "stroke-dashoffset 640ms cubic-bezier(0.3,0.9,0.4,1), stroke 220ms ease" }}
          />
        </svg>
        <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 1 }}>
          <span style={{ fontSize: 28, fontWeight: weightBold, color: ink, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{centerMain}</span>
          {centerSub ? <span style={{ fontSize: 11, color: inkMute }}>{centerSub}</span> : null}
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
        <span style={{ fontSize: 14, fontWeight: weightBold, color: ink }}>{label}</span>
        <span style={{ fontSize: 12, color: inkDim }}>{caption}</span>
      </div>
    </div>
  );
}

export function DefaultUsageRingsBlockView({ block, context }: TypedBlockViewProps<"defaultUsageRings">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, SCHEMA);
  const isPreview = previewMode === true;

  const api = useBlockApi({ needs: ["activeSubscription"], disabled: isPreview });
  const mut = useAccountMutations();
  const details = isPreview ? null : api.activeDetails;
  const clientId = api.activeKey?.client_id ?? "";

  const { data: devData, mutate } = useSWR<DevicesPayload>(
    !isPreview && clientId ? `/api/keys/${encodeURIComponent(clientId)}/devices` : null,
    (url: string) => apiFetch<DevicesPayload>(url),
    { revalidateOnFocus: false, refreshInterval: 60_000 },
  );

  const limitGb = isPreview ? 200 : (typeof details?.traffic_limit_gb === "number" ? details.traffic_limit_gb : null);
  const usedGb = isPreview ? 84 : (typeof details?.used_traffic_gb === "number" ? details.used_traffic_gb : 0);
  const deviceLimit = isPreview ? 6 : (details?.device_limit ?? 0);
  const devices: Device[] = isPreview ? MOCK_DEVICES : (devData?.devices ?? []);
  const deviceCount = devices.length;

  const paged = usePagedItems(devices, cfg.pageSize);
  const [busyHwid, setBusyHwid] = useState<string | null>(null);
  const [status, setStatus] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const noKey = !isPreview && !api.keys.isLoading && api.activeKey === null;
  const loadingState = !isPreview && !noKey && (api.keys.isLoading || (api.activeKey !== null && api.activeDetails === undefined));

  const trafficUnlimited = limitGb === null || limitGb <= 0;
  const trafficPct = trafficUnlimited ? 100 : Math.max(0, Math.min(100, Math.round((usedGb / limitGb!) * 100)));
  const trafficLow = !trafficUnlimited && trafficPct >= Math.max(1, Math.min(100, cfg.lowWarnPercent));
  const trafficColor = trafficLow ? t.error : t.accent;

  const devUnlimited = deviceLimit <= 0;
  const devPct = devUnlimited ? (deviceCount > 0 ? 100 : 0) : Math.max(0, Math.min(100, Math.round((deviceCount / deviceLimit) * 100)));
  const devFull = !devUnlimited && deviceCount >= deviceLimit && deviceLimit > 0;
  const devColor = devFull ? t.error : t.accent;

  const sectionScreen = useSectionScreen();
  const openAddons = (focus: "traffic" | "device") => {
    if (isPreview) return;
    sectionScreen?.openScreen("tariffPanel", "addons", { focus });
    if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const trafficAddon = !trafficUnlimited && (isPreview || Boolean(details?.addons_traffic_enabled)) && trafficPct >= Math.max(1, Math.min(100, cfg.lowWarnPercent));
  const deviceAddon = (isPreview || Boolean(details?.addons_devices_enabled)) && devFull;

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
      loading={loadingState}
      loadingText={cfg.loadingText}
      empty={noKey}
      emptyText={cfg.noKeysText}
      bodyStyle={{ flex: "1 1 auto" }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: t.space.lg }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: t.space.lg, flexWrap: "wrap" }}>
          <Ring
            pct={trafficPct}
            color={trafficUnlimited ? t.accent : trafficColor}
            track={hexToRgba(trafficUnlimited ? t.accent : trafficColor, 0.16)}
            centerMain={trafficUnlimited ? "∞" : `${trafficPct}%`}
            centerSub={trafficUnlimited ? "" : `${formatGigabytes(usedGb)} ${cfg.unitGb}`}
            label={cfg.trafficLabel}
            caption={trafficUnlimited ? cfg.trafficUnlimitedText : cfg.trafficUnitFormat.replace("{limit}", formatGigabytes(limitGb ?? 0))}
            inkDim={t.inkDim}
            inkMute={t.inkMute}
            ink={t.ink}
            weightBold={t.weight.bold}
          />
          <Ring
            pct={devPct}
            color={devColor}
            track={hexToRgba(devColor, 0.16)}
            centerMain={devUnlimited ? String(deviceCount) : `${deviceCount}/${deviceLimit}`}
            centerSub={devUnlimited ? cfg.devicesUnlimitedText : cfg.devicesSubLabel}
            label={cfg.devicesLabel}
            caption={devUnlimited ? cfg.devicesUnlimitedText : `${deviceCount} ${cfg.devicesSubLabel}`}
            inkDim={t.inkDim}
            inkMute={t.inkMute}
            ink={t.ink}
            weightBold={t.weight.bold}
          />
        </div>

        {trafficAddon || deviceAddon ? (
          <div style={{ display: "flex", gap: t.space.sm, flexWrap: "wrap" }}>
            {trafficAddon ? (
              <button type="button" onClick={() => openAddons("traffic")} style={{ ...btnSolid(t), flex: "1 1 0", justifyContent: "center", cursor: "pointer" }}>
                {cfg.addonTrafficLabel}
              </button>
            ) : null}
            {deviceAddon ? (
              <button type="button" onClick={() => openAddons("device")} style={{ ...btnSolid(t), flex: "1 1 0", justifyContent: "center", cursor: "pointer" }}>
                {cfg.addonDevicesLabel}
              </button>
            ) : null}
          </div>
        ) : null}

        <div style={{ borderTop: `1px solid ${t.line}`, paddingTop: t.space.md }}>
          <div style={{ fontSize: t.font.xs, color: t.inkDim, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 6 }}>
            {cfg.devicesListTitle}
          </div>
          {deviceCount === 0 ? (
            <div style={{ color: t.inkMute, fontSize: t.font.sm, padding: "10px 0" }}>{cfg.emptyDevicesText}</div>
          ) : (
            <>
              <DefaultListRows t={t} slots={paged.pageSize} flipRef={paged.flipRef}>
              {paged.pageItems.map((dev, i) => {
                const subtitle = [dev.platform, dev.os_version].filter(Boolean).join(" ").trim() || dev.user_agent || dev.hwid;
                return (
                  <div
                    key={dev.hwid || i}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: t.space.md,
                      minHeight: t.px(LIST_ROW_H),
                    }}
                  >
                    <span style={iconSquareStyle(t, t.accent, 40)}>
                      {platformGlyph(dev.platform) === "phone" ? (
                        <svg width={17} height={17} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                          <rect x="6" y="2" width="12" height="20" rx="2.5" />
                          <line x1="10" y1="18.5" x2="14" y2="18.5" />
                        </svg>
                      ) : (
                        <svg width={17} height={17} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                          <rect x="3" y="4" width="18" height="12" rx="1.6" />
                          <line x1="2" y1="20" x2="22" y2="20" />
                        </svg>
                      )}
                    </span>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ fontSize: t.font.smPlus, fontWeight: t.weight.bold, color: t.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {dev.device_model || dev.platform || "Устройство"}
                      </div>
                      <div style={{ fontSize: t.font.xs, color: t.inkDim, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {subtitle}
                      </div>
                    </div>
                    {dev.created_at ? (
                      <span style={{ fontSize: t.font.xs, color: t.inkMute, whiteSpace: "nowrap" }}>{formatDateFull(dev.created_at)}</span>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => onDelete(dev.hwid)}
                      disabled={busyHwid === dev.hwid || isPreview}
                      style={{ ...btnSecondary(t), ...btnSm(t), color: t.error, cursor: busyHwid === dev.hwid ? "wait" : "pointer", opacity: busyHwid === dev.hwid ? 0.5 : 1 }}
                    >
                      {busyHwid === dev.hwid ? cfg.deleteBusyLabel : cfg.deleteLabel}
                    </button>
                  </div>
                );
              })}
              </DefaultListRows>
              {status ? (
                <div style={{ fontSize: t.font.xs, lineHeight: 1.5, color: status.kind === "ok" ? t.success : t.error }}>
                  {status.text}
                </div>
              ) : null}
              <DefaultPagination t={t} page={paged.page} totalPages={paged.totalPages} onPrev={paged.prev} onNext={paged.next} />
            </>
          )}
        </div>
      </div>
    </DefaultPanel>,
    false,
    true,
  );
}
