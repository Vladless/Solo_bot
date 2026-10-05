"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, iconSquareStyle, DefaultPanel, f, parseBlockData } from ".";
import { DefaultBody, DefaultOptionCard, GROUP_GAP, OPTION_CARD_MIN_WIDTH } from "./layout";
import { useBalancedColumns } from "@/components/constructor/blockContent/useBalancedColumns";
import { useCabinetTarget } from "@/app/(marketing)/landing/useCabinetTarget";
import type { DefaultQuickActionItem } from "@/components/constructor/blockData/blocks";

const SCHEMA = {
  panelHeader: f.str("Быстрые действия"),
  panelHeaderHint: f.str(""),
  emptyText: f.str("Действия не настроены"),
  maxColumns: f.num(3),
};

export function DefaultQuickActionsBlockView({ block, context }: TypedBlockViewProps<"defaultQuickActions">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, SCHEMA);
  const isPreview = previewMode === true;

  const items: DefaultQuickActionItem[] = Array.isArray(d.items) ? (d.items as DefaultQuickActionItem[]) : [];
  const maxCols = Math.max(1, Math.min(4, Math.round(cfg.maxColumns)));
  const { ref: gridRef, columns } = useBalancedColumns(items.length, {
    minCardWidth: OPTION_CARD_MIN_WIDTH,
    maxCols,
    gap: GROUP_GAP,
    fill: true,
  });

  const goToTarget = useCabinetTarget();
  const router = useRouter();
  const [hovered, setHovered] = useState<number | null>(null);

  const run = (item: DefaultQuickActionItem) => {
    if (isPreview) return;
    if (item.linkType === "tab") {
      goToTarget({
        tabGroup: item.cabinetTabGroup,
        tabId: item.cabinetTabId,
        screenGroup: item.cabinetScreenGroup,
        screenId: item.cabinetScreenId,
        anchor: item.anchor,
      });
      return;
    }
    const href = (item.href ?? "").trim();
    if (!href) return;
    if (/^[a-z][a-z0-9+.-]*:/i.test(href)) {
      window.open(href, "_blank", "noreferrer");
      return;
    }
    router.push(href);
  };

  return wrap(
    <DefaultPanel
      t={t}
      header={cfg.panelHeader}
      hint={cfg.panelHeaderHint || undefined}
      empty={items.length === 0}
      emptyText={cfg.emptyText}
      noBodyPadding
    >
      <DefaultBody t={t}>
        <div
          ref={gridRef}
          style={{
            display: "grid",
            gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
            gap: t.px(GROUP_GAP),
            flex: "1 1 auto",
            minHeight: 0,
          }}
        >
          {items.map((item, i) => (
            <DefaultOptionCard
              key={i}
              t={t}
              onClick={() => run(item)}
              hovered={hovered === i}
              onHoverChange={(value) => setHovered(value ? i : null)}
              ariaLabel={item.title}
            >
              {item.icon ? (
                <span style={{ ...iconSquareStyle(t, t.accent, 40), fontSize: t.font.md }}>{item.icon}</span>
              ) : null}
              <span
                style={{
                  fontSize: t.font.smPlus,
                  fontWeight: t.weight.bold,
                  color: t.ink,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {item.title ?? ""}
              </span>
              {item.subtitle ? (
                <span style={{ fontSize: t.font.xs, color: t.inkMute, lineHeight: 1.4 }}>{item.subtitle}</span>
              ) : null}
            </DefaultOptionCard>
          ))}
        </div>
      </DefaultBody>
    </DefaultPanel>,
    false,
    true,
  );
}
