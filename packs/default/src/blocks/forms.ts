import type { PackForm } from "@/components/constructor/packForms";
import { DEFAULT_BONUS_FORMS } from "./forms/bonus";
import { DEFAULT_CORE_FORMS } from "./forms/fromCore";
import { DEFAULT_CABINET_PROFILE_FORMS } from "./forms/cabinet-profile";
import { DEFAULT_CABINET_SETTINGS_FORMS } from "./forms/cabinet-settings";
import { DEFAULT_CHROME_FORMS } from "./forms/chrome";
import { DEFAULT_LANDING_FORMS } from "./forms/landing";
import { DEFAULT_PARTNER_FORMS } from "./forms/partner";
import { DEFAULT_SUBSCRIPTION_FORMS } from "./forms/subscription";
import { DEFAULT_SUPPORT_FORMS } from "./forms/support";

/** Формы блоков «Стандарта»: секции и поля описаны данными, рисует их общая форма набора. */
export const DEFAULT_PACK_FORMS: Record<string, PackForm> = {
  ...DEFAULT_CORE_FORMS,
  ...DEFAULT_BONUS_FORMS,
  ...DEFAULT_CABINET_PROFILE_FORMS,
  ...DEFAULT_CABINET_SETTINGS_FORMS,
  ...DEFAULT_CHROME_FORMS,
  ...DEFAULT_LANDING_FORMS,
  ...DEFAULT_PARTNER_FORMS,
  ...DEFAULT_SUBSCRIPTION_FORMS,
  ...DEFAULT_SUPPORT_FORMS,
};
