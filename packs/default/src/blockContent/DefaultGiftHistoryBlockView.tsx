"use client";

import { useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, iconSquareStyle, pickContrast, DefaultPanel, f, parseBlockData, useAuthenticatedSWR, apiFetch } from ".";
import { usePagedItems, DefaultPagination } from "./DefaultPagination";
import { DefaultQrModal } from "./DefaultQrModal";
import { MOCK_GIFTS, type MockGift } from "@/components/constructor/blockContent/cabinetKit/_mocks";
import { CHIP_H, DefaultBody, DefaultFooter, DefaultListItem, DefaultListRows, btnChip } from "./layout";
import { formatDateNumeric } from "@/lib/format-date";

const GIFT_HISTORY_SCHEMA = {
  panelHeader: f.str("История"),
  panelHeaderHintFormat: f.str(""),
  emptyText: f.str("Пока нет подарков"),
  loadingText: f.str("Загрузка..."),
  unauthenticatedText: f.str("Войдите в аккаунт"),
  unusedLabel: f.str("Ожидает"),
  usedLabel: f.str("Использован"),
  copyLabel: f.str("Копировать"),
  copiedLabel: f.str("Скопировано"),
  shareLabel: f.str("Отправить"),
  shareTitle: f.str("Подарок"),
  qrTitle: f.str("QR подарка"),
  qrHint: f.str("Покажите QR другу — ссылка на подарок откроется по сканированию."),
  qrErrorText: f.str("Не удалось загрузить QR"),
  qrCloseLabel: f.str("Закрыть"),
  maxItems: f.num(100),
  pageSize: f.num(4),
};

type MyGiftItem = MockGift & { created_at: string | null };
type MyGiftsResponse = { ok: boolean; gifts: MyGiftItem[] };


function QrGlyph() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <path d="M14 14h3v3h-3z" />
      <path d="M21 14v2M21 19v2M16 21h2" />
    </svg>
  );
}

function GiftGlyph() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="8" width="18" height="4" rx="1" />
      <path d="M5 12v8a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-8" />
      <path d="M12 8v13" />
      <path d="M12 8c-1.5-3.5-6-3.5-6-1s4.5 2.5 6 1Z" />
      <path d="M12 8c1.5-3.5 6-3.5 6-1s-4.5 2.5-6 1Z" />
    </svg>
  );
}

