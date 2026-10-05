"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { panelShadow, btnSolid, type DefaultTheme } from "./defaultTheme";
import { useLoadedFade } from "@/components/constructor/blockContent/useLoadedFade";

export function DefaultQrModal({
  t,
  open,
  onClose,
  qrImg,
  loading = false,
  error = "",
  caption = "",
  title = "",
  closeLabel = "Закрыть",
  loadingText = "Загрузка…",
}: {
  t: DefaultTheme;
  open: boolean;
  onClose: () => void;
  qrImg: string | null;
  loading?: boolean;
  error?: string;
  caption?: string;
  title?: string;
  closeLabel?: string;
  loadingText?: string;
}) {
  const [shown, setShown] = useState(false);
  const [mounted, setMounted] = useState(open);
  const loadedFade = useLoadedFade(loading);

  useEffect(() => {
    if (open) {
      setMounted(true);
      let r2 = 0;
      const r1 = requestAnimationFrame(() => {
        r2 = requestAnimationFrame(() => setShown(true));
      });
      return () => {
        cancelAnimationFrame(r1);
        cancelAnimationFrame(r2);
      };
    }
    setShown(false);
    const timer = window.setTimeout(() => setMounted(false), 260);
    return () => window.clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!mounted || typeof document === "undefined") return null;

  return createPortal(
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 100000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 20,
        background: "rgba(8,10,16,0.62)",
        backdropFilter: "blur(6px)",
        WebkitBackdropFilter: "blur(6px)",
        opacity: shown ? 1 : 0,
        transition: "opacity 220ms ease",
        pointerEvents: open ? "auto" : "none",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "100%",
          maxWidth: 340,
          background: t.panel,
          color: t.ink,
          borderRadius: t.radius.md,
          boxShadow: panelShadow(t),
          fontFamily: t.monoFont,
          padding: "22px 22px 20px",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 16,
          opacity: shown ? 1 : 0,
          transform: shown ? "scale(1) translateY(0)" : "scale(0.92) translateY(8px)",
          transition: "opacity 220ms ease, transform 240ms cubic-bezier(0.2,0.8,0.2,1)",
        }}
      >
        {title ? (
          <div style={{ fontSize: t.font.lg, fontWeight: t.weight.bold, color: t.ink, textAlign: "center" }}>{title}</div>
        ) : null}
        <div
          style={{
            width: "100%",
            aspectRatio: "1 / 1",
            maxWidth: 240,
            background: "#FFFFFF",
            borderRadius: t.radius.sm,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 14,
            boxSizing: "border-box",
          }}
        >
          {loading ? (
            <span style={{ color: "#6b7280", fontSize: t.font.sm }}>{loadingText}</span>
          ) : error ? (
            <span className={loadedFade} style={{ color: "#dc2626", fontSize: t.font.sm, textAlign: "center" }}>{error}</span>
          ) : qrImg ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img className={`${loadedFade} showcase-sensitive`} src={qrImg} alt="QR" style={{ display: "block", width: "100%", height: "100%", objectFit: "contain" }} />
          ) : null}
        </div>
        {caption ? (
          <p style={{ margin: 0, color: t.inkDim, fontSize: t.font.sm, lineHeight: 1.5, textAlign: "center" }}>{caption}</p>
        ) : null}
        <button
          type="button"
          onClick={onClose}
          style={{ ...btnSolid(t), alignSelf: "stretch", cursor: "pointer" }}
        >
          {closeLabel}
        </button>
      </div>
    </div>,
    document.body,
  );
}
