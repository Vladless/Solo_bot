import { type CSSProperties } from "react";
import { hexToRgba } from "@/components/constructor/utils";
import { usePageThemeTokens } from "@/lib/page-theme-tokens-context";
import { registerPackTheme, usePackTheme } from "@/components/constructor/blockContent/cabinetKit/packThemeRegistry";
import { DEFAULT_PACK_FONTS } from "./defaultFonts";
import {
  isHex,
  blendHex,
  pickContrast,
  resolveFontScale,
  scaleSteps,
  useIsMobile,
  usePackThemeTokens,
  type MonoTheme,
  type MonoSpacing,
  type MonoFontSize,
} from "@/components/constructor/blockContent/cabinetKit/theme";

export { isHex, blendHex, pickContrast } from "@/components/constructor/blockContent/cabinetKit/theme";
export { useIsMobile, useValueChangeFlash } from "@/components/constructor/blockContent/cabinetKit/theme";
export { f, parseBlockData } from "@/components/constructor/blockContent/cabinetKit/blockSchema";
export { pickBool } from "@/components/constructor/blockContent/cabinetKit/dataPickers";

export type DefaultTheme = MonoTheme & {
  isLight: boolean;
  /** Мягкая тень карточки: выключается в «Оформлении» блока (`vs_elevation: false`). */
  elevation: boolean;
  /** Начертание из вкладки «Текст» блока: применяется на корне панели. */
  textBold: boolean;
  textItalic: boolean;
  scale: number;
  px: (v: number) => number;
};

export const DEFAULT_PACK_ID = "default";

const DEFAULT_SANS_FONT = DEFAULT_PACK_FONTS.body;

const HEADING_FONT_KEYS = ["xl", "xxl", "display", "hero", "giant"];

const DEFAULT_SPACE: MonoSpacing = { xs: 4, sm: 8, smPlus: 10, md: 12, mdPlus: 14, lg: 16, xl: 24 };
const DEFAULT_SPACE_MOBILE: MonoSpacing = { xs: 3, sm: 6, smPlus: 8, md: 10, mdPlus: 12, lg: 14, xl: 18 };
const DEFAULT_RADIUS = { sm: 14, md: 22 };
const DEFAULT_FONT: MonoFontSize = {
  xxs: 10, xs: 11, xsPlus: 12, sm: 13, smPlus: 14, md: 15, lg: 16, xl: 18, xxl: 21, display: 25, hero: 30, giant: 34,
};
const DEFAULT_FONT_MOBILE: MonoFontSize = {
  xxs: 9, xs: 10, xsPlus: 11, sm: 12, smPlus: 13, md: 14, lg: 15, xl: 17, xxl: 19, display: 22, hero: 26, giant: 28,
};
const DEFAULT_TRACKING = { normal: "0", wide: "0.01em", tight: "0", ultra: "0.02em" };
const DEFAULT_WEIGHT = { regular: 400, medium: 500, bold: 700 };

const DEFAULT_INK_DARK = "#F4F5F7";
const DEFAULT_SURFACE_DARK = "#0A0A0A";
const DEFAULT_INK_LIGHT = "#0E1116";
const DEFAULT_SURFACE_LIGHT = "#F4F6FA";
const DEFAULT_ACCENT = "#FF7A1A";




/** Множитель из темы: за границами разумного игнорируем, иначе кабинет становится нечитаемым. */
function themeScale(raw: unknown): number {
  if (typeof raw !== "number" || !Number.isFinite(raw) || raw <= 0) return 1;
  return Math.max(0.4, Math.min(2.5, raw));
}

