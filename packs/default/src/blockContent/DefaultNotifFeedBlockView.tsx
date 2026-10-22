"use client";

import React, { useEffect, useRef, useState } from "react";
import { useSwipeToDelete } from "@/components/constructor/blockContent/notifications/useSwipeToDelete";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { ELEMENT_FILL_CLASS } from "@/components/constructor/blockContent/blockLayout";
import { useNotifications, useMockNotifications } from "@/components/constructor/blockContent/notifications/useNotifications";
import { NotificationOpenButton } from "@/components/constructor/blockContent/notifications/NotificationOpenButton";
import { useNotificationOpen } from "@/components/constructor/blockContent/notifications/useNotificationOpen";
import type { NotificationTarget } from "@/components/constructor/blockContent/notifications/notificationTarget";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";
import { useDefaultTheme, panelStyle, linkBtn, iconSquareStyle } from "./defaultTheme";
import { SlidingSegmented } from "./SlidingSegmented";
import { useLoadedReveal } from "@/components/constructor/blockContent/useLoadedFade";
import { hexToRgba } from "@/components/constructor/utils";
import { mixHexColors } from "@/lib/visual-settings/color-math";
import { f, parseBlockData } from "@/components/constructor/blockContent/cabinetKit/blockSchema";

const NOTIF_FEED_SCHEMA = {
  panelHeader: f.str(""),
  filterAllFormat: f.str("Все · {count}"),
  filterUnreadFormat: f.str("Новые · {count}"),
  filterImportantFormat: f.str("Важные · {count}"),
  markAllLabel: f.str("Прочитать все"),
  markAllBusyLabel: f.str("..."),
  markAllSuccessText: f.str("Прочитано"),
  markAllErrorText: f.str("Не удалось"),
  clearAllLabel: f.str("Удалить все"),
  clearAllConfirmLabel: f.str("Точно?"),
  emptyText: f.str("Уведомлений нет"),
  unauthenticatedText: f.str("Войдите в аккаунт"),
  loadingText: f.str("Загрузка..."),
  maxItems: f.num(30),
  deleteLabel: f.str("Удалить"),
  expandLabel: f.str("Подробнее"),
  collapseLabel: f.str("Свернуть"),
  openLabel: f.str("Перейти"),
};

type FilterKind = "all" | "unread" | "important";

const COLLAPSED_LINES = 3;

const NOTIF_CLS: Record<string, "ok" | "warn" | "err" | "info"> = {
  payment_success: "ok",
  payment: "ok",
  key_created: "ok",
  key_renewed: "ok",
  gift_received: "ok",
  gift_redeemed: "ok",
  subscription_expiring: "warn",
  key_expiry: "warn",
  key_expired: "err",
  traffic_warning: "warn",
  traffic_exhausted: "err",
  payment_pending: "warn",
  ticket: "info",
  referral_joined: "info",
  partner_joined: "info",
  system: "info",
  broadcast: "info",
  warning: "warn",
  error: "err",
};

function notifIcon(cls: string): React.ReactNode {
  const c = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (cls) {
    case "ok": return (<svg {...c}><path d="m5 12.5 4.2 4.2L19 7" /></svg>);
    case "warn": return (<svg {...c}><path d="M12 3.5 2.5 20.5h19L12 3.5z" /><line x1="12" y1="10" x2="12" y2="14.5" /><circle cx="12" cy="17.6" r="0.6" /></svg>);
    case "err": return (<svg {...c}><circle cx="12" cy="12" r="9" /><path d="m9 9 6 6m0-6-6 6" /></svg>);
    default: return (<svg {...c}><path d="M6 9a6 6 0 1 1 12 0c0 4.5 2 5.5 2 5.5H4S6 13.5 6 9z" /><path d="M9.5 18a2.5 2.5 0 0 0 5 0" /></svg>);
  }
}

