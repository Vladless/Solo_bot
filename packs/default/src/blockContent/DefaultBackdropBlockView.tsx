"use client";

import { useEffect, useRef, useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme } from "./defaultTheme";
import { hexToRgba } from "@/components/constructor/utils";
import { usePanelDecor } from "@/components/constructor/blockContent/panelDecor";

type Node = { x: number; y: number; ox: number; oy: number; phase: number; hot: boolean };

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

export function DefaultBackdropBlockView({ block, context }: TypedBlockViewProps<"defaultBackdrop">) {
  const decor = usePanelDecor();
  const d = block.data as Record<string, unknown>;
  const wrap = context.wrap;
  const t = useDefaultTheme(d);

  const accent = t.accent;
  const dotColor = hexToRgba(t.ink, 0.4);
  const lineColor = hexToRgba(t.ink, 0.05);
  const textColor = t.inkDim;
  const bgColor = t.surface;
  const showStatusBar = d.showStatusBar !== false;
  const showCanvas = d.showCanvas !== false;
  const showLiveTime = d.showLiveTime === true;
  const cols = Math.max(8, Math.min(48, Number(d.cols) || 18));
  const rows = Math.max(4, Math.min(24, Number(d.rows) || 9));
  const hotRatio = Math.max(0, Math.min(0.5, Number(d.hotRatio) || 0.04));

  const statusLeft = String(d.statusLeft ?? "Онлайн");
  const statusCenterBase = String(d.statusCenter ?? "Защищённое соединение");
  const statusRight = String(d.statusRight ?? "v4.2");

  const [now, setNow] = useState<string>("");
  useEffect(() => {
    if (!showLiveTime) return;
    const tick = () => {
      const date = new Date();
      setNow(`${pad(date.getHours())}:${pad(date.getMinutes())}`);
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [showLiveTime]);

  const statusCenter = showLiveTime && now ? `${statusCenterBase} · ${now}` : statusCenterBase;

  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    if (!showCanvas) return;
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const ctxNullable = canvas.getContext("2d");
    if (!ctxNullable) return;
    const ctx: CanvasRenderingContext2D = ctxNullable;
    const cnv: HTMLCanvasElement = canvas;
    const box: HTMLDivElement = container;

    let raf = 0;
    let w = 0;
    let h = 0;
    let dpr = 1;
    let nodes: Node[] = [];

    function build() {
      dpr = window.devicePixelRatio || 1;
      const rect = box.getBoundingClientRect();
      w = Math.max(1, Math.round(rect.width));
      h = Math.max(1, Math.round(rect.height));
      cnv.width = Math.max(1, Math.floor(w * dpr));
      cnv.height = Math.max(1, Math.floor(h * dpr));
      cnv.style.width = `${w}px`;
      cnv.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const next: Node[] = [];
      const padX = 40;
      const padY = 40;
      for (let y = 0; y < rows; y++) {
        for (let x = 0; x < cols; x++) {
          const px = padX + (cols > 1 ? (x / (cols - 1)) * (w - padX * 2) : w / 2);
          const py = padY + (rows > 1 ? (y / (rows - 1)) * (h - padY * 2) : h / 2);
          next.push({
            x: px,
            y: py,
            ox: px,
            oy: py,
            phase: Math.random() * Math.PI * 2,
            hot: Math.random() < hotRatio,
          });
        }
      }
      nodes = next;
    }

    let time = 0;
    function draw() {
      if (w < 2 || h < 2) {
        raf = requestAnimationFrame(draw);
        return;
      }
      time += 0.01;
      ctx.clearRect(0, 0, w, h);

      ctx.strokeStyle = lineColor;
      ctx.lineWidth = 1;
      ctx.beginPath();
      const stepX = w / 24;
      const stepY = h / 12;
      for (let x = 0; x <= 24; x++) {
        ctx.moveTo(x * stepX, 0);
        ctx.lineTo(x * stepX, h);
      }
      for (let y = 0; y <= 12; y++) {
        ctx.moveTo(0, y * stepY);
        ctx.lineTo(w, y * stepY);
      }
      ctx.stroke();

      const wave = Math.sin(time * 0.6) * 70;
      const pulseY = h * 0.5 + wave;

      for (const n of nodes) {
        n.x = n.ox + Math.cos(time + n.phase) * 1.1;
        n.y = n.oy + Math.sin(time * 0.8 + n.phase) * 1.1;
        const distToPulse = Math.abs(n.y - pulseY);
        const isPulse = distToPulse < 8;
        ctx.fillStyle = n.hot ? accent : isPulse ? accent : dotColor;
        const r = n.hot ? 1.7 : isPulse ? 1.5 : 0.9;
        ctx.globalAlpha = n.hot || isPulse ? 0.85 : 0.6;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      const hub = nodes[Math.floor(nodes.length / 2) + 4] ?? nodes[Math.floor(nodes.length / 2)] ?? nodes[0];
      if (hub) {
        ctx.strokeStyle = accent;
        ctx.lineWidth = 1;
        ctx.globalAlpha = 0.22 + Math.sin(time * 2) * 0.1;
        for (const n of nodes) {
          if (!n.hot) continue;
          const dx = n.x - hub.x;
          const dy = n.y - hub.y;
          const dist = Math.hypot(dx, dy);
          if (dist < 260) {
            ctx.beginPath();
            ctx.moveTo(hub.x, hub.y);
            ctx.lineTo(n.x, n.y);
            ctx.stroke();
          }
        }
        ctx.globalAlpha = 1;

        ctx.strokeStyle = accent;
        ctx.lineWidth = 1.2;
        const ringR = 12 + Math.sin(time * 2.4) * 4;
        ctx.beginPath();
        ctx.arc(hub.x, hub.y, ringR, 0, Math.PI * 2);
        ctx.stroke();
        ctx.fillStyle = accent;
        ctx.beginPath();
        ctx.arc(hub.x, hub.y, 2.2, 0, Math.PI * 2);
        ctx.fill();
      }

      raf = requestAnimationFrame(draw);
    }

    build();
    draw();
    const observer = new ResizeObserver(() => build());
    observer.observe(box);
    window.addEventListener("resize", build);
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      window.removeEventListener("resize", build);
    };
  }, [showCanvas, accent, dotColor, lineColor, cols, rows, hotRatio]);

  const content = (
    <div
      ref={containerRef}
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        minHeight: 240,
        overflow: "hidden",
        backgroundColor: bgColor,
        borderRadius: t.radius.md,
        fontFamily: t.monoFont,
        color: textColor,
        ...decor,
      }}
    >
      {showCanvas ? (
        <canvas ref={canvasRef} aria-hidden style={{ position: "absolute", inset: 0, pointerEvents: "none" }} />
      ) : null}
      {showStatusBar ? (
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "14px 20px",
            fontSize: t.font.xs,
            letterSpacing: t.tracking.normal,
            zIndex: 2,
          }}
        >
          <span style={{ color: accent, fontWeight: t.weight.medium }}>{statusLeft}</span>
          <span>{statusCenter}</span>
          <span>{statusRight}</span>
        </div>
      ) : null}
    </div>
  );

  return wrap ? wrap(content, false, true) : content;
}