export function DefaultGiftHistoryBlockView({ block, context }: TypedBlockViewProps<"defaultGiftHistory">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);

  const cfg = parseBlockData(d, GIFT_HISTORY_SCHEMA);
  const { panelHeader, panelHeaderHintFormat, emptyText, loadingText, unauthenticatedText, unusedLabel, usedLabel, copyLabel, copiedLabel, shareLabel, shareTitle, maxItems } = cfg;
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [qrGiftId, setQrGiftId] = useState<string | null>(null);
  const [qrImgs, setQrImgs] = useState<Record<string, string>>({});
  const [qrLoading, setQrLoading] = useState(false);
  const [qrError, setQrError] = useState("");

  async function handleQr(giftId: string) {
    setQrGiftId(giftId);
    setQrError("");
    if (qrImgs[giftId] || previewMode) return;
    setQrLoading(true);
    try {
      const res = await apiFetch<{ ok: boolean; image_data_url?: string }>(`/api/gifts/my/${encodeURIComponent(giftId)}/qr`);
      setQrImgs((m) => ({ ...m, [giftId]: res.image_data_url ?? "" }));
    } catch {
      setQrError(cfg.qrErrorText);
    } finally {
      setQrLoading(false);
    }
  }

  function handleCopy(giftId: string, link: string) {
    if (!link) return;
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(link).catch(() => { });
    }
    setCopiedId(giftId);
    setTimeout(() => setCopiedId((c) => (c === giftId ? null : c)), 1600);
  }

  async function handleShare(giftId: string, link: string) {
    if (!link) return;
    const nav = typeof navigator !== "undefined" ? (navigator as Navigator & { share?: (data: ShareData) => Promise<void> }) : null;
    if (nav?.share) {
      try {
        await nav.share({ title: shareTitle, url: link });
        return;
      } catch {
      }
    }
    handleCopy(giftId, link);
  }

  const { data, isLoading, isAuthenticated } = useAuthenticatedSWR<MyGiftsResponse>({
    path: "/api/gifts/my?limit=100",
    cacheKey: "my-gifts-mono",
    disabled: previewMode === true,
    logTag: "gifts-mono",
  });

  const gifts: MyGiftItem[] = previewMode ? MOCK_GIFTS : data?.gifts ?? [];
  const items = gifts.slice(0, maxItems);
  const paged = usePagedItems(items, cfg.pageSize);
  const usedCount = items.reduce((n, g) => n + (g.is_used ? 1 : 0), 0);
  const hint = panelHeaderHintFormat
    ? panelHeaderHintFormat
        .replace(/\{count\}/g, String(items.length))
        .replace(/\{total\}/g, String(items.length))
        .replace(/\{used\}/g, String(usedCount))
        .replace(/\{activated\}/g, String(usedCount))
    : undefined;

  const isUnauthed = !previewMode && !isAuthenticated;
  const isLoadingState = !previewMode && isLoading;
  const isEmpty = !isUnauthed && !isLoadingState && items.length === 0;

  return wrap(
    <DefaultPanel
      t={t}
      header={panelHeader}
      hint={hint}
      loading={isUnauthed}
      skeleton={false}
      loadingText={isUnauthed ? unauthenticatedText : loadingText}
      empty={isEmpty}
      emptyText={emptyText}
      noBodyPadding
    >
      <DefaultBody t={t}>
        <DefaultListRows t={t} slots={paged.pageSize} flipRef={paged.flipRef} loading={isLoadingState}>
          {(isLoadingState ? [] : paged.pageItems).map((g) => {
            const hasActions = !g.is_used && Boolean(g.site_gift_link);
            const actions = hasActions ? (
              <span style={{ display: "inline-flex", alignItems: "center", gap: t.space.sm, flexShrink: 0 }}>
                <button
                  type="button"
                  onClick={() => void handleQr(g.gift_id)}
                  aria-label={cfg.qrTitle}
                  title={cfg.qrTitle}
                  style={{ ...btnChip(t), width: t.px(CHIP_H), padding: 0 }}
                >
                  <QrGlyph />
                </button>
                <button
                  type="button"
                  onClick={() => void handleShare(g.gift_id, g.site_gift_link)}
                  style={btnChip(t)}
                >
                  {shareLabel}
                </button>
                <button
                  type="button"
                  onClick={() => handleCopy(g.gift_id, g.site_gift_link)}
                  style={{ ...btnChip(t), background: t.accent, color: pickContrast(t.accent), boxShadow: "none" }}
                >
                  {copiedId === g.gift_id ? copiedLabel : copyLabel}
                </button>
              </span>
            ) : null;
            return (
              <DefaultListItem
                key={g.gift_id}
                t={t}
                leading={<span style={iconSquareStyle(t, g.is_used ? t.inkMute : t.success, 40)}><GiftGlyph /></span>}
                title={g.tariff_name}
                meta={`${g.price_rub} ₽ · ${g.is_used ? usedLabel : unusedLabel}`}
                value={formatDateNumeric(g.created_at)}
                action={actions}
              />
            );
          })}
        </DefaultListRows>
        <DefaultFooter t={t}>
          <DefaultPagination t={t} page={paged.page} totalPages={paged.totalPages} onPrev={paged.prev} onNext={paged.next} />
        </DefaultFooter>
      </DefaultBody>
      <DefaultQrModal
        t={t}
        open={qrGiftId !== null}
        onClose={() => setQrGiftId(null)}
        qrImg={qrGiftId ? qrImgs[qrGiftId] ?? null : null}
        loading={qrLoading}
        error={qrError}
        caption={cfg.qrHint}
        title={cfg.qrTitle}
        closeLabel={cfg.qrCloseLabel}
        loadingText={loadingText}
      />
    </DefaultPanel>,
    false,
    true,
  );
}
