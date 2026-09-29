"use client";

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { buttonShadow, pickContrast, CONTROL_H, CONTROL_R, type DefaultTheme } from "./defaultTheme";

const TRACK_PAD = 4;

export const SEGMENT_SLIDE_TRANSITION = "340ms cubic-bezier(.3,1.25,.5,1)";

type SegOption = { id: string; content: ReactNode };

export function SlidingSegmented({
  t,
  value,
  onChange,
  options,
  vertical = false,
  stretch = true,
  style,
}: {
  t: DefaultTheme;
  value: string;
  onChange: (id: string) => void;
  options: SegOption[];
  vertical?: boolean;
  stretch?: boolean;
  style?: CSSProperties;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const btnRefs = useRef(new Map<string, HTMLButtonElement>());
  const mountedRef = useRef(false);
  const [rect, setRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null);

  useEffect(() => {
    const measure = () => {
      const el = btnRefs.current.get(value);
      if (!el) {
        setRect(null);
        return;
      }
      setRect({ left: el.offsetLeft, top: el.offsetTop, width: el.offsetWidth, height: el.offsetHeight });
    };
    measure();
    const raf = requestAnimationFrame(() => {
      measure();
      mountedRef.current = true;
    });
    const wrap = wrapRef.current;
    if (!wrap || typeof ResizeObserver === "undefined") return () => cancelAnimationFrame(raf);
    const ro = new ResizeObserver(measure);
    ro.observe(wrap);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [value, options.length]);

  return (
    <div
      ref={wrapRef}
      style={{
        position: "relative",
        display: stretch ? "flex" : "inline-flex",
        flexDirection: vertical ? "column" : "row",
        gap: 4,
        background: t.innerBg,
        borderRadius: CONTROL_R,
        padding: TRACK_PAD,
        ...style,
      }}
    >
      {rect ? (
        <span
          aria-hidden
          style={{
            position: "absolute",
            left: rect.left,
            top: rect.top,
            width: rect.width,
            height: rect.height,
            borderRadius: 999,
            background: t.ink,
            boxShadow: buttonShadow(t),
            transition: mountedRef.current
              ? `left ${SEGMENT_SLIDE_TRANSITION}, top ${SEGMENT_SLIDE_TRANSITION}, width ${SEGMENT_SLIDE_TRANSITION}, height ${SEGMENT_SLIDE_TRANSITION}`
              : "none",
            pointerEvents: "none",
          }}
        />
      ) : null}
      {options.map((opt) => {
        const active = value === opt.id;
        return (
          <button
            key={opt.id}
            ref={(el) => {
              if (el) btnRefs.current.set(opt.id, el);
              else btnRefs.current.delete(opt.id);
            }}
            type="button"
            onClick={() => onChange(opt.id)}
            style={{
              position: "relative",
              zIndex: 1,
              flex: stretch && !vertical ? "1 1 0" : "0 0 auto",
              minWidth: 0,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              minHeight: t.px(CONTROL_H) - TRACK_PAD * 2,
              padding: `0 ${t.space.md}px`,
              border: "none",
              borderRadius: 999,
              background: "transparent",
              color: active ? pickContrast(t.ink) : t.inkDim,
              fontFamily: t.monoFont,
              fontSize: t.font.sm,
              fontWeight: active ? t.weight.bold : t.weight.medium,
              cursor: "pointer",
              transition: "color 200ms ease",
              whiteSpace: "nowrap",
            }}
          >
            {opt.content}
          </button>
        );
      })}
    </div>
  );
}