function fmtTs(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diffMs = Date.now() - d.getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "сейчас";
  if (mins < 60) return `${mins} мин`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} ч`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "вчера";
  if (days < 30) return `${days} дн`;
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "short" });
}

export function DefaultNotifFeedBlockView({ block, context }: TypedBlockViewProps<"defaultNotifFeed">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const decor = usePanelDecor(t.panel);

  const cfg = parseBlockData(d, NOTIF_FEED_SCHEMA);
  const { panelHeader, filterAllFormat, filterUnreadFormat, filterImportantFormat, markAllLabel, markAllBusyLabel, markAllSuccessText, markAllErrorText, emptyText, unauthenticatedText, loadingText, maxItems, deleteLabel, expandLabel, collapseLabel, clearAllLabel, clearAllConfirmLabel, openLabel } = cfg;
  const [markBusy, setMarkBusy] = useState(false);
  const [markStatus, setMarkStatus] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [clearArmed, setClearArmed] = useState(false);

  function handleClearAll() {
    if (previewMode) return;
    if (!clearArmed) {
      setClearArmed(true);
      setTimeout(() => setClearArmed(false), 3000);
      return;
    }
    setClearArmed(false);
    setExpandedId(null);
    void src.removeAll();
  }

  async function handleMarkAll() {
    if (previewMode || markBusy) return;
    setMarkBusy(true);
    setMarkStatus(null);
    try {
      await real.markAllRead();
      setMarkStatus({ kind: "ok", text: markAllSuccessText });
    } catch {
      setMarkStatus({ kind: "err", text: markAllErrorText });
    } finally {
      setMarkBusy(false);
      setTimeout(() => setMarkStatus(null), 2500);
    }
  }

  const real = useNotifications({ disabled: previewMode === true, limit: maxItems });
  const mock = useMockNotifications();
  const src = previewMode ? mock : real;

  const items = src.notifications.slice(0, maxItems);
  const unreadCount = items.filter((it) => !it.read).length;
  const importantCount = items.filter((it) => {
    const cls = NOTIF_CLS[it.type];
    return cls === "warn" || cls === "err";
  }).length;

  const { open: openNotification, targetOf } = useNotificationOpen({
    markRead: (id) => src.markRead(id),
    disabled: previewMode === true,
  });
  const [filter, setFilter] = useState<FilterKind>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const filtered = filter === "unread"
    ? items.filter((it) => !it.read)
    : filter === "important"
      ? items.filter((it) => {
          const cls = NOTIF_CLS[it.type];
          return cls === "warn" || cls === "err";
        })
      : items;

  const { revealRef, fadeClass: loadedFade } = useLoadedReveal(!previewMode && src.isLoading);
  let body: React.ReactNode;
  if (!previewMode && !src.isAuthenticated && !src.isLoading) {
    body = <div style={{ padding: "8px 0 16px", color: t.inkDim, fontSize: t.font.sm }}>{unauthenticatedText}</div>;
  } else if (!previewMode && src.isLoading) {
    body = <div style={{ padding: "8px 0 16px", color: t.inkDim, fontSize: t.font.sm }}>{loadingText}</div>;
  } else if (filtered.length === 0) {
    body = <div style={{ padding: "8px 0 16px", color: t.inkDim, fontSize: t.font.sm }}>{emptyText}</div>;
  } else {
    body = (
      <div style={{ overflowY: "auto", minHeight: 0, flex: "1 1 auto", display: "flex", flexDirection: "column", gap: 8 }}>
        {filtered.map((n) => (
          <NotifRow
            key={n.id}
            n={n}
            t={t}
            isExpanded={expandedId === n.id}
            onToggleExpand={() => {
              setExpandedId((cur) => (cur === n.id ? null : n.id));
              if (!n.read && !previewMode) void src.markRead(n.id);
            }}
            onDelete={() => {
              if (previewMode) return;
              void src.removeOne(n.id);
              if (expandedId === n.id) setExpandedId(null);
            }}
            deleteLabel={deleteLabel}
            expandLabel={expandLabel}
            collapseLabel={collapseLabel}
            openLabel={openLabel}
            openTarget={targetOf(n)}
            onOpen={() => void openNotification(n)}
          />
        ))}
      </div>
    );
  }

  return wrap(
    <div ref={revealRef} className={ELEMENT_FILL_CLASS} style={{ ...panelStyle(t), ...decor }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: t.space.md, padding: "20px 26px 12px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: t.space.md, flexWrap: "wrap" }}>
          {panelHeader ? <b style={{ color: t.ink, fontWeight: t.weight.bold, fontSize: t.font.lg }}>{panelHeader}</b> : null}
          <SlidingSegmented
            t={t}
            value={filter}
            onChange={(id) => setFilter(id as typeof filter)}
            stretch={false}
            options={[
              { id: "all", content: filterAllFormat.replace("{count}", String(items.length)) },
              { id: "unread", content: filterUnreadFormat.replace("{count}", String(unreadCount)) },
              { id: "important", content: filterImportantFormat.replace("{count}", String(importantCount)) },
            ]}
          />
        </div>
        <span style={{ display: "inline-flex", alignItems: "center", gap: t.space.md }}>
          {markAllLabel ? (
            <button onClick={handleMarkAll} disabled={markBusy} style={{ ...linkBtn(t), cursor: markBusy ? "wait" : "pointer", opacity: markBusy ? 0.6 : 1 }}>
              {markBusy ? markAllBusyLabel : markAllLabel}
            </button>
          ) : null}
          {clearAllLabel && items.length > 0 ? (
            <button onClick={handleClearAll} style={{ ...linkBtn(t), color: clearArmed ? t.error : t.inkDim, cursor: "pointer" }}>
              {clearArmed ? clearAllConfirmLabel : clearAllLabel}
            </button>
          ) : null}
          {markStatus ? (
            <span style={{ fontSize: t.font.xs, color: markStatus.kind === "ok" ? t.success : t.error }}>{markStatus.text}</span>
          ) : null}
        </span>
      </div>
      <div className={loadedFade} style={{ padding: "4px 26px 20px", display: "flex", flexDirection: "column", minHeight: 0, flex: "1 1 auto" }}>{body}</div>
    </div>,
    false,
    true,
  );
}

type NotifRowProps = {
  n: { id: string; type: string; title: string; message: string; read: boolean; created_at: string };
  t: ReturnType<typeof useDefaultTheme>;
  isExpanded: boolean;
  onToggleExpand: () => void;
  onDelete: () => void;
  deleteLabel: string;
  expandLabel: string;
  collapseLabel: string;
  openLabel: string;
  openTarget: NotificationTarget | null;
  onOpen: () => void;
};

function NotifRow({ n, t, isExpanded, onToggleExpand, onDelete, deleteLabel, expandLabel, collapseLabel, openLabel, openTarget, onOpen }: NotifRowProps) {
  const cls = NOTIF_CLS[n.type] ?? "info";
  const tint = cls === "ok" ? t.success : cls === "warn" ? t.warn : cls === "err" ? t.error : t.accent;

  const swipe = useSwipeToDelete();
  const swipeOffset = swipe.swipeOffset;

  const msgRef = useRef<HTMLDivElement>(null);
  const [fullMsgH, setFullMsgH] = useState(0);
  const [overflowing, setOverflowing] = useState(false);
  useEffect(() => {
    const el = msgRef.current;
    if (!el || isExpanded) return;
    const fontSize = parseFloat(window.getComputedStyle(el).fontSize) || 16;
    setFullMsgH(el.scrollHeight);
    setOverflowing(el.scrollHeight > fontSize * COLLAPSED_LINES + 1);
  }, [n.message, isExpanded]);


  const rowBg = n.read ? t.innerBg : mixHexColors(t.innerBg, t.accent, 0.06);

  return (
    <div
      translate="no"
      data-translate="no"
      style={{ position: "relative", borderRadius: t.radius.sm, overflow: "hidden", flexShrink: 0 }}
      {...swipe.handlers}
    >
      <button
        type="button"
        onClick={onDelete}
        onTouchStart={(e) => e.stopPropagation()}
        onTouchEnd={(e) => e.stopPropagation()}
        style={{ position: "absolute", right: 0, top: 0, bottom: 0, width: 160, background: t.error, color: "#fff", border: "none", fontFamily: t.monoFont, fontWeight: t.weight.bold, fontSize: t.font.sm, cursor: "pointer", touchAction: "manipulation" }}
      >
        {deleteLabel}
      </button>
      <div style={{ transform: `translateX(${swipeOffset}px)`, transition: swipe.swiping ? "none" : "transform 0.2s ease-out", background: rowBg }}>
        <div
          role="button"
          tabIndex={0}
          onClick={onToggleExpand}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onToggleExpand();
            }
          }}
          style={{ display: "flex", gap: t.space.md, alignItems: "flex-start", padding: "14px 16px", cursor: "pointer" }}
        >
          <span style={{ ...iconSquareStyle(t, tint, 40), marginTop: 1 }}>{notifIcon(cls)}</span>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: t.font.smPlus, fontWeight: t.weight.bold, color: t.ink, marginBottom: 3 }}>{n.title}</div>
            <div
              ref={msgRef}
              style={{
                position: "relative",
                maxHeight: isExpanded ? (fullMsgH ? `${fullMsgH}px` : "none") : overflowing ? `${COLLAPSED_LINES}em` : "none",
                overflow: "hidden",
                transition: "max-height 0.32s cubic-bezier(0.22, 1, 0.36, 1)",
              }}
            >
              <div
                style={{
                  fontSize: t.font.xsPlus,
                  color: t.inkDim,
                  lineHeight: 1.5,
                  maxWidth: "70ch",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {n.message}
              </div>
              {!isExpanded && overflowing ? (
                <div
                  aria-hidden
                  style={{
                    position: "absolute",
                    left: 0,
                    right: 0,
                    bottom: 0,
                    height: "1.4em",
                    background: `linear-gradient(${hexToRgba(rowBg, 0)}, ${rowBg})`,
                    pointerEvents: "none",
                  }}
                />
              ) : null}
            </div>
            {overflowing || isExpanded ? (
              <span style={{ display: "inline-block", marginTop: 4, fontSize: t.font.xs, color: t.accent }}>
                {isExpanded ? collapseLabel : expandLabel}
              </span>
            ) : null}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: t.space.sm, flexShrink: 0, marginTop: 1 }}>
            <span style={{ fontSize: t.font.xs, color: t.inkDim, whiteSpace: "nowrap" }}>{fmtTs(n.created_at)}</span>
            {openTarget ? (
              <NotificationOpenButton
                kind={openTarget.kind}
                label={openLabel}
                color={t.accent}
                size={16}
                style={{ padding: 2 }}
                onClick={onOpen}
              />
            ) : null}
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onDelete(); }}
              title={deleteLabel}
              aria-label={deleteLabel}
              style={{ background: "transparent", border: "none", color: t.inkMute, cursor: "pointer", padding: 2, fontSize: t.font.md, lineHeight: 1 }}
            >
              ×
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
