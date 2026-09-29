"use client";

import { useAuthenticatedSWR } from "@/lib/useAuthenticatedSWR";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, DefaultPanel, f, parseBlockData } from ".";
import { usePagedItems, DefaultPagination } from "./DefaultPagination";
import { DefaultBody, DefaultFooter, DefaultListRows, LIST_ROW_H } from "./layout";

type NodeStatusItem = { uuid: string; label: string; description: string };
type NodeStatusNode = { uuid: string; online: boolean; name: string; load: number; host: string; port: number | null; position: number };
type NodeStatusPayload = { nodes: NodeStatusNode[] };

/** Строка подсказки под полосой: место под неё держим всегда, иначе блок подрастает по приходу данных. */
const HINT_LINE_H = 16;

const SCHEMA = {
  panelHeader: f.str("Статус серверов"),
  hint: f.str(""),
  onlineLabel: f.str("Работает"),
  offlineLabel: f.str("Не работает"),
  checkingLabel: f.str("Проверка…"),
  pingFormat: f.str("≈ {ms} мс"),
  fastestFormat: f.str("⚡ Самый быстрый: {name} · ≈ {ms} мс"),
  recommendFormat: f.str("🚀 Рекомендуем: {name} (меньше всего нагрузка)"),
  probePort: f.num(443),
  probeEnabled: f.bool(true),
  emptyText: f.str("Нет данных о серверах"),
  loadingText: f.str("Загрузка…"),
  totalFormat: f.str("Всего {total} · онлайн {online} · недоступно {offline}"),
  pageSize: f.num(5),
  prevLabel: f.str("Назад"),
  nextLabel: f.str("Вперёд"),
  items: f.array<NodeStatusItem>(),
};

type Row = { uuid: string; label: string; description: string; online: boolean; load: number; host: string; port: number | null; position: number };

const PREVIEW: Row[] = [
  { uuid: "p1", label: "🇳🇱 Нидерланды", description: "Основной", online: true, load: 142, host: "", port: null, position: 0 },
  { uuid: "p2", label: "🇩🇪 Германия", description: "", online: true, load: 88, host: "", port: null, position: 1 },
  { uuid: "p3", label: "🇺🇸 США", description: "", online: false, load: 0, host: "", port: null, position: 2 },
];

import { useReachability, withFlag } from "@/components/constructor/blockContent/nodeStatusShared";

