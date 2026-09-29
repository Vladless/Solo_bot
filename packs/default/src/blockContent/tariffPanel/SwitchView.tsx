"use client";

import type { WebTariffPublic } from "@/components/constructor/blockContent/tariffTypes";
import { useDefaultTheme, linkBtn } from "..";
import { hexToRgba } from "@/components/constructor/utils";
import { DefaultOptionCard, DefaultOptionSlots, OPTION_CARD_HEIGHT } from "../layout";
import { DefaultOptionRow, OPTION_ROW_HEIGHT } from "../DefaultOptionRow";
import { DefaultPageDots, useAutoPagedItems } from "../DefaultPagination";
import { MONO_ICON_PATHS } from "@/lib/cabinet-sidebar-icons";
import { formatPeriod, formatPricePerDayValue } from "@/components/constructor/blockContent/tariffUtils";

type DefaultTheme = ReturnType<typeof useDefaultTheme>;
type PagedItems = ReturnType<typeof useAutoPagedItems<WebTariffPublic>>;

/** Экран смены тарифа: список доступных тарифов карточками или строками. */
export function SwitchView({
  t,
  activeKey,
  isPreview,
  switchSortedTariffs,
  switchTariffsList,
  switchListLoading,
  switchLoadedFade,
  switchCompact,
  listCols,
  cardCols,
  pageFlipRef,
  pageItems,
  perPage,
  page,
  totalPages,
  setPage,
  switchHoverId,
  setSwitchHoverId,
  setSwitchGrid,
  switchTariffPageLabel,
  switchTariffBackLabel,
  switchTariffEmptyText,
  switchTariffCurrentBadge,
  switchTariffSelectLabel,
  switchTariffUpgradeLabel,
  switchTariffPerDayLabel,
  closeSwitch,
  onPickTariff,
}: {
  t: DefaultTheme;
  activeKey: { tariff_id?: number | null } | null | undefined;
  isPreview: boolean;
  switchSortedTariffs: WebTariffPublic[];
  switchTariffsList: WebTariffPublic[] | undefined;
  switchListLoading: boolean;
  switchLoadedFade: string | undefined;
  switchCompact: boolean;
  listCols: number;
  cardCols: number;
  pageFlipRef: PagedItems["flipRef"];
  pageItems: WebTariffPublic[];
  perPage: number;
  page: number;
  totalPages: number;
  setPage: (page: number) => void;
  switchHoverId: number | null;
  setSwitchHoverId: (id: number | null) => void;
  setSwitchGrid: (node: HTMLDivElement | null) => void;
  switchTariffPageLabel: string;
  switchTariffBackLabel: string;
  switchTariffEmptyText: string;
  switchTariffCurrentBadge: string;
  switchTariffSelectLabel: string;
  switchTariffUpgradeLabel: string;
  switchTariffPerDayLabel: string;
  closeSwitch: () => void;
  onPickTariff: (tariffId: number) => void;
}) {
  const subIcon = (size: number) => (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={t.accent} strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {MONO_ICON_PATHS.key}
    </svg>
  );
    const sorted = switchSortedTariffs;
    const currentTariffId = activeKey?.tariff_id ?? null;
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: t.space.md, flex: "1 1 auto", minHeight: 0 }}>
        <button
          type="button"
          onClick={closeSwitch}
          style={{
            ...linkBtn(t),
            color: t.ink,
            fontSize: t.font.md,
            fontWeight: t.weight.bold,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "9px 16px",
            borderRadius: t.radius.sm,
            background: hexToRgba(t.ink, 0.06),
            alignSelf: "flex-start",
            cursor: "pointer",
          }}
        >
          {switchTariffBackLabel}
        </button>
        {!isPreview && switchListLoading && !switchTariffsList ? (
          <div style={{ color: t.inkDim, fontSize: t.font.sm }}>Загрузка...</div>
        ) : sorted.length === 0 ? (
          <div className={switchLoadedFade} style={{ color: t.inkDim, fontSize: t.font.sm }}>{switchTariffEmptyText}</div>
        ) : (
          <div
            className={switchLoadedFade ? `scrollbar-hide ${switchLoadedFade}` : "scrollbar-hide"}
            ref={setSwitchGrid}
            style={{ minHeight: 0, flex: "1 1 auto", overflow: "visible", padding: "4px 3px", display: "flex", flexDirection: "column" }}
          >
            <div
              ref={pageFlipRef}
              style={
                switchCompact
                  ? {
                      display: "grid",
                      gridTemplateColumns: `repeat(${listCols}, minmax(0, 1fr))`,
                      gridAutoRows: `${t.px(OPTION_ROW_HEIGHT)}px`,
                      gap: 6,
                      alignContent: "start",
                      minHeight: 0,
                      flex: "1 1 auto",
                      overflow: "visible",
                    }
                  : {
                      display: "grid",
                      gridTemplateColumns: `repeat(${cardCols}, minmax(0, 1fr))`,
                      gridAutoRows: `minmax(${t.px(OPTION_CARD_HEIGHT)}px, max-content)`,
                      alignContent: "start",
                      gap: 10,
                      minHeight: 0,
                      flex: "1 1 auto",
                      overflow: "visible",
                    }
              }
            >
              {pageItems.map((tr: WebTariffPublic) => {
                const isCurrent = currentTariffId === tr.id;
                const pricePerDay = tr.duration_days > 0 ? tr.price_rub / tr.duration_days : tr.price_rub;
                const devOpts = tr.device_options ?? [];
                const trOptsPos = (tr.traffic_options_gb ?? []).filter((v) => v > 0);
                const isCfg = Boolean(tr.configurable && ((tr.device_options ?? []).length > 0 || (tr.traffic_options_gb ?? []).length > 0));
                const devicesText = devOpts.length > 1
                  ? `${Math.min(...devOpts)}–${Math.max(...devOpts)} устр.`
                  : tr.device_limit && tr.device_limit > 0 ? `${tr.device_limit} устр.` : "∞ устр.";
                const trafficText = trOptsPos.length > 1
                  ? `${Math.min(...trOptsPos)}–${Math.max(...trOptsPos)} ГБ`
                  : tr.traffic_limit && tr.traffic_limit > 0 ? `${tr.traffic_limit} ГБ` : "∞";
                const isHovered = switchHoverId === tr.id;
                const actionLabel = isCurrent
                  ? switchTariffCurrentBadge
                  : currentTariffId == null
                    ? switchTariffSelectLabel
                    : switchTariffUpgradeLabel;
                if (switchCompact) {
                  return (
                    <DefaultOptionRow
                      key={tr.id}
                      t={t}
                      title={tr.name}
                      leading={subIcon(22)}
                      meta={`${devicesText} · ${trafficText}${tr.duration_days > 0 ? ` · ${formatPeriod(tr.duration_days)}` : ""}`}
                      price={`${isCfg ? "от " : ""}${tr.price_rub.toLocaleString("ru-RU")} ₽`}
                      priceNote={listCols === 1 ? `~${formatPricePerDayValue(pricePerDay)} ₽ ${switchTariffPerDayLabel}` : undefined}
                      actionLabel={actionLabel}
                      badge={isCurrent ? switchTariffCurrentBadge : undefined}
                      selected={isCurrent}
                      disabled={isCurrent}
                      onClick={() => onPickTariff(tr.id)}
                    />
                  );
                }
                return (
                  <DefaultOptionCard
                    key={tr.id}
                    t={t}
                    selected={isCurrent}
                    hovered={isHovered && !isCurrent}
                    disabled={isCurrent}
                    onClick={() => onPickTariff(tr.id)}
                    onHoverChange={(value) => setSwitchHoverId(value ? tr.id : null)}
                    ariaLabel={`${tr.name} — ${actionLabel}`}
                  >
                    <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                      {subIcon(24)}
                      <span style={{ fontSize: t.font.lg, fontWeight: t.weight.bold, color: isCurrent ? t.accent : t.ink, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {tr.name}
                      </span>
                    </span>
                    <span style={{ fontSize: t.font.sm, color: t.inkDim }}>
                      {devicesText} · {trafficText}{tr.duration_days > 0 ? ` · ${formatPeriod(tr.duration_days)}` : ""}
                    </span>
                    <span style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
                      <span style={{ fontSize: t.font.xl, fontWeight: t.weight.bold, color: t.ink, fontVariantNumeric: "tabular-nums", lineHeight: 1 }}>
                        {isCfg ? "от " : ""}{tr.price_rub.toLocaleString("ru-RU")} ₽
                      </span>
                      <span style={{ fontSize: t.font.xs, color: t.inkMute }}>
                        ~{formatPricePerDayValue(pricePerDay)} ₽ {switchTariffPerDayLabel}
                      </span>
                    </span>
                  </DefaultOptionCard>
                );
              })}
              <DefaultOptionSlots count={perPage - pageItems.length} />
            </div>
            <DefaultPageDots
              t={t}
              page={page}
              totalPages={totalPages}
              onPick={setPage}
              label={switchTariffPageLabel}
            />
          </div>
        )}
      </div>
    );
}
