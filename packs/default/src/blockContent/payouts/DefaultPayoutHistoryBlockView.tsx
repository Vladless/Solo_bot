"use client";

import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import {
  useDefaultTheme,
  pillStyle,
  DefaultPanel,
  f,
  parseBlockData,
  useLoadedFade,
} from "..";
import { usePagedItems, DefaultPagination } from "../DefaultPagination";
import { usePayoutHistory, payoutStatusKind } from "@/components/constructor/blockContent/payouts/payoutData";
import { DefaultBody, DefaultFooter, DefaultListItem, DefaultListRows } from "../layout";
import { formatDateShort } from "@/lib/format-date";

const SCHEMA = {
  panelHeader: f.str("История выводов"),
  panelHeaderHintFmt: f.str("всего {count}"),
  emptyText: f.str("Заявок пока нет"),
  loadingText: f.str("Загрузка..."),
  statusPendingLabel: f.str("В обработке"),
  statusApprovedLabel: f.str("Одобрено"),
  statusRejectedLabel: f.str("Отклонено"),
  statusPaidLabel: f.str("Выплачено"),
  maxItems: f.num(20),
  pageSize: f.num(4),
  pagePrevLabel: f.str("Назад"),
  pageNextLabel: f.str("Вперёд"),
  pageLabelFormat: f.str("{page} / {total}"),
};


export function DefaultPayoutHistoryBlockView({ block, context }: TypedBlockViewProps<"defaultPayoutHistory">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, SCHEMA);

  const isPreview = previewMode === true;
  const history = usePayoutHistory(isPreview, cfg.maxItems);
  const loadedFade = useLoadedFade(history.isLoading);
  const paged = usePagedItems(history.items, cfg.pageSize);

  const statusPill = (status: string) => {
    const kind = payoutStatusKind(status);
    const label =
      kind === "pending" ? cfg.statusPendingLabel :
      kind === "approved" ? cfg.statusApprovedLabel :
      kind === "rejected" ? cfg.statusRejectedLabel :
      kind === "paid" ? cfg.statusPaidLabel :
      status;
    const color =
      kind === "paid" || kind === "approved" ? t.success :
      kind === "rejected" ? t.error :
      kind === "pending" ? t.warn :
      t.inkDim;
    return <span style={pillStyle(t, color)}>{label}</span>;
  };

  return wrap(
    <DefaultPanel
      t={t}
      header={cfg.panelHeader}
      hint={cfg.panelHeaderHintFmt.replace("{count}", String(history.items.length))}
      noBodyPadding
      loading={history.isLoading}
      loadingText={cfg.loadingText}
      empty={history.loaded && history.items.length === 0}
      emptyText={cfg.emptyText}
    >
      <DefaultBody t={t} style={{ minWidth: 0 }}>
        <div className={loadedFade} style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: "1 1 auto" }}>
          <DefaultListRows t={t} slots={paged.pageSize} flipRef={paged.flipRef}>
            {paged.pageItems.map((it) => (
              <DefaultListItem
                key={it.id}
                t={t}
                title={formatDateShort(it.created_at)}
                meta={it.method || it.destination || undefined}
                value={`${it.amount_rub.toLocaleString("ru-RU")} ₽`}
                valueColor={t.ink}
                action={statusPill(it.status)}
              />
            ))}
          </DefaultListRows>
        </div>
        <DefaultFooter t={t}>
          <DefaultPagination
            t={t}
            page={paged.page}
            totalPages={paged.totalPages}
            onPrev={paged.prev}
            onNext={paged.next}
            prevLabel={cfg.pagePrevLabel}
            nextLabel={cfg.pageNextLabel}
            labelFormat={cfg.pageLabelFormat}
          />
        </DefaultFooter>
      </DefaultBody>
    </DefaultPanel>,
    false,
    true,
  );
}