export function DefaultNodeStatusBlockView({ block, context, editMode }: TypedBlockViewProps<"defaultNodeStatus">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, SCHEMA);
  const isPreview = previewMode === true;

  const { data, isLoading } = useAuthenticatedSWR<NodeStatusPayload>({
    path: "/api/web/node-status",
    cacheKey: "web-node-status",
    disabled: isPreview,
    logTag: "node-status",
  });

  const overrides = new Map<string, NodeStatusItem>();
  for (const it of cfg.items ?? []) {
    if (it && it.uuid) overrides.set(it.uuid, it);
  }

  const all: Row[] = isPreview
    ? PREVIEW
    : (data?.nodes ?? []).map((n) => {
        const o = overrides.get(n.uuid);
        return {
          uuid: n.uuid,
          online: n.online === true,
          load: Number(n.load) || 0,
          host: n.host || "",
          port: typeof n.port === "number" ? n.port : null,
          position: Number(n.position) || 0,
          label: withFlag((o?.label && o.label.trim()) || n.name || "Сервер"),
          description: (o?.description && o.description.trim()) || "",
        };
      });

  const sorted = [...all].sort((a, b) => a.position - b.position);
  const total = sorted.length;
  const onlineRows = sorted.filter((n) => n.online);
  const onlineCount = onlineRows.length;
  const offlineCount = total - onlineCount;
  const maxLoad = Math.max(1, ...sorted.map((n) => n.load));
  const summary = cfg.totalFormat
    .replace("{total}", String(total))
    .replace("{online}", String(onlineCount))
    .replace("{offline}", String(offlineCount));

  const paged = usePagedItems(sorted, cfg.pageSize);
  const reach = useReachability(
    paged.pageItems.filter((n) => n.online).map((n) => ({ uuid: n.uuid, host: n.host, port: n.port })),
    cfg.probePort || 443,
    !isPreview && cfg.probeEnabled,
  );

  let fastest: { name: string; ms: number } | null = null;
  for (const n of onlineRows) {
    const r = reach[n.uuid];
    if (r?.state === "up" && r.ms != null && (fastest === null || r.ms < fastest.ms)) {
      fastest = { name: n.label, ms: r.ms };
    }
  }
  let leastLoaded: { name: string } | null = null;
  if (!isPreview && onlineRows.length > 0) {
    const best = onlineRows.reduce((a, b) => (b.load < a.load ? b : a));
    leastLoaded = { name: best.label };
  }

  const probing = !isPreview && cfg.probeEnabled;
  const pingLabel = (n: Row): { text: string; color: string } | null => {
    if (!probing || !n.online) return null;
    const r = reach[n.uuid];
    if (r?.state === "up" && r.ms != null) {
      return { text: cfg.pingFormat.replace("{ms}", String(r.ms)), color: t.inkDim };
    }
    if (r?.state === "checking" || !r) return { text: cfg.checkingLabel, color: t.inkMute };
    return { text: "—", color: t.inkMute };
  };

  if (!isPreview && !editMode && !isLoading && total === 0) return null;

  return wrap(
    <DefaultPanel
      t={t}
      header={cfg.panelHeader}
      hint={cfg.hint || undefined}
      loading={false}
      skeleton={false}
      loadingText={cfg.loadingText}
      empty={!isLoading && total === 0}
      emptyText={cfg.emptyText}
      noBodyPadding
    >
      <DefaultBody t={t}>
        <div style={{ display: "flex", flexDirection: "column", gap: t.space.sm }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ fontSize: t.font.xs, color: t.inkDim }}>{summary}</div>
            <div style={{ display: "flex", height: 8, borderRadius: 999, overflow: "hidden", background: t.line }}>
              <div data-motion-bar="true" style={{ width: `${total ? (onlineCount / total) * 100 : 0}%`, background: t.success }} />
              <div data-motion-bar="true" style={{ width: `${total ? (offlineCount / total) * 100 : 0}%`, background: t.inkMute }} />
            </div>
          </div>

          {isPreview ? null : (
            <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
              <div style={{ fontSize: t.font.xs, fontWeight: t.weight.bold, color: t.ink, minHeight: t.px(HINT_LINE_H) }}>
                {fastest ? cfg.fastestFormat.replace("{name}", fastest.name).replace("{ms}", String(fastest.ms)) : ""}
              </div>
              <div style={{ fontSize: t.font.xs, fontWeight: t.weight.bold, color: t.ink, minHeight: t.px(HINT_LINE_H) }}>
                {leastLoaded ? cfg.recommendFormat.replace("{name}", leastLoaded.name) : ""}
              </div>
            </div>
          )}

          <DefaultListRows t={t} slots={paged.pageSize} flipRef={paged.flipRef} loading={!isPreview && isLoading}>
            {(!isPreview && isLoading ? [] : paged.pageItems).map((n) => {
              const pl = pingLabel(n);
              return (
                <div
                  key={n.uuid}
                  style={{ display: "flex", alignItems: "center", gap: t.space.sm, height: t.px(LIST_ROW_H), minWidth: 0, overflow: "hidden" }}
                >
                  <span
                    {...(n.online ? { "data-motion-pulse": "true" } : {})}
                    style={{
                      width: 9,
                      height: 9,
                      borderRadius: 999,
                      flexShrink: 0,
                      background: n.online ? t.success : t.inkMute,
                      boxShadow: n.online ? `0 0 8px ${t.success}` : "none",
                    }}
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: t.font.sm, fontWeight: t.weight.bold, color: t.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {n.label}
                    </div>
                    {n.description ? (
                      <div style={{ fontSize: t.font.xs, color: t.inkDim, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {n.description}
                      </div>
                    ) : null}
                    <div style={{ marginTop: 4, height: 4, borderRadius: 999, background: t.line, overflow: "hidden" }}>
                      <div data-motion-bar="true" style={{ width: `${n.online ? Math.max(4, (n.load / maxLoad) * 100) : 0}%`, height: "100%", background: t.accent }} />
                    </div>
                  </div>
                  <div style={{ flexShrink: 0, textAlign: "right" }}>
                    <div style={{ fontSize: t.font.xs, fontWeight: t.weight.bold, color: n.online ? t.success : t.inkMute }}>
                      {n.online ? cfg.onlineLabel : cfg.offlineLabel}
                    </div>
                    {pl ? (
                      <div style={{ marginTop: 2, fontSize: t.font.xs, color: pl.color, whiteSpace: "nowrap" }}>{pl.text}</div>
                    ) : null}
                  </div>
                </div>
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
              prevLabel={cfg.prevLabel}
              nextLabel={cfg.nextLabel}
            />
          </DefaultFooter>
        </div>
      </DefaultBody>
    </DefaultPanel>,
    false,
    true,
  );
}
