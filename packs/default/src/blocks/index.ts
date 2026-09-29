import type { ElementDefinition } from "@/components/constructor/elementDefinitions";
import { registerPackTraits } from "@/components/constructor/packTraits";
import { tariffPrefetch } from "@/components/constructor/blocks/surfaces";
import { DEFAULT_PACK_FONTS } from "../blockContent/defaultFonts";
import { DEFAULT_PACK_COLOR_FIELDS } from "../blockContent/defaultPalette";
import { PACK_PANEL_COLOR_KEYS, PACK_PANEL_SIZE_KEYS } from "@/components/constructor/blockContent/blockPanelFields";
import { DEFAULT_CABINET_SETTINGS_BLOCKS } from "./cabinet-settings";
import { DEFAULT_CABINET_PROFILE_BLOCKS } from "./cabinet-profile";
import { DEFAULT_SUBSCRIPTION_BLOCKS } from "./subscription";
import { DEFAULT_PARTNER_BLOCKS } from "./partner";
import { DEFAULT_BONUS_BLOCKS } from "./bonus";
import { DEFAULT_SUPPORT_BLOCKS } from "./support";
import { DEFAULT_CHROME_BLOCKS } from "./chrome";
import { DEFAULT_LANDING_BLOCKS } from "./landing";

registerPackTraits("default", {
  panelRadiusPx: 22,
  contentAnim: { type: "soft", ms: 300 },
  interactionMotion: "soft",
  themeFonts: DEFAULT_PACK_FONTS,
  ownsPalette: true,
  colorFields: [...DEFAULT_PACK_COLOR_FIELDS],
  panelColors: PACK_PANEL_COLOR_KEYS,
  panelSizes: PACK_PANEL_SIZE_KEYS,
});

/** Блоки «Стандарта» красятся палитрой набора — она же уходит в панели цвета и размера. */
const DEFAULT_PACK_SURFACE = { colors: PACK_PANEL_COLOR_KEYS, sizes: PACK_PANEL_SIZE_KEYS };

/** Блоки, которые греют тарифы своей группы заранее. */
const TARIFF_PREFETCH_TYPES = ["defaultGiftCta", "defaultTariffSwitcher", "defaultGiftCardsGrid", "defaultTariffPanel"];

export const CABINET_DEFAULT_BLOCKS: ElementDefinition[] = [
  ...DEFAULT_CABINET_SETTINGS_BLOCKS,
  ...DEFAULT_CABINET_PROFILE_BLOCKS,
  ...DEFAULT_SUBSCRIPTION_BLOCKS,
  ...DEFAULT_PARTNER_BLOCKS,
  ...DEFAULT_BONUS_BLOCKS,
  ...DEFAULT_SUPPORT_BLOCKS,
  ...DEFAULT_CHROME_BLOCKS,
  ...DEFAULT_LANDING_BLOCKS,
].map((def) => {
  const surface = def.surface ?? DEFAULT_PACK_SURFACE;
  return TARIFF_PREFETCH_TYPES.includes(def.type)
    ? { ...def, surface: { ...surface, prefetch: tariffPrefetch } }
    : { ...def, surface };
});