function resolveDefaultThemeForMode(d: Record<string, unknown>, isLightMode: boolean): DefaultTheme {
  const defaultInk = isLightMode ? DEFAULT_INK_LIGHT : DEFAULT_INK_DARK;
  const defaultSurface = isLightMode ? DEFAULT_SURFACE_LIGHT : DEFAULT_SURFACE_DARK;

  const accent = isHex(d.accentColor) ? (d.accentColor as string) : DEFAULT_ACCENT;
  const ink = isHex(d.inkColor) ? (d.inkColor as string) : defaultInk;
  const surface = isHex(d.surfaceColor) ? (d.surfaceColor as string) : defaultSurface;
  const panelBlend = isLightMode ? 0.0 : 0.1;
  const blockFont = typeof d.fontFamily === "string" ? d.fontFamily.trim() : "";
  const rawFont = typeof d.monoFontFamily === "string" ? d.monoFontFamily.trim() : "";
  const customFont = rawFont && !/mono/i.test(rawFont) ? rawFont : "";
  return {
    monoFont: blockFont || customFont || DEFAULT_SANS_FONT,
    accent,
    ink,
    inkDim: isHex(d.inkDimColor) ? (d.inkDimColor as string) : hexToRgba(ink, 0.62),
    inkMute: isHex(d.inkMuteColor) ? (d.inkMuteColor as string) : hexToRgba(ink, isLightMode ? 0.58 : 0.4),
    line: isHex(d.lineColor) ? (d.lineColor as string) : hexToRgba(ink, 0.08),
    lineStrong: isHex(d.lineStrongColor) ? (d.lineStrongColor as string) : hexToRgba(ink, 0.16),
    surface,
    panel: isHex(d.panelColor) ? (d.panelColor as string) : (isLightMode ? "#FFFFFF" : blendHex(surface, ink, panelBlend)),
    success: isHex(d.successColor) ? (d.successColor as string) : (isLightMode ? "#0F6E32" : "#2DD46F"),
    warn: isHex(d.warnColor) ? (d.warnColor as string) : (isLightMode ? "#B45309" : "#FFC107"),
    error: isHex(d.errorColor) ? (d.errorColor as string) : (isLightMode ? "#C81E1E" : "#FF5A5A"),
    innerBg: isLightMode ? blendHex(surface, "#000000", 0.03) : blendHex(surface, ink, 0.16),
    space: DEFAULT_SPACE,
    radius: DEFAULT_RADIUS,
    font: DEFAULT_FONT,
    tracking: DEFAULT_TRACKING,
    weight: DEFAULT_WEIGHT,
    isLight: isLightMode,
    elevation: d.vs_elevation !== false,
    textBold: d.textBold === true,
    textItalic: d.textItalic === true,
    scale: 1,
    px: (v: number) => v,
  };
}

export function useDefaultTheme(d: Record<string, unknown>): DefaultTheme {
  const pageTokens = usePageThemeTokens();
  const merged = usePackThemeTokens(d);
  const base = usePackTheme<DefaultTheme>(DEFAULT_PACK_ID, merged);
  const isMobile = useIsMobile();
  const blockScale = resolveFontScale(d);
  const scale = blockScale * (isMobile ? themeScale(pageTokens?.mobileTextScale) : 1);
  const headingScale = blockScale * (isMobile ? themeScale(pageTokens?.mobileHeadingScale) : 1);
  return {
    ...base,
    space: scaleSteps(isMobile ? DEFAULT_SPACE_MOBILE : base.space, scale, { min: 1 }),
    font: scaleSteps(isMobile ? DEFAULT_FONT_MOBILE : base.font, scale, {
      min: 8,
      headingKeys: HEADING_FONT_KEYS,
      headingScale,
    }),
    scale,
    px: (v: number) => Math.max(1, Math.round(v * scale)),
  };
}

/**
 * Палитра пака, пересчитанная под свою подложку: текст, линии и внутренние плашки берут
 * контраст от переданного цвета. Нужна блокам, которые красятся не в цвет карточки —
 * например рельс сайдбара в цвете шапки сайта.
 */
export function defaultThemeOnSurface(base: DefaultTheme, surface: string): DefaultTheme {
  if (!isHex(surface) || surface.toLowerCase() === base.panel.toLowerCase()) return base;
  const ink = pickContrast(surface);
  return {
    ...base,
    isLight: ink === "#0a0a0a",
    ink,
    inkDim: hexToRgba(ink, 0.62),
    inkMute: hexToRgba(ink, 0.4),
    line: hexToRgba(ink, 0.1),
    lineStrong: hexToRgba(ink, 0.18),
    surface,
    panel: surface,
    innerBg: blendHex(surface, ink, 0.12),
  };
}

export function resolveDefaultTheme(d: Record<string, unknown>): DefaultTheme {
  return resolveDefaultThemeForMode(d, false);
}

