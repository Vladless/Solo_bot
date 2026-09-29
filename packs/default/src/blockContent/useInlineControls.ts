"use client";

import { useBalancedColumns } from "@/components/constructor/blockContent/useBalancedColumns";

/**
 * Поле ввода и кнопка рядом или друг под другом — по фактической ширине блока.
 * Если на два контрола не хватает места, поле обрезает свой placeholder, поэтому
 * в узком блоке они встают в столбик на всю ширину.
 */
export function useInlineControls(gap: number, minControlWidth = 240): {
  ref: (el: HTMLElement | null) => void;
  stack: boolean;
} {
  const { ref, columns } = useBalancedColumns(2, {
    minCardWidth: minControlWidth,
    maxCols: 2,
    gap,
    fill: true,
  });
  return { ref, stack: columns === 1 };
}
