"use client";

import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, DefaultPanel, SlidingSegmented, f, parseBlockData } from ".";
import { useThemeMode, type ThemeMode } from "@/lib/theme-mode";
import { hapticImpact } from "@/lib/telegram-webapp";

const SCHEMA = {
  panelHeader: f.str("Тема"),
  hint: f.str(""),
  lightLabel: f.str("Светлая"),
  darkLabel: f.str("Тёмная"),
  autoLabel: f.str("Системная"),
};

export function DefaultThemeModeBlockView({ block, context }: TypedBlockViewProps<"defaultThemeMode">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, SCHEMA);
  const { mode, setMode } = useThemeMode();

  const options: Array<{ id: ThemeMode; label: string }> = [
    { id: "light", label: cfg.lightLabel },
    { id: "dark", label: cfg.darkLabel },
    { id: "auto", label: cfg.autoLabel },
  ];
  const pick = (id: ThemeMode) => {
    if (previewMode) return;
    setMode(id);
    hapticImpact();
  };

  return wrap(
    <DefaultPanel t={t} header={cfg.panelHeader} hint={cfg.hint || undefined}>
      <SlidingSegmented
        t={t}
        value={mode}
        onChange={(id) => pick(id as ThemeMode)}
        options={options.map((opt) => ({ id: opt.id, content: opt.label }))}
      />
    </DefaultPanel>,
    false,
    true,
  );
}
