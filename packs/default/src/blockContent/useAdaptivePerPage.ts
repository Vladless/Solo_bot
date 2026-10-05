"use client";

import { useEffect, useRef, useState } from "react";

export function useAdaptivePerPage({
  rowPx,
  reservedPx,
  enabled,
}: {
  rowPx: number;
  reservedPx: number;
  enabled: boolean;
}) {
  const areaRef = useRef<HTMLDivElement | null>(null);
  const [cap, setCap] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    const el = areaRef.current;
    if (!el) return;
    const measure = () => {
      const h = el.clientHeight;
      if (h < rowPx) return;
      setCap(Math.max(1, Math.floor((h - reservedPx) / rowPx)));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [enabled, rowPx, reservedPx]);

  return { areaRef, perPageCap: enabled && cap > 0 ? cap : Infinity };
}
