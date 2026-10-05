"use client";

import { formatDayMonth } from "@/components/constructor/blockContent/cabinetKit/blockHelpers";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, iconSquareStyle, pillStyle, linkBtn, DefaultPanel, f, parseBlockData, useBlockApi } from ".";
import { usePagedItems, DefaultPagination } from "./DefaultPagination";
import { DefaultBody, DefaultFooter, DefaultListItem, DefaultListRows } from "./layout";
import type { CabinetFriendItem } from "@/components/constructor/blockData/blocks";

function buildFriendsSchema(mode: "referral" | "partner") {
  return {
    panelHeader: f.str(mode === "partner" ? "Приведённые партнёром" : "Приведённые друзья"),
    panelHeaderHintFormat: f.str("{count} человек"),
    allLabelFormat: f.str("Все {count}"),
    allHref: f.str(""),
    emptyText: f.str(mode === "partner" ? "Пока нет приглашённых" : "Пока нет рефералов"),
    loadingText: f.str("Загрузка..."),
    activeBadge: f.str(mode === "partner" ? "Оплатил" : "Активен"),
    pendingBadge: f.str(mode === "partner" ? "Ожидает" : "Ожидает"),
    pageSize: f.num(4),
    items: f.array<CabinetFriendItem>(),
  };
}


export function DefaultFriendsListBlockView({ block, context }: TypedBlockViewProps<"defaultFriendsList">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);

  const mode: "referral" | "partner" = d.mode === "partner" ? "partner" : "referral";
  const cfg = parseBlockData(d, buildFriendsSchema(mode));
  const { panelHeader, panelHeaderHintFormat, allLabelFormat, allHref, emptyText, loadingText, activeBadge, pendingBadge, items: fallbackItems } = cfg;

  const api = useBlockApi({
    needs: mode === "partner" ? ["partnersInvited"] : ["referralsList"],
    disabled: previewMode === true,
  });
  const refData = api.referralsList;
  const partnerData = api.partnersInvited;

  const isPreview = previewMode === true;
  const isLoading = mode === "partner" ? partnerData.isLoading && !partnerData.data : refData.isLoading && !refData.data;
  const totalCountFromApi = mode === "partner" ? (partnerData.data?.total ?? 0) : (refData.data?.total ?? 0);
  const apiItems: CabinetFriendItem[] = mode === "partner"
    ? (partnerData.data?.items ?? []).map((p) => ({
        name: `tg · ${p.tg_id}`,
        plan: p.keys_count > 0 ? `${p.keys_count} ключ${p.keys_count > 1 ? "ей" : ""}` : "—",
        bonus: p.payments_count > 0 ? activeBadge : pendingBadge,
        ts: formatDayMonth(p.joined_at),
      }))
    : (refData.data?.items ?? []).map((r) => ({
        name: r.display_id || `id ${r.referred_user_id}`,
        plan: r.referred_tg_id ? `tg · ${r.referred_tg_id}` : "—",
        bonus: r.reward_issued ? activeBadge : pendingBadge,
        ts: "",
      }));
  const items: CabinetFriendItem[] = isPreview ? fallbackItems : apiItems;
  const paged = usePagedItems(items, cfg.pageSize);
  const totalCount = isPreview ? items.length : totalCountFromApi;
  const hint = panelHeaderHintFormat.replace("{count}", String(totalCount));
  const allLabel = allLabelFormat.replace("{count}", String(totalCount));
  const headerAction = allLabel
    ? (allHref ? <a href={allHref} style={linkBtn(t)}>{allLabel}</a> : <span style={{ ...linkBtn(t), cursor: "default" }}>{allLabel}</span>)
    : undefined;

  return wrap(
    <DefaultPanel
      t={t}
      header={panelHeader}
      headerAction={headerAction ?? <span style={{ color: t.inkDim, fontSize: t.font.sm }}>{hint}</span>}
      loading={false}
      skeleton={false}
      loadingText={loadingText}
      empty={items.length === 0}
      emptyText={emptyText}
      noBodyPadding
    >
      <DefaultBody t={t}>
        <DefaultListRows t={t} slots={paged.pageSize} flipRef={paged.flipRef} loading={!isPreview && isLoading}>
          {(!isPreview && isLoading ? [] : paged.pageItems).map((it, i) => {
            const bonus = (it.bonus ?? "").trim();
            const isPositive = bonus !== "" && bonus !== pendingBadge && bonus !== "—";
            return (
              <DefaultListItem
                key={i}
                t={t}
                leading={<span style={{ ...iconSquareStyle(t, t.accent, 40), fontSize: t.font.smPlus, fontWeight: t.weight.bold }}>@</span>}
                title={it.name ?? ""}
                meta={it.plan || undefined}
                action={bonus ? <span style={pillStyle(t, isPositive ? t.success : t.inkDim)}>{bonus}</span> : null}
              />
            );
          })}
        </DefaultListRows>
        <DefaultFooter t={t}>
          <DefaultPagination t={t} page={paged.page} totalPages={paged.totalPages} onPrev={paged.prev} onNext={paged.next} />
        </DefaultFooter>
      </DefaultBody>
    </DefaultPanel>,
    false,
    true,
  );
}
