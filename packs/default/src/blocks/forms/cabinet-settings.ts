import type { PackForm } from "@/components/constructor/packForms";

/** Формы блоков «Стандарта»: настройки кабинета. */
export const DEFAULT_CABINET_SETTINGS_FORMS: Record<string, PackForm> = {
  defaultHapticSettings: [
    {
      title: { ru: "Тексты", en: "Texts" },
      description: { ru: "Заголовок и подписи вариантов силы вибрации. Клиент сам выбирает силу — выбор хранится у него.", en: "Header and labels for vibration strength options. The client picks the strength — saved on their device." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "// Вибрация" },
        { kind: "text", key: "hint", label: { ru: "Hint", en: "Hint" }, placeholder: "отклик" },
        { kind: "text", key: "offLabel", label: { ru: "Выкл", en: "Off" }, placeholder: "ВЫКЛ" },
        { kind: "text", key: "lightLabel", label: { ru: "Слабый", en: "Light" }, placeholder: "СЛАБЫЙ" },
        { kind: "text", key: "mediumLabel", label: { ru: "Средний", en: "Medium" }, placeholder: "СРЕДНИЙ" },
        { kind: "text", key: "heavyLabel", label: { ru: "Сильный", en: "Heavy" }, placeholder: "СИЛЬНЫЙ" },
        { kind: "text", key: "unsupportedLabel", label: { ru: "Нет поддержки вибрации", en: "No vibration support" }, placeholder: "Устройство не поддерживает вибрацию…", multiline: true },
      ],
    },
  ],
  defaultSectionTabs: [
    {
      title: { ru: "Вид", en: "Appearance" },
      fields: [
        { kind: "select", key: "displayMode", label: { ru: "Как показывать", en: "Display mode" }, options: [
          { value: "tabs", label: { ru: "Вкладками", en: "Tabs" } },
          { value: "list", label: { ru: "Списком", en: "List" } },
        ] },
        { kind: "select", key: "orientation", label: { ru: "Направление", en: "Orientation" }, options: [
          { value: "horizontal", label: { ru: "По горизонтали", en: "Horizontal" } },
          { value: "vertical", label: { ru: "По вертикали", en: "Vertical" } },
        ] },
        { kind: "text", key: "defaultScreenId", label: { ru: "Экран по умолчанию", en: "Default screen" }, placeholder: "main" },
      ],
    },
    {
      title: { ru: "Контент", en: "Content" },
      description: { ru: "Каждый таб переключает активный экран группы: блоки с совпадающим screenId показываются, остальные скрываются.", en: "Each tab switches the active screen of a group: blocks with matching screenId are shown, others are hidden." },
      tab: "content",
      fields: [
        { kind: "widget", widget: "sectionTabsManager" },
      ],
    },
    {
      title: { ru: "Стиль", en: "Style" },
      description: { ru: "Цвета активного и неактивного таба и отступы. Размер иконки и шрифт — во вкладке «Размер».", en: "Active and inactive tab colors and spacing. Icon size and font — in the «Size» tab." },
      tab: "style",
      fields: [
        { kind: "color", key: "activeBackgroundColor", label: { ru: "Фон активного", en: "Active background" } },
        { kind: "color", key: "activeTextColor", label: { ru: "Текст активного", en: "Active text" } },
        { kind: "color", key: "inactiveBackgroundColor", label: { ru: "Фон неактивного", en: "Inactive background" } },
        { kind: "color", key: "inactiveTextColor", label: { ru: "Текст неактивного", en: "Inactive text" } },
        { kind: "number", key: "gap", label: { ru: "Промежуток (px)", en: "Gap (px)" }, min: 0, max: 32 },
      ],
    },
  ],
};
