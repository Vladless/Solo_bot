"use client";

import { mapSessionRow } from "@/components/constructor/blockContent/cabinetKit/blockHelpers";
import { useState } from "react";
import { formatRelativeTime } from "@/lib/format-date";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, iconSquareStyle, linkBtn, DefaultPanel, f, parseBlockData, useBlockApi } from ".";
import { DefaultPagination, usePagedItems } from "./DefaultPagination";
import { DefaultBody, DefaultFooter, DefaultListItem, DefaultListRows, btnChip } from "./layout";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import { isApiError } from "@/lib/api";
import { MOCK_SESSIONS, type MockSession } from "@/components/constructor/blockContent/cabinetKit/_mocks";

const LOGIN_HISTORY_SCHEMA = {
  panelHeader: f.str("История входов"),
  allLabel: f.str("Все сессии"),
  allHref: f.str(""),
  currentSuffix: f.str("сейчас"),
  emptyText: f.str("Нет активных сессий"),
  maxItems: f.num(100),
  pageSize: f.num(4),
  prevLabel: f.str("Назад"),
  nextLabel: f.str("Вперёд"),
  pageLabelFormat: f.str("{page} / {total}"),
  revokeLabel: f.str("Завершить"),
  revokeBusyLabel: f.str("..."),
  revokeErrorText: f.str("Не удалось"),
};

type SessionItem = MockSession;

type ApiSession = {
  id: string;
  device_label: string | null;
  ip: string | null;
  created_at: string;
  last_seen_at: string;
  expires_at: string | null;
  is_current: boolean;
};


export function DefaultLoginHistoryBlockView({ block, context }: TypedBlockViewProps<"defaultLoginHistory">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);

  const cfg = parseBlockData(d, LOGIN_HISTORY_SCHEMA);

  const isPreview = previewMode === true;
  const api = useBlockApi({ needs: ["sessions"], disabled: isPreview });
  const mut = useAccountMutations();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const items: SessionItem[] = isPreview
    ? MOCK_SESSIONS
    : ((api.sessions.data ?? []) as unknown as ApiSession[]).map((s) => mapSessionRow(s, formatRelativeTime));
  const loaded = isPreview || api.sessions.data !== undefined || api.sessions.error !== undefined;

  async function handleRevoke(id: string) {
    if (busyId || isPreview) return;
    setBusyId(id);
    setErrorMsg(null);
    try {
      await mut.revokeSession(id);
    } catch (error) {
      const fallback = cfg.revokeErrorText;
      const msg = isApiError(error) ? error.message : fallback;
      setErrorMsg(msg || fallback);
    } finally {
      setBusyId(null);
    }
  }

  const allItems = items.slice(0, cfg.maxItems);
  const pageSize = Math.max(1, Math.min(50, cfg.pageSize));
  const paged = usePagedItems(allItems, pageSize);
  const visible = loaded ? paged.pageItems : [];

  const headerAction = cfg.allLabel
    ? (cfg.allHref
        ? <a href={cfg.allHref} style={linkBtn(t)}>{cfg.allLabel}</a>
        : <span style={{ ...linkBtn(t), cursor: "default" }}>{cfg.allLabel}</span>)
    : undefined;

  return wrap(
    <DefaultPanel
      t={t}
      header={cfg.panelHeader}
      headerAction={headerAction}
      loading={false}
      skeleton={false}
      empty={loaded && visible.length === 0}
      emptyText={cfg.emptyText}
      noBodyPadding
    >
      <DefaultBody t={t}>
        <DefaultListRows t={t} slots={paged.pageSize} flipRef={paged.flipRef} loading={!loaded}>
          {visible.map((s) => (
            <DefaultListItem
              key={s.id}
              t={t}
              leading={<span style={{ ...iconSquareStyle(t, t.accent, 40), fontSize: 9 }}>●</span>}
              title={s.device}
              meta={s.ip}
              value={s.current ? cfg.currentSuffix : s.ts}
              valueColor={s.current ? t.success : t.inkDim}
              action={
                !s.current ? (
                  <button
                    type="button"
                    onClick={() => handleRevoke(s.id)}
                    disabled={busyId === s.id}
                    style={{ ...btnChip(t, t.error), cursor: busyId === s.id ? "wait" : "pointer", opacity: busyId === s.id ? 0.5 : 1 }}
                  >
                    {busyId === s.id ? cfg.revokeBusyLabel : cfg.revokeLabel}
                  </button>
                ) : null
              }
            />
          ))}
        </DefaultListRows>
        {errorMsg ? <div style={{ fontSize: t.font.xsPlus, color: t.error }}>{errorMsg}</div> : null}
        <DefaultFooter t={t}>
          <DefaultPagination
            t={t}
            page={paged.page}
            totalPages={paged.totalPages}
            onPrev={paged.prev}
            onNext={paged.next}
            prevLabel={cfg.prevLabel}
            nextLabel={cfg.nextLabel}
            labelFormat={cfg.pageLabelFormat}
          />
        </DefaultFooter>
      </DefaultBody>
    </DefaultPanel>,
    false,
    true,
  );
}
