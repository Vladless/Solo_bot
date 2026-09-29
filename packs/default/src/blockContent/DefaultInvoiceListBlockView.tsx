"use client";

import { getCachedIdentityId } from "@/lib/api";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, linkBtn, pillStyle, DefaultPanel, f, parseBlockData, useBlockApi, isAdjustmentRow, type PaymentRow } from ".";
import { DefaultPagination, usePagedItems } from "./DefaultPagination";
import { DefaultBody, DefaultFooter, DefaultListItem, DefaultListRows, btnChip } from "./layout";
import { MOCK_PAYMENTS } from "@/components/constructor/blockContent/cabinetKit/_mocks";
import { formatDateFull } from "@/lib/format-date";

const INVOICE_LIST_SCHEMA = {
  panelHeader: f.str("Транзакции"),
  panelHeaderHintFormat: f.str("{count} записей"),
  allLabel: f.str(""),
  allHref: f.str(""),
  emptyText: f.str("Платежей пока нет"),
  errorText: f.str("Не удалось загрузить платежи"),
  unauthenticatedText: f.str("Войдите, чтобы увидеть историю"),
  paidLabel: f.str("Оплачен"),
  failedLabel: f.str("Не оплачено"),
  pdfLabel: f.str("PDF"),
  prevLabel: f.str("Назад"),
  nextLabel: f.str("Вперёд"),
  pageLabelFormat: f.str("{page} / {total}"),
  maxItems: f.num(200),
  pageSize: f.num(4),
  showAdjustments: f.bool(false),
};


export function DefaultInvoiceListBlockView({ block, context }: TypedBlockViewProps<"defaultInvoiceList">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);

  const cfg = parseBlockData(d, INVOICE_LIST_SCHEMA);
  const { panelHeader, panelHeaderHintFormat, allLabel, allHref, emptyText, errorText, unauthenticatedText, paidLabel, failedLabel, pdfLabel, prevLabel, nextLabel, pageLabelFormat, maxItems, showAdjustments } = cfg;
  const pageSize = Math.max(1, Math.min(100, cfg.pageSize));

  const identityId = previewMode ? null : getCachedIdentityId();
  const api = useBlockApi({ needs: ["payments"], disabled: previewMode === true || !identityId });

  const rawList: PaymentRow[] = previewMode ? MOCK_PAYMENTS : api.payments.data ?? [];
  const sourceList: PaymentRow[] = showAdjustments ? rawList : rawList.filter((p) => !isAdjustmentRow(p));
  const allItems = sourceList.slice(0, maxItems);
  const paged = usePagedItems(allItems, pageSize);
  const items = paged.pageItems;
  const hint = panelHeaderHintFormat.replace("{count}", String(allItems.length));

  const isUnauthed = !previewMode && !identityId;
  const isLoadingState = !previewMode && (api.payments.isLoading || (!api.payments.data && !api.payments.error));
  const isError = !previewMode && !isUnauthed && !isLoadingState && !api.payments.data && api.payments.error != null;

  const headerAction = allLabel
    ? (allHref ? <a href={allHref} style={linkBtn(t)}>{allLabel}</a> : <span style={{ ...linkBtn(t), cursor: "default" }}>{allLabel}</span>)
    : undefined;

  function openInvoice(id: string) {
    if (typeof window === "undefined") return;
    const href = `/api/auth/me/payments/${encodeURIComponent(id)}/invoice`;
    window.open(href, "_blank", "noopener,noreferrer");
  }

  return wrap(
    <DefaultPanel
      t={t}
      header={panelHeader}
      hint={hint}
      headerAction={headerAction}
      loading={isUnauthed}
      skeleton={false}
      loadingText={unauthenticatedText}
      empty={!isUnauthed && !isLoadingState && (isError || allItems.length === 0)}
      emptyText={isError ? errorText : emptyText}
      noBodyPadding
    >
      <DefaultBody t={t}>
        <DefaultListRows t={t} slots={paged.pageSize} flipRef={paged.flipRef} loading={isLoadingState}>
          {(isLoadingState ? [] : items).map((inv) => {
            const isPaid = inv.status === "completed" || inv.status === "success" || inv.status === "paid";
            const statusColor = isPaid ? t.success : t.error;
            return (
              <DefaultListItem
                key={String(inv.id)}
                t={t}
                title={formatDateFull(inv.created_at)}
                meta={inv.purpose ?? undefined}
                value={`${inv.amount.toLocaleString("ru-RU")} ${inv.currency === "RUB" ? "₽" : inv.currency}`}
                valueColor={t.ink}
                action={
                  <span style={{ display: "inline-flex", alignItems: "center", gap: t.space.sm }}>
                    <span style={pillStyle(t, statusColor)}>{isPaid ? paidLabel : failedLabel}</span>
                    {isPaid && !previewMode && Number(inv.id) > 0 ? (
                      <button type="button" onClick={() => openInvoice(String(inv.id))} style={btnChip(t, t.inkDim)}>
                        {pdfLabel}
                      </button>
                    ) : null}
                  </span>
                }
              />
            );
          })}
        </DefaultListRows>
        <DefaultFooter t={t}>
          <DefaultPagination
            t={t}
            page={paged.page}
            totalPages={paged.totalPages}
            onPrev={paged.prev}
            onNext={paged.next}
            prevLabel={prevLabel}
            nextLabel={nextLabel}
            labelFormat={pageLabelFormat}
          />
        </DefaultFooter>
      </DefaultBody>
    </DefaultPanel>,
    false,
    true,
  );
}
