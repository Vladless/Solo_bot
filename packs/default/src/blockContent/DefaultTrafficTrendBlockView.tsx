"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, DefaultPanel, f, parseBlockData, useBlockApi, apiFetch, pickContrast } from ".";
import { hexToRgba } from "@/components/constructor/utils";

type Point = { date: string; used_gb: number | null; limit_gb: number | null };
type HistoryResponse = { client_id: string; days: number; points: Point[] };

const SCHEMA = {
  panelHeader: f.str("Использование трафика"),
  emptyText: f.str("Данные появятся в течение нескольких дней."),
  loadingText: f.str("Загрузка..."),
  unitLabel: f.str("ГБ"),
  peakLabel: f.str("Пик"),
  lastLabel: f.str("Сейчас"),
};

type RangeOpt = { key: string; label: string; days: number; granularity: "day" | "hour" };
const RANGES: RangeOpt[] = [
  { key: "h", label: "1д", days: 1, granularity: "hour" },
  { key: "7", label: "7д", days: 7, granularity: "day" },
  { key: "30", label: "30д", days: 30, granularity: "day" },
  { key: "90", label: "90д", days: 90, granularity: "day" },
];

const MOCK: Point[] = Array.from({ length: 30 }, (_, i) => ({
  date: `2026-05-${String(i + 1).padStart(2, "0")}`,
  used_gb: Math.round((i * 1.7 + Math.sin(i / 3) * 4 + 6) * 10) / 10,
  limit_gb: null,
}));

function fmtDay(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
}

function fmtHour(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

export function DefaultTrafficTrendBlockView({ block, context }: TypedBlockViewProps<"defaultTrafficTrend">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, SCHEMA);
  const isPreview = previewMode === true;

  const api = useBlockApi({ needs: ["activeSubscription"], disabled: isPreview });
  const clientId = api.activeKey?.client_id;
  const [range, setRange] = useState<RangeOpt>(RANGES[2]);

  const { data, isLoading } = useSWR<HistoryResponse>(
    !isPreview && clientId
      ? `/api/keys/${encodeURIComponent(clientId)}/traffic-history?days=${range.days}&granularity=${range.granularity}`
      : null,
    (url: string) => apiFetch<HistoryResponse>(url),
  );

  const points: Point[] = isPreview ? MOCK : (data?.points ?? []);
  const values = points.map((p) => (typeof p.used_gb === "number" ? p.used_gb : 0));
  const peak = values.length ? Math.max(...values) : 0;
  const last = values.length ? values[values.length - 1] : 0;
  const chartMax = Math.max(1, peak);

  const path = useMemo(() => {
    if (points.length < 2) return { line: "", area: "" };
    const W = 100;
    const H = 100;
    const max = chartMax;
    const stepX = W / (points.length - 1);
    const coords = points.map((p, i) => {
      const v = typeof p.used_gb === "number" ? p.used_gb : 0;
      const x = i * stepX;
      const y = H - (v / max) * (H - 6) - 3;
      return [x, y] as const;
    });
    const line = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`).join(" ");
    const area = `${line} L${W} ${H} L0 ${H} Z`;
    return { line, area };
  }, [points, chartMax]);

  const gridLevels = [1, 0.5, 0];

  const accent = t.accent;
  const isEmpty = !isPreview && !isLoading && points.length < 2;

  return wrap(
    <DefaultPanel
      t={t}
      header={cfg.panelHeader}
      loading={!isPreview && isLoading}
      headerAction={
        <div style={{ display: "inline-flex", gap: 4 }}>
          {RANGES.map((r) => (
            <button
              key={r.key}
              type="button"
              onClick={() => !isPreview && setRange(r)}
              style={{
                fontFamily: t.monoFont,
                fontSize: t.font.xs,
                fontWeight: t.weight.bold,
                padding: "3px 9px",
                borderRadius: t.radius.sm,
                border: "none",
                cursor: "pointer",
                background: range.key === r.key ? t.accent : hexToRgba(t.ink, 0.06),
                color: range.key === r.key ? pickContrast(t.accent) : t.inkDim,
              }}
            >
              {r.label}
            </button>
          ))}
        </div>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: t.space.md }}>
        {isLoading && !isPreview ? (
          <div style={{ fontSize: t.font.sm, color: t.inkDim, padding: "20px 0" }}>{cfg.loadingText}</div>
        ) : isEmpty ? (
          <div style={{ fontSize: t.font.sm, color: t.inkDim, padding: "20px 0" }}>{cfg.emptyText}</div>
        ) : (
          <>
            <div style={{ display: "flex", gap: t.space.lg }}>
              <div>
                <div style={{ fontSize: t.font.xs, color: t.inkMute }}>{cfg.lastLabel}</div>
                <div style={{ fontSize: t.font.xl, fontWeight: t.weight.bold, color: t.ink }}>
                  {last.toFixed(1)} {cfg.unitLabel}
                </div>
              </div>
              <div>
                <div style={{ fontSize: t.font.xs, color: t.inkMute }}>{cfg.peakLabel}</div>
                <div style={{ fontSize: t.font.xl, fontWeight: t.weight.bold, color: t.inkDim }}>
                  {peak.toFixed(1)} {cfg.unitLabel}
                </div>
              </div>
            </div>
            <div style={{ position: "relative", width: "100%", height: 120 }}>
              {gridLevels.map((frac) => (
                <div
                  key={frac}
                  style={{
                    position: "absolute",
                    left: 0,
                    right: 0,
                    top: `${97 - frac * 94}%`,
                    borderTop: `1px dashed ${hexToRgba(t.ink, 0.1)}`,
                    pointerEvents: "none",
                  }}
                />
              ))}
              <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", display: "block" }} aria-hidden>
                <path d={path.area} fill={hexToRgba(accent, 0.14)} stroke="none" />
                <path data-motion-draw="true" pathLength={1} d={path.line} fill="none" stroke={accent} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
              </svg>
              {gridLevels.map((frac) => (
                <span
                  key={frac}
                  style={{
                    position: "absolute",
                    right: 2,
                    top: `${97 - frac * 94}%`,
                    transform: frac === 1 ? "translateY(0)" : frac === 0 ? "translateY(-100%)" : "translateY(-50%)",
                    fontSize: t.font.xs,
                    color: t.inkMute,
                    background: hexToRgba(t.panel, 0.85),
                    padding: "0 4px",
                    borderRadius: t.radius.sm,
                    fontVariantNumeric: "tabular-nums",
                    pointerEvents: "none",
                  }}
                >
                  {(chartMax * frac).toFixed(1)} {cfg.unitLabel}
                </span>
              ))}
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: t.font.xs, color: t.inkMute, fontVariantNumeric: "tabular-nums" }}>
              <span>{points.length ? (range.granularity === "hour" ? fmtHour(points[0].date) : fmtDay(points[0].date)) : ""}</span>
              <span>{points.length ? (range.granularity === "hour" ? fmtHour(points[points.length - 1].date) : fmtDay(points[points.length - 1].date)) : ""}</span>
            </div>
          </>
        )}
      </div>
    </DefaultPanel>,
    false,
    true,
  );
}
