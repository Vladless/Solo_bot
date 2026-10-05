"use client";

import { notifyChannelValue } from "@/components/constructor/blockContent/cabinetKit/blockHelpers";
import { useEffect, useMemo, useRef, useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, toggleTrackStyle, toggleKnobStyle } from "./defaultTheme";
import { DefaultPanel } from "./DefaultPanel";
import { useBlockApi } from "@/components/constructor/blockContent/cabinetKit/apiHooksRegistry";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import { f, parseBlockData } from "@/components/constructor/blockContent/cabinetKit/blockSchema";
import type { CabinetNotifChannel } from "@/components/constructor/blockData/blocks";

const NOTIF_CHANNELS_SCHEMA = {
  notLinkedText: f.str("не привязан"),
  saveErrorText: f.str("Не удалось сохранить"),
  panelHeader: f.str("Каналы доставки"),
  panelHeaderHint: f.str(""),
};


export function DefaultNotifChannelsBlockView({ block, context }: TypedBlockViewProps<"defaultNotifChannels">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);

  const cfg = parseBlockData(d, NOTIF_CHANNELS_SCHEMA);
  const channelsRaw = d.channels;
  const channels: CabinetNotifChannel[] = useMemo(
    () => (Array.isArray(channelsRaw) ? (channelsRaw as CabinetNotifChannel[]) : []),
    [channelsRaw],
  );

  const api = useBlockApi({ needs: ["summary", "notificationPrefs"], disabled: previewMode === true, mock: previewMode === true });
  const summary = { data: api.summary.data };
  const mut = useAccountMutations();

  const [serverPrefs, setServerPrefs] = useState<Record<string, boolean> | null>(previewMode ? {} : null);
  const [persistError, setPersistError] = useState<string | null>(null);

  const savedPrefs = useRef<Record<string, boolean>>({});

  useEffect(() => {
    if (previewMode) return;
    if (api.notificationPrefs.data) {
      savedPrefs.current = api.notificationPrefs.data;
      setServerPrefs(api.notificationPrefs.data);
    } else if (api.notificationPrefs.error) {
      setServerPrefs({});
    }
  }, [previewMode, api.notificationPrefs.data, api.notificationPrefs.error]);

  const toggles = useMemo(() => {
    const next: Record<string, boolean> = {};
    channels.forEach((c, i) => {
      const key = c.kind ?? `idx-${i}`;
      if (serverPrefs && key in serverPrefs) next[key] = Boolean(serverPrefs[key]);
      else next[key] = Boolean(c.defaultOn);
    });
    return next;
  }, [channels, serverPrefs]);

  const persistTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (persistTimer.current) window.clearTimeout(persistTimer.current);
  }, []);

  function toggleChannel(key: string) {
    if (previewMode) return;
    const next = { ...(serverPrefs ?? {}), [key]: !toggles[key] };
    setServerPrefs(next);
    if (persistTimer.current) window.clearTimeout(persistTimer.current);
    persistTimer.current = window.setTimeout(() => {
      mut.updateNotificationPrefs(next)
        .then(() => {
          savedPrefs.current = next;
          setPersistError(null);
        })
        .catch(() => {
          setServerPrefs({ ...savedPrefs.current });
          setPersistError(cfg.saveErrorText);
        });
    }, 400);
  }

  return wrap(
    <DefaultPanel
      t={t}
      header={cfg.panelHeader}
      hint={cfg.panelHeaderHint || undefined}
      loading={previewMode !== true && (api.summary.isLoading || api.notificationPrefs.isLoading)}
      noBodyPadding
    >
      <div style={{ padding: "8px 26px 18px" }}>
        {channels.map((c, i) => {
          const value = notifyChannelValue(c.kind, summary.data, c.staticValue, cfg.notLinkedText);
          const key = c.kind ?? `idx-${i}`;
          const on = Boolean(toggles[key]) && (c.kind === "email" ? Boolean(summary.data?.email) : c.kind === "telegram" ? Boolean(summary.data?.linked_telegram) : true);
          const linked = c.kind === "email" ? Boolean(summary.data?.email) : c.kind === "telegram" ? Boolean(summary.data?.linked_telegram) : true;
          return (
            <div
              key={i}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "14px 0",
                borderBottom: i < channels.length - 1 ? `1px solid ${t.line}` : "none",
                gap: t.space.lg,
                opacity: linked ? 1 : 0.55,
              }}
            >
              <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
                <div style={{ fontSize: t.font.smPlus, fontWeight: t.weight.bold, color: t.ink }}>{c.name ?? c.kind ?? "—"}</div>
                <div style={{ fontSize: t.font.xsPlus, color: t.inkDim }}>{value}</div>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={on}
                disabled={!linked}
                onClick={() => toggleChannel(key)}
                style={{ ...toggleTrackStyle(t, on), cursor: linked ? "pointer" : "not-allowed" }}
              >
                <span style={toggleKnobStyle(t, on)} />
              </button>
            </div>
          );
        })}
        {persistError ? (
          <div style={{ padding: "10px 0 0", fontSize: t.font.xsPlus, color: t.error }}>{persistError}</div>
        ) : null}
      </div>
    </DefaultPanel>,
    false,
    true,
  );
}
