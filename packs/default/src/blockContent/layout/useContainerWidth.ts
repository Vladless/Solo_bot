"use client";

import { useEffect, useState } from "react";

/** Фактический размер блока: по нему контент решает, что показывать и в каком масштабе. */
export function useContainerSize(): {
  ref: (el: HTMLElement | null) => void;
  width: number;
  height: number;
} {
  const [el, setEl] = useState<HTMLElement | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    if (!el) return;
    const measure = () => {
      const next = { width: el.clientWidth, height: el.clientHeight };
      setSize((prev) => (prev.width === next.width && prev.height === next.height ? prev : next));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);

  return { ref: setEl, width: size.width, height: size.height };
}

/** Только ширина — для блоков, которым важна лишь она. */
export function useContainerWidth(): { ref: (el: HTMLElement | null) => void; width: number } {
  const { ref, width } = useContainerSize();
  return { ref, width };
}