export function panelShadow(t: DefaultTheme): string {
  if (!t.elevation) return "none";
  if (t.isLight) {
    return `0 1px 3px rgba(16,24,40,0.06), 0 8px 20px rgba(16,24,40,0.08), 0 22px 48px -6px rgba(16,24,40,0.12), inset 0 0 0 1px rgba(16,24,40,0.04)`;
  }
  return `0 2px 8px rgba(0,0,0,0.5), 0 20px 48px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.05)`;
}

/**
 * Та же тень, но без внутреннего ободка: для поверхностей, которые стыкуются с шапкой или краем
 * экрана — иначе inset-линия читается как разделитель на стыке.
 */
export function panelShadowFlush(t: DefaultTheme): string {
  return panelShadow(t)
    .split(", ")
    .filter((part) => !part.includes("inset"))
    .join(", ") || "none";
}

export function panelShadowSm(t: DefaultTheme): string {
  if (!t.elevation) return "none";
  if (t.isLight) {
    return `0 1px 3px rgba(16,24,40,0.06), 0 6px 16px rgba(16,24,40,0.07), 0 14px 32px -6px rgba(16,24,40,0.10), inset 0 0 0 1px rgba(16,24,40,0.04)`;
  }
  return `0 1px 5px rgba(0,0,0,0.45), 0 14px 34px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.04)`;
}

export function cardEdge(t: DefaultTheme): string {
  return t.isLight ? `inset 0 0 0 1px rgba(16,24,40,0.04)` : `inset 0 1px 0 rgba(255,255,255,0.04)`;
}

export function buttonShadow(t: DefaultTheme): string {
  if (t.isLight) {
    return `0 1px 1px rgba(16,24,40,0.05), 0 2px 4px -1px rgba(16,24,40,0.08), inset 0 0 0 1px rgba(16,24,40,0.06)`;
  }
  return `0 1px 2px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.06)`;
}

export function panelStyle(t: DefaultTheme): CSSProperties {
  return {
    borderRadius: `var(--block-visual-radius, ${t.radius.md}px)`,
    background: t.panel,
    boxShadow: panelShadow(t),
    fontFamily: t.monoFont,
    fontWeight: t.textBold ? t.weight.bold : undefined,
    fontStyle: t.textItalic ? "italic" : undefined,
    color: t.ink,
    display: "flex",
    flexDirection: "column",
    minHeight: 0,
    height: "100%",
    overflow: "hidden",
  };
}

export function panelHeaderStyle(t: DefaultTheme): CSSProperties {
  return {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: `${t.px(24)}px ${t.px(26)}px ${t.px(4)}px`,
    fontSize: t.font.lg,
    fontWeight: t.weight.bold,
    color: t.ink,
    letterSpacing: t.tracking.normal,
    flex: "0 0 auto",
  };
}

export function panelBodyStyle(t?: DefaultTheme): CSSProperties {
  const px = t?.px ?? ((v: number) => v);
  return {
    padding: `${px(14)}px ${px(26)}px ${px(26)}px`,
    flex: "1 1 auto",
    minHeight: 0,
  };
}

export function rowDividerStyle(t: DefaultTheme): CSSProperties {
  return { borderBottom: `1px solid ${t.line}` };
}

export const CONTROL_H = 44;
export const CONTROL_R = "var(--site-button-radius, 9999px)";
export const NAV_R = "var(--nav-radius-val, 9999px)";

export function btnSolid(t: DefaultTheme): CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: t.px(8),
    minHeight: t.px(CONTROL_H),
    padding: `0 ${t.px(20)}px`,
    border: "none",
    background: t.accent,
    color: pickContrast(t.accent),
    fontFamily: t.monoFont,
    fontWeight: t.weight.bold,
    fontSize: t.font.sm,
    letterSpacing: t.tracking.normal,
    cursor: "pointer",
    borderRadius: CONTROL_R,
    transition: "filter 160ms ease, transform 160ms ease",
  };
}

export function btnSecondary(t: DefaultTheme): CSSProperties {
  return {
    ...btnSolid(t),
    background: t.isLight ? t.panel : t.innerBg,
    color: t.ink,
    boxShadow: buttonShadow(t),
  };
}

export function btnGhost(t: DefaultTheme): CSSProperties {
  return {
    ...btnSolid(t),
    background: t.innerBg,
    color: t.ink,
  };
}

