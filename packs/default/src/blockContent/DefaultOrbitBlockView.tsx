"use client";

import { useEffect, useRef, useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, pickContrast, cardEdge, f, parseBlockData } from ".";
import { hexToRgba } from "@/components/constructor/utils";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";

const ORBIT_SCHEMA = {
  centerLabel: f.str(""),
  nodeLabels: f.str("AMS, FRA, NYC, LON, TYO, SGP"),
  spinSeconds: f.num(46),
  showPulse: f.bool(true),
};

const ORBIT_CSS = `
@keyframes d-orbit-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
@keyframes d-orbit-spin-rev { from { transform: rotate(0deg); } to { transform: rotate(-360deg); } }
@keyframes d-orbit-ping {
  0% { transform: scale(1); opacity: 0.45; }
  80% { transform: scale(2.4); opacity: 0; }
  100% { transform: scale(2.4); opacity: 0; }
}
`;

export function DefaultOrbitBlockView({ block, context }: TypedBlockViewProps<"defaultOrbit">) {
  const decor = usePanelDecor();
  const { wrap } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, ORBIT_SCHEMA);

  const labels = cfg.nodeLabels.split(",").map((s) => s.trim()).filter(Boolean).slice(0, 12);
  const spin = Math.max(8, Math.min(180, cfg.spinSeconds));
  const rings = [
    { size: 46, duration: spin * 0.7, reverse: false },
    { size: 67, duration: spin, reverse: true },
    { size: 87, duration: spin * 1.45, reverse: false },
  ];
  const counts = [0, 0, 0];
  labels.forEach((_, i) => {
    counts[i % 3] += 1;
  });
  const ringNodes: { label: string; angle: number }[][] = [[], [], []];
  let idx = 0;
  for (let ring = 0; ring < 3; ring += 1) {
    for (let k = 0; k < counts[ring]; k += 1) {
      ringNodes[ring].push({ label: labels[idx], angle: (360 / counts[ring]) * k + ring * 40 });
      idx += 1;
    }
  }

  const onAccent = pickContrast(t.accent);
  const core = (s: number) => Math.max(56, Math.min(112, Math.round(s * 0.17)));

  const hostRef = useRef<HTMLDivElement | null>(null);
  const [side, setSide] = useState<number | null>(null);
  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const cell = (el.closest("[data-block-cell]") as HTMLElement | null) ?? el.parentElement;
    if (!cell) return;
    const measure = () => setSide(Math.max(120, Math.min(cell.clientWidth, cell.clientHeight)));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(cell);
    return () => ro.disconnect();
  }, []);

  const content = (
    <div
      ref={hostRef}
      style={{
        width: side ? `${side}px` : "100%",
        height: side ? `${side}px` : "100%",
        maxWidth: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: t.monoFont,
        minHeight: 0,
        minWidth: 0,
        margin: "0 auto",
        ...decor,
      }}
    >
      <style dangerouslySetInnerHTML={{ __html: ORBIT_CSS }} />
      <div
        style={{
          position: "relative",
          width: side ? `${side}px` : "0px",
          aspectRatio: "1 / 1",
          flexShrink: 0,
        }}
      >
        {rings.map((ring, ri) => (
          <div
            key={`ring-${ri}`}
            aria-hidden
            style={{
              position: "absolute",
              top: "50%",
              left: "50%",
              width: `${ring.size}%`,
              height: `${ring.size}%`,
              transform: "translate(-50%, -50%)",
              borderRadius: "50%",
              border: `1px solid ${hexToRgba(t.ink, 0.1)}`,
            }}
          />
        ))}
        {rings.map((ring, ri) => (
          <div
            key={`orbit-${ri}`}
            style={{
              position: "absolute",
              top: `${(100 - ring.size) / 2}%`,
              left: `${(100 - ring.size) / 2}%`,
              width: `${ring.size}%`,
              height: `${ring.size}%`,
            }}
          >
            <div
              className="d-orbit-anim"
              style={{
                position: "absolute",
                inset: 0,
                animation: `${ring.reverse ? "d-orbit-spin-rev" : "d-orbit-spin"} ${ring.duration}s linear infinite`,
              }}
            >
              {ringNodes[ri].map((n) => (
                <div key={n.label} style={{ position: "absolute", inset: 0, transform: `rotate(${n.angle}deg)` }}>
                  <div style={{ position: "absolute", top: 0, left: "50%", transform: "translate(-50%, -50%)" }}>
                    <div
                      className="d-orbit-anim"
                      style={{
                        animation: `${ring.reverse ? "d-orbit-spin" : "d-orbit-spin-rev"} ${ring.duration}s linear infinite`,
                      }}
                    >
                      <span
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: 6,
                          padding: "5px 10px",
                          borderRadius: 999,
                          background: t.panel,
                          boxShadow: cardEdge(t),
                          color: t.inkDim,
                          fontSize: t.font.xxs,
                          fontWeight: t.weight.medium,
                          whiteSpace: "nowrap",
                          transform: `rotate(${-n.angle}deg)`,
                        }}
                      >
                        <span aria-hidden data-motion-pulse="true" style={{ width: 6, height: 6, borderRadius: 999, background: t.success, flexShrink: 0 }} />
                        {n.label}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
        <span
          style={{
            position: "absolute",
            top: "50%",
            left: "50%",
            transform: "translate(-50%, -50%)",
            width: side ? core(side) : 64,
            height: side ? core(side) : 64,
            borderRadius: side ? Math.round(core(side) * 0.31) : 20,
            background: t.accent,
            color: onAccent,
            display: "grid",
            placeItems: "center",
            zIndex: 2,
          }}
        >
          <svg width={side ? Math.round(core(side) * 0.47) : 30} height={side ? Math.round(core(side) * 0.47) : 30} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3l7 3v5c0 4.5-3 8.2-7 9.5C8 19.2 5 15.5 5 11V6l7-3Z" />
            <path d="M9.2 11.8l2 2 3.6-4" />
          </svg>
          {cfg.showPulse ? (
            <>
              <span className="d-orbit-anim" aria-hidden style={{ position: "absolute", inset: 0, borderRadius: "31%", background: hexToRgba(t.accent, 0.5), animation: "d-orbit-ping 3.2s cubic-bezier(0.2, 0.6, 0.4, 1) infinite", zIndex: -1 }} />
              <span className="d-orbit-anim" aria-hidden style={{ position: "absolute", inset: 0, borderRadius: "31%", background: hexToRgba(t.accent, 0.5), animation: "d-orbit-ping 3.2s cubic-bezier(0.2, 0.6, 0.4, 1) 1.6s infinite", zIndex: -1 }} />
            </>
          ) : null}
        </span>
        {cfg.centerLabel ? (
          <span
            style={{
              position: "absolute",
              top: "calc(50% + 42px)",
              left: "50%",
              transform: "translateX(-50%)",
              fontSize: t.font.xs,
              color: t.inkDim,
              whiteSpace: "nowrap",
              zIndex: 2,
            }}
          >
            {cfg.centerLabel}
          </span>
        ) : null}
      </div>
    </div>
  );

  return wrap ? wrap(content, false, true) : content;
}
