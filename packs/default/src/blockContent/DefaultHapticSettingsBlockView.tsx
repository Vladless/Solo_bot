"use client";

import { useEffect, useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, DefaultPanel, SlidingSegmented, f, parseBlockData } from ".";
import { getHapticPref, setHapticPref, hapticImpact, hapticSupported, type HapticPref } from "@/lib/telegram-webapp";

const SCHEMA = {
  panelHeader: f.str("Вибрация"),
  hint: f.str(""),
  offLabel: f.str("Выкл"),
  lightLabel: f.str("Слабый"),
  mediumLabel: f.str("Средний"),
  heavyLabel: f.str("Сильный"),
  unsupportedLabel: f.str("Устройство не поддерживает вибрацию — отклик будет только в приложении Telegram."),
};

export function DefaultHapticSettingsBlockView({ block, context }: TypedBlockViewProps<"defaultHapticSettings">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, SCHEMA);
  const [pref, setPref] = useState<HapticPref>("medium");
  const [supported, setSupported] = useState(true);

  useEffect(() => {
    setPref(getHapticPref());
    setSupported(hapticSupported());
    const onChange = () => setPref(getHapticPref());
    window.addEventListener("soloweb:haptic-changed", onChange);
    return () => window.removeEventListener("soloweb:haptic-changed", onChange);
  }, []);

  const options: Array<{ id: HapticPref; label: string }> = [
    { id: "off", label: cfg.offLabel },
    { id: "light", label: cfg.lightLabel },
    { id: "medium", label: cfg.mediumLabel },
    { id: "heavy", label: cfg.heavyLabel },
  ];

  const pick = (id: HapticPref) => {
    if (previewMode) return;
    setHapticPref(id);
    setPref(id);
    hapticImpact();
  };

  return wrap(
    <DefaultPanel t={t} header={cfg.panelHeader} hint={cfg.hint || undefined}>
      <SlidingSegmented
        t={t}
        value={pref}
        onChange={(id) => pick(id as HapticPref)}
        options={options.map((opt) => ({ id: opt.id, content: opt.label }))}
      />
      {!previewMode && !supported ? (
        <div style={{ fontSize: t.font.xs, color: t.inkMute, lineHeight: 1.5 }}>{cfg.unsupportedLabel}</div>
      ) : null}
    </DefaultPanel>,
    false,
    true,
  );
}