export function btnRed(t: DefaultTheme): CSSProperties {
  return {
    ...btnSolid(t),
    background: t.accent,
    color: pickContrast(t.accent),
  };
}

export function btnSm(t?: DefaultTheme): CSSProperties {
  const px = t?.px ?? ((v: number) => v);
  return { minHeight: px(36), padding: `0 ${px(14)}px`, fontSize: px(13) };
}

export function actionsRow(isMobile: boolean): CSSProperties {
  return isMobile ? { width: "100%", justifyContent: "center" } : {};
}

export function actionsItem(isMobile: boolean): CSSProperties {
  return isMobile ? { flex: "1 1 0", minWidth: 0 } : {};
}

export function actionsSolo(isMobile: boolean): CSSProperties {
  return isMobile ? { alignSelf: "stretch", width: "100%" } : {};
}

export function linkBtn(t: DefaultTheme): CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: t.px(4),
    background: "transparent",
    border: "none",
    padding: 0,
    color: t.accent,
    fontFamily: t.monoFont,
    fontWeight: t.weight.bold,
    fontSize: t.font.sm,
    cursor: "pointer",
    textDecoration: "none",
    whiteSpace: "nowrap",
  };
}

export function pillStyle(t: DefaultTheme, color: string): CSSProperties {
  return {
    fontSize: t.font.xs,
    fontWeight: t.weight.bold,
    letterSpacing: t.tracking.normal,
    padding: `${t.px(5)}px ${t.px(11)}px`,
    borderRadius: 999,
    background: hexToRgba(color, 0.12),
    color,
    fontFamily: t.monoFont,
    whiteSpace: "nowrap",
    display: "inline-flex",
    alignItems: "center",
  };
}

export function iconSquareStyle(t: DefaultTheme, tint: string, size = 44): CSSProperties {
  return {
    width: t.px(size),
    height: t.px(size),
    borderRadius: t.radius.sm,
    background: hexToRgba(tint, 0.15),
    color: tint,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
  };
}

export function avatarGradientStyle(t: DefaultTheme, size = 56): CSSProperties {
  const box = t.px(size);
  return {
    width: box,
    height: box,
    borderRadius: Math.round(box * 0.32),
    background: `linear-gradient(135deg, ${blendHex(t.accent, "#FFFFFF", 0.12)}, ${blendHex(t.accent, "#000000", 0.12)})`,
    color: pickContrast(t.accent),
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontWeight: t.weight.bold,
    flexShrink: 0,
    boxShadow: `0 6px 16px -6px ${hexToRgba(t.accent, 0.5)}`,
  };
}

export function rowGridStyle(t?: DefaultTheme): CSSProperties {
  const px = t?.px ?? ((v: number) => v);
  return {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: `${px(14)}px 0`,
    gap: px(16),
  };
}

export function toggleTrackStyle(t: DefaultTheme, on: boolean): CSSProperties {
  return {
    width: t.px(46),
    height: t.px(28),
    borderRadius: 999,
    background: on ? t.accent : hexToRgba(t.ink, 0.16),
    position: "relative",
    flexShrink: 0,
    cursor: "pointer",
    transition: "background 180ms ease",
    border: "none",
    padding: 0,
  };
}

export function toggleKnobStyle(t: DefaultTheme, on: boolean): CSSProperties {
  return {
    position: "absolute",
    top: t.px(3),
    left: on ? t.px(21) : t.px(3),
    width: t.px(22),
    height: t.px(22),
    borderRadius: 999,
    background: "#FFFFFF",
    boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
    transition: "left 180ms cubic-bezier(0.2,0.7,0.2,1)",
  };
}

export function statTileStyle(t: DefaultTheme): CSSProperties {
  return {
    borderRadius: t.radius.md,
    background: t.panel,
    boxShadow: panelShadowSm(t),
    padding: `${t.px(20)}px ${t.px(20)}px`,
    fontFamily: t.monoFont,
    color: t.ink,
    display: "flex",
    flexDirection: "column",
    gap: t.px(10),
    minHeight: 0,
  };
}

registerPackTheme(DEFAULT_PACK_ID, (d, isLightMode) => resolveDefaultThemeForMode(d, isLightMode));
