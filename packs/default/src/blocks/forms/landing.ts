import type { PackForm } from "@/components/constructor/packForms";

/** Формы блоков «Стандарта»: лендинг. */
export const DEFAULT_LANDING_FORMS: Record<string, PackForm> = {
  defaultFeaturesGrid: [
    {
      title: { ru: "Карточки", en: "Cards" },
      fields: [
        { kind: "list", key: "items", label: { ru: "Карточки", en: "Cards" }, addLabel: { ru: "Карточка", en: "Card" }, newItem: { code: "", title: "", desc: "" }, item: [
          { kind: "text", key: "code", label: { ru: "Код", en: "Code" }, placeholder: "01" },
          { kind: "text", key: "title", label: { ru: "Заголовок", en: "Title" } },
          { kind: "text", key: "desc", label: { ru: "Описание", en: "Description" }, multiline: true },
          { kind: "text", key: "metricValue", label: { ru: "Значение метрики", en: "Metric value" } },
          { kind: "text", key: "metricLabel", label: { ru: "Подпись метрики", en: "Metric label" } },
          { kind: "select", key: "iconKey", source: "featureIcons", allowEmpty: true, label: { ru: "Иконка", en: "Icon" } },
        ] },
      ],
    },
    {
      title: { ru: "Геометрия", en: "Geometry" },
      tab: "style",
      fields: [
        { kind: "number", key: "padding", label: { ru: "Внутренний отступ карточки (px)", en: "Card padding (px)" }, min: 0, max: 80, default: 32 },
        { kind: "number", key: "iconSize", label: { ru: "Размер иконки (px)", en: "Icon size (px)" }, min: 40, max: 96, default: 64 },
        { kind: "number", key: "iconStroke", label: { ru: "Толщина линий иконки", en: "Icon stroke width" }, min: 0.5, max: 4, step: 0.1, default: 1.4 },
      ],
    },
    {
      title: { ru: "Hover-эффект", en: "Hover effect" },
      tab: "interaction",
      description: { ru: "Подсветка карточки при наведении: иконка и её рамка красятся в акцент.", en: "Card highlight on hover: icon and its border get accent color." },
      fields: [
        { kind: "boolean", key: "hoverEnabled", label: { ru: "Включить hover", en: "Enable hover" }, default: true },
        { kind: "number", key: "hoverTransitionMs", label: { ru: "Длительность перехода (мс)", en: "Transition (ms)" }, min: 0, max: 2000, step: 10, default: 200 },
      ],
    },
    {
      title: { ru: "Шрифты", en: "Fonts" },
      fields: [
        { kind: "select", key: "monoFontFamily", label: { ru: "Шрифт код/мета (пусто = из темы)", en: "Code/meta font (empty = from theme)" }, source: "fonts", allowEmpty: true, emptyLabel: { ru: "Из темы", en: "From theme" }, allowCustom: true, customPlaceholder: { ru: "из темы / JetBrains Mono", en: "из темы / JetBrains Mono" } },
      ],
    },
  ],
  defaultTariffsGrid: [
    {
      title: { ru: "Тарифы вручную", en: "Manual tariffs" },
      description: { ru: "Пусто — карточки берутся с сервера по группе тарифов.", en: "Empty — cards come from the server by tariff group." },
      fields: [
        { kind: "select", key: "ctaFlowId", source: "flows", allowEmpty: true, allowCustom: true, label: { ru: "Сценарий кнопки", en: "Button flow" }, emptyLabel: { ru: "Без сценария", en: "No flow" } },
        { kind: "list", key: "tariffBadges", label: { ru: "Бейджи тарифов", en: "Tariff badges" }, addLabel: { ru: "Бейдж", en: "Badge" }, newItem: { tariff: "", badge: "" }, item: [{ kind: "text", key: "tariff", label: { ru: "Номер тарифа", en: "Tariff id" }, placeholder: "12" }, { kind: "text", key: "badge", label: { ru: "Надпись", en: "Badge" }, placeholder: "ХИТ" }, { kind: "boolean", key: "highlighted", label: { ru: "Выделять карточку", en: "Highlight card" }, default: false }] },
        { kind: "list", key: "items", label: { ru: "Карточки тарифов", en: "Tariff cards" }, addLabel: { ru: "Тариф", en: "Tariff" }, newItem: { name: "", price: "", period: "" }, item: [
          { kind: "text", key: "name", label: { ru: "Название", en: "Name" } },
          { kind: "text", key: "desc", label: { ru: "Описание", en: "Description" }, multiline: true },
          { kind: "text", key: "badge", label: { ru: "Бейдж", en: "Badge" } },
          { kind: "boolean", key: "highlighted", label: { ru: "Выделить", en: "Highlight" }, default: false },
          { kind: "text", key: "price", label: { ru: "Цена", en: "Price" } },
          { kind: "text", key: "currency", label: { ru: "Валюта", en: "Currency" }, placeholder: "₽" },
          { kind: "text", key: "period", label: { ru: "Период", en: "Period" }, placeholder: "/ мес" },
          { kind: "text", key: "ctaLabel", label: { ru: "Кнопка", en: "Button" } },
          { kind: "text", key: "ctaHref", label: { ru: "Ссылка кнопки", en: "Button link" } },
        ] },
      ],
    },
    {
      title: { ru: "Источник тарифов", en: "Tariff source" },
      tab: "data",
      description: {
        ru: "Группа тарифов из бота и то, как показывать подгруппы внутри неё.",
        en: "The tariff group from the bot and how its subgroups are shown.",
      },
      fields: [
        { kind: "select", key: "groupCode", label: { ru: "Группа тарифов", en: "Tariff group" }, source: "tariffGroups", allowEmpty: true, allowCustom: true, emptyLabel: { ru: "Все группы", en: "All groups" }, customPlaceholder: { ru: "Код группы или пусто = все", en: "Group code or empty = all" } },
        {
          kind: "select",
          key: "subgroupMode",
          label: { ru: "Подгруппы тарифов", en: "Tariff subgroups" },
          options: [
            { value: "all", label: { ru: "Показывать все подряд", en: "Show all at once" } },
            { value: "tabs", label: { ru: "Переключателем подгрупп", en: "With a subgroup switcher" } },
            { value: "one", label: { ru: "Только одну подгруппу", en: "A single subgroup only" } },
          ],
          default: "all",
        },
        {
          kind: "select",
          key: "subgroup",
          label: { ru: "Какая подгруппа", en: "Which subgroup" },
          source: "tariffSubgroups",
          allowEmpty: true,
          emptyLabel: { ru: "— выберите подгруппу —", en: "— pick a subgroup —" },
          when: { key: "subgroupMode", equals: "one" },
        },
        {
          kind: "text",
          key: "subgroupOtherLabel",
          label: { ru: "Подпись вкладки «остальные»", en: "The “other” tab label" },
          placeholder: "Остальные",
          when: { key: "subgroupMode", equals: "tabs" },
        },
      ],
    },
    {
      title: { ru: "Опции конфигурируемых тарифов", en: "Configurable tariff options" },
      description: { ru: "Если карточка привязана к конфигурируемому тарифу — внутри карточки появятся чипы выбора устройств/трафика, цена пересчитывается через /api/tariffs/config-price, а CTA откроет /checkout с выбранными опциями.", en: "If a card is bound to a configurable tariff — chips for devices/traffic appear inside, price is recalculated via /api/tariffs/config-price, and CTA opens /checkout with options." },
      fields: [
        { kind: "text", key: "devicesLabel", label: { ru: "Подпись группы устройств", en: "Devices label" }, placeholder: "Устройств" },
        { kind: "text", key: "trafficLabel", label: { ru: "Подпись группы трафика", en: "Traffic label" }, placeholder: "Трафик, ГБ" },
        { kind: "text", key: "devicesUnit", label: { ru: "Единица устройств", en: "Devices unit" }, placeholder: "" },
        { kind: "text", key: "trafficUnit", label: { ru: "Единица трафика", en: "Traffic unit" }, placeholder: "ГБ" },
        { kind: "text", key: "unlimitedTrafficLabel", label: { ru: "Безлимит трафика", en: "Unlimited traffic" }, placeholder: "Безлимит" },
        { kind: "text", key: "priceComputingText", label: { ru: "Пока считаем цену", en: "Price computing" }, placeholder: "..." },
        { kind: "select", key: "checkoutSlug", label: { ru: "Slug страницы оформления", en: "Checkout page slug" }, source: "pages", allowEmpty: true, emptyLabel: { ru: "Страница по умолчанию", en: "Default page" }, allowCustom: true, customPlaceholder: { ru: "checkout", en: "checkout" } },
        { kind: "boolean", key: "showConfigOptions", label: { ru: "Показывать выбор опций для конфиг. тарифов", en: "Show option pickers for configurable tariffs" }, default: true },
      ],
    },
    {
      title: "Настройка тарифа клиентом",
      fields: [
        { kind: "text", key: "configDevicesTitle", label: { ru: "Заголовок «устройства»", en: "Devices title" }, placeholder: "Устройства" },
        { kind: "text", key: "configTrafficTitle", label: { ru: "Заголовок «трафик»", en: "Traffic title" }, placeholder: "Трафик" },
        { kind: "text", key: "configChooseLabel", label: { ru: "Кнопка выбора", en: "Choose button" }, placeholder: "Выбрать" },
        { kind: "text", key: "configDoneLabel", label: { ru: "Кнопка «готово»", en: "Done button" }, placeholder: "Готово" },
      ],
    },
    {
      title: "Склонения периода",
      description: "Три формы через слэш: 1 год / 2 года / 5 лет.",
      fields: [
        { kind: "text", key: "periodYearForms", label: { ru: "Годы", en: "Years" }, placeholder: "год/года/лет" },
        { kind: "text", key: "periodMonthForms", label: { ru: "Месяцы", en: "Months" }, placeholder: "месяц/месяца/месяцев" },
        { kind: "text", key: "periodDayForms", label: { ru: "Дни", en: "Days" }, placeholder: "день/дня/дней" },
      ],
    },
    {
      title: "Кнопка автоматических карточек",
      fields: [
        { kind: "text", key: "autoCtaLabel", label: { ru: "Кнопка на карточке", en: "Card button" }, placeholder: "Выбрать" },
      ],
    },
    {
      title: "Подпись срока",
      fields: [
        { kind: "text", key: "durationFeatureLabel", label: { ru: "Подпись «срок»", en: "Duration label" }, placeholder: "Срок" },
      ],
    },
    {
      title: { ru: "Геометрия", en: "Geometry" },
      tab: "style",
      fields: [
        { kind: "number", key: "padding", label: { ru: "Внутренний отступ карточки (px)", en: "Card padding (px)" }, min: 16, max: 80, default: 32 },
      ],
    },
    {
      title: { ru: "Hover-эффект", en: "Hover effect" },
      tab: "interaction",
      fields: [
        { kind: "boolean", key: "hoverEnabled", label: { ru: "Включить hover", en: "Enable hover" }, default: true },
        { kind: "number", key: "hoverTransitionMs", label: { ru: "Длительность перехода (мс)", en: "Transition (ms)" }, min: 0, max: 2000, step: 10, default: 200 },
      ],
    },
    {
      title: { ru: "Шрифты", en: "Fonts" },
      fields: [
        { kind: "select", key: "monoFontFamily", label: { ru: "Шрифт код/CTA (пусто = из темы)", en: "Code/CTA font (empty = from theme)" }, source: "fonts", allowEmpty: true, emptyLabel: { ru: "Из темы", en: "From theme" }, allowCustom: true, customPlaceholder: { ru: "из темы / JetBrains Mono", en: "из темы / JetBrains Mono" } },
      ],
    },
  ],
};
