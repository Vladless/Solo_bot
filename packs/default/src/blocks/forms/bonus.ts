import type { PackForm } from "@/components/constructor/packForms";

/** Формы блоков «Стандарта»: ежедневный бонус. */
export const DEFAULT_BONUS_FORMS: Record<string, PackForm> = {
  defaultGiftCardsGrid: [
    {
      title: { ru: "Источник карточек", en: "Cards source" },
      tab: "data",
      description: { ru: "Статичные — admin задаёт каждую карточку вручную. Тарифы из API — карточка на каждый tariff из /api/tariffs/public.", en: "Static — admin sets each card manually. Tariffs from API — one card per tariff." },
      fields: [
        { kind: "select", key: "source", label: { ru: "Режим", en: "Mode" }, default: "static", options: [{ value: "static", label: { ru: "Статичные карточки (admin вводит)", en: "Static cards (admin input)" } }, { value: "tariffs", label: { ru: "Тарифы из API (auto)", en: "Tariffs from API (auto)" } }] },
      ],
    },
    {
      title: { ru: "Параметры режима «Тарифы из API»", en: "«Tariffs from API» params" },
      tab: "data",
      description: { ru: "Фильтр по группе и куда вести при клике на карточку.", en: "Group filter and target page on click." },
      fields: [
        { kind: "select", key: "tariffCheckoutSlug", label: { ru: "Slug страницы checkout", en: "Checkout slug" }, source: "pages", allowEmpty: true, emptyLabel: { ru: "Страница по умолчанию", en: "Default page" }, allowCustom: true, customPlaceholder: { ru: "checkout", en: "checkout" } },
        { kind: "text", key: "tariffCtaLabel", label: { ru: "Текст кнопки", en: "CTA label" }, placeholder: "Подарить" },
        { kind: "text", key: "tariffTagLabel", label: { ru: "Tag в шапке карточки", en: "Card tag" }, placeholder: "ТАРИФ" },
        { kind: "text", key: "tariffPeriodFmt", label: { ru: "Период (с {days})", en: "Period ({days})" }, placeholder: "{days} дн" },
        { kind: "text", key: "tariffDevicesFmt", label: { ru: "Устройства ({n})", en: "Devices ({n})" }, placeholder: "{n} устр." },
        { kind: "text", key: "tariffTrafficFmt", label: { ru: "Трафик ({n})", en: "Traffic ({n})" }, placeholder: "{n} ГБ" },
        { kind: "text", key: "tariffUnlimitedText", label: { ru: "Текст безлимита", en: "Unlimited text" }, placeholder: "∞" },
        { kind: "text", key: "tariffLoadingText", label: { ru: "Текст загрузки", en: "Loading text" }, placeholder: "Загрузка тарифов..." },
        { kind: "text", key: "tariffEmptyText", label: { ru: "Текст «нет тарифов»", en: "Empty text" }, placeholder: "Нет доступных тарифов" },
        { kind: "select", key: "tariffsGroupCode", label: { ru: "Группа тарифов (фильтр)", en: "Tariff group (filter)" }, source: "tariffGroups", allowEmpty: true, allowCustom: true, emptyLabel: { ru: "Все группы", en: "All groups" } },
      ],
    },
    {
      title: { ru: "Карточки (статичные)", en: "Static cards" },
      description: { ru: "Каждая карточка — это промо/подарок. highlight = красная рамка. Если есть код — будет кнопка «Активировать» (POST /coupons/apply).", en: "Each card is a promo/gift. highlight = red border. With code — «Activate» button." },
      fields: [
        { kind: "list", key: "cards", label: { ru: "Карточки", en: "Cards" }, addLabel: { ru: "Карточка", en: "Card" }, newItem: { tag: "ACTIVE", title: "", description: "", expiresLabel: "", ctaLabel: "Активировать" }, item: [{ kind: "text", key: "tag", label: { ru: "Тег", en: "Tag" }, placeholder: "NEW / ACTIVE" }, { kind: "text", key: "expiresLabel", label: { ru: "Срок", en: "Expires label" }, placeholder: "до 06 МАЯ" }, { kind: "text", key: "title", label: { ru: "Заголовок", en: "Title" } }, { kind: "text", key: "description", label: { ru: "Описание", en: "Description" }, multiline: true }, { kind: "text", key: "ctaLabel", label: { ru: "Кнопка", en: "CTA label" } }, { kind: "text", key: "code", label: { ru: "Промо-код (опц.)", en: "Promo code (opt)" }, placeholder: "SOLO50" }, { kind: "boolean", key: "highlight", label: { ru: "Выделить карточку", en: "Highlight card" } }] },
      ],
    },
    {
      title: "Активация подарка",
      tab: "data",
      fields: [
        { kind: "text", key: "applyingLabel", label: { ru: "В процессе", en: "Applying" }, placeholder: "Активируем..." },
        { kind: "text", key: "successLabel", label: { ru: "Успех", en: "Success" }, placeholder: "✓ Применён" },
        { kind: "text", key: "errorLabel", label: { ru: "Ошибка", en: "Error" }, placeholder: "✕ Не применён" },
        { kind: "text", key: "ctaHrefFallback", label: { ru: "Ссылка кнопки по умолчанию", en: "Fallback CTA href" }, placeholder: "/dashboard/gifts" },
      ],
    },
  ],
  defaultGiftCta: [
    {
      title: { ru: "Призыв (главный экран)", en: "CTA (main screen)" },
      fields: [
        { kind: "text", key: "panelHint", label: { ru: "Hint сверху", en: "Top hint" }, placeholder: "// подари подписку" },
        { kind: "text", key: "title", label: { ru: "Главный заголовок", en: "Main title" }, placeholder: "ПОДАРИТЬ ПОДПИСКУ" },
        { kind: "text", key: "titleAccent", label: { ru: "Акцентный хвостик", en: "Accent tail" }, placeholder: "DROP" },
        { kind: "text", key: "description", label: { ru: "Описание", en: "Description" }, placeholder: "Выберите тариф...", multiline: true },
      ],
    },
    {
      title: { ru: "Список тарифов (внутренний экран)", en: "Tariff list (inner screen)" },
      fields: [
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" }, placeholder: "Загрузка тарифов..." },
        { kind: "text", key: "errorText", label: { ru: "Ошибка", en: "Error" }, placeholder: "Не удалось загрузить тарифы" },
        { kind: "text", key: "emptyText", label: { ru: "Нет тарифов", en: "Empty" }, placeholder: "Нет доступных тарифов" },
        { kind: "text", key: "perDayLabel", label: { ru: "Подпись «в день»", en: "Per-day label" }, placeholder: "в день" },
        { kind: "text", key: "unlimitedText", label: { ru: "Безлимит", en: "Unlimited" }, placeholder: "∞" },
        { kind: "text", key: "pickLabel", label: { ru: "Кнопка «Подарить» в строке", en: "«Pick» in row" }, placeholder: "Подарить →" },
        { kind: "text", key: "pageLabel", label: { ru: "Подпись страниц (для доступности)", en: "Page label (a11y)" }, placeholder: "Страница" },
        { kind: "text", key: "backLabel", label: { ru: "Кнопка «Назад»", en: "Back button" }, placeholder: "← Назад" },
      ],
    },
    {
      title: { ru: "Раскладка", en: "Layout" },
      tab: "style",
      fields: [
        { kind: "number", key: "compactListFrom", label: { ru: "Список вместо карточек от N тарифов", en: "Switch to list from N tariffs" }, min: 1, max: 50, default: 2 },
      ],
    },
    {
      title: "Параметры подарка",
      fields: [
        { kind: "text", key: "devicesLabel", label: { ru: "Подпись «устройства»", en: "Devices label" }, placeholder: "Устройств" },
        { kind: "text", key: "trafficLabel", label: { ru: "Подпись «трафик»", en: "Traffic label" }, placeholder: "Трафик, ГБ" },
        { kind: "text", key: "fromPriceLabel", label: { ru: "Префикс цены", en: "Price prefix" }, placeholder: "от" },
        { kind: "text", key: "priceComputingText", label: { ru: "Цена считается", en: "Price computing" }, placeholder: "..." },
      ],
    },
    {
      title: { ru: "Кнопка «Выбрать подарок»", en: "«Pick gift» button" },
      description: { ru: "Главная CTA — открывает экран со списком тарифов внутри блока.", en: "Main CTA — opens tariff list inside the block." },
      fields: [
        { kind: "text", key: "ctaLabel", label: { ru: "Текст кнопки", en: "Label" }, placeholder: "Выбрать подарок" },
      ],
    },
    {
      title: { ru: "Источник тарифов", en: "Tariff source" },
      tab: "data",
      description: { ru: "Рекомендуем завести в админке бота отдельную группу (например «gifts») и выбрать её здесь — иначе как подарки будут показаны все публичные тарифы.", en: "Create a dedicated group (e.g. «gifts») in bot admin and select it here — otherwise all public tariffs are shown as gifts." },
      fields: [
        { kind: "select", key: "checkoutSlug", label: { ru: "Slug страницы checkout", en: "Checkout slug" }, source: "pages", allowEmpty: true, emptyLabel: { ru: "Страница по умолчанию", en: "Default page" }, allowCustom: true, customPlaceholder: { ru: "checkout", en: "checkout" } },
        { kind: "select", key: "tariffsGroupCode", label: { ru: "Группа тарифов (фильтр)", en: "Tariff group (filter)" }, source: "tariffGroups", allowEmpty: true, allowCustom: true, emptyLabel: { ru: "Все группы", en: "All groups" } },
      ],
    },
  ],
  defaultGiftHistory: [
    {
      title: { ru: "Тексты", en: "Texts" },
      description: { ru: "Тянет данные из /api/gifts/my.", en: "Reads from /api/gifts/my." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "// История подарков" },
        { kind: "text", key: "panelHeaderHintFormat", label: { ru: "Хинт-формат ({count})", en: "Hint format" }, placeholder: "{count} активаций" },
        { kind: "text", key: "unusedLabel", label: { ru: "Pill: ожидает", en: "Pill: unused" }, placeholder: "ОЖИДАЕТ" },
        { kind: "text", key: "usedLabel", label: { ru: "Pill: использован", en: "Pill: used" }, placeholder: "ИСПОЛЬЗОВАН" },
        { kind: "text", key: "emptyText", label: { ru: "Пусто", en: "Empty" } },
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" } },
        { kind: "text", key: "unauthenticatedText", label: { ru: "Не авторизован", en: "Not authed" } },
        { kind: "number", key: "maxItems", label: { ru: "Максимум", en: "Max items" }, min: 1, max: 200, default: 20 },
      ],
    },
    {
      title: "Копирование кода",
      fields: [
        { kind: "text", key: "copyLabel", label: { ru: "Кнопка «Копировать»", en: "Copy button" }, placeholder: "Копировать" },
        { kind: "text", key: "copiedLabel", label: { ru: "После копирования", en: "After copy" }, placeholder: "Скопировано" },
      ],
    },
    {
      title: "QR, отправка и пагинация",
      fields: [
        { kind: "text", key: "qrTitle", label: { ru: "Заголовок QR", en: "QR title" }, placeholder: "QR подарка" },
        { kind: "text", key: "qrHint", label: { ru: "Подпись под QR", en: "QR caption" }, placeholder: "Покажите QR другу", multiline: true },
        { kind: "text", key: "qrCloseLabel", label: { ru: "Закрыть QR", en: "Close QR" }, placeholder: "Закрыть" },
        { kind: "text", key: "qrErrorText", label: { ru: "Ошибка QR", en: "QR error" }, placeholder: "Не удалось загрузить QR" },
        { kind: "text", key: "shareLabel", label: { ru: "Кнопка «Отправить»", en: "Share button" }, placeholder: "Отправить" },
        { kind: "text", key: "shareTitle", label: { ru: "Заголовок при отправке", en: "Share title" }, placeholder: "Подарок" },
        { kind: "number", key: "pageSize", label: { ru: "Подарков на странице", en: "Gifts per page" }, min: 1, max: 50, default: 4 },
      ],
    },
  ],
  defaultGiftRedeem: [
    {
      title: { ru: "Тексты", en: "Texts" },
      description: { ru: "POST /api/gifts/redeem — активация подарочного кода (от другого юзера). Не путать с промокодом (тот через /api/coupons/apply).", en: "POST /api/gifts/redeem — redeem a gift code from another user. Not the same as promo code." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "// Активировать подарок" },
        { kind: "text", key: "panelHeaderHint", label: { ru: "Хинт", en: "Hint" }, placeholder: "введи код" },
        { kind: "text", key: "description", label: { ru: "Описание (над полем ввода)", en: "Description (above input)" }, placeholder: "Введите код подарка...", multiline: true },
        { kind: "text", key: "placeholder", label: { ru: "Подсказка input", en: "Input placeholder" }, placeholder: "GIFT-XXXXXX" },
        { kind: "text", key: "submitLabel", label: { ru: "Кнопка", en: "Submit label" }, placeholder: "Активировать" },
        { kind: "text", key: "submittingLabel", label: { ru: "Кнопка во время отправки", en: "Submitting label" }, placeholder: "Активируем..." },
        { kind: "text", key: "successText", label: { ru: "Сообщение успеха", en: "Success text" }, placeholder: "✓ Подарок активирован" },
        { kind: "text", key: "notFoundText", label: { ru: "Код не найден", en: "Not found text" }, placeholder: "✕ Код не найден или уже использован" },
        { kind: "text", key: "errorText", label: { ru: "Общая ошибка", en: "Error text" }, placeholder: "✕ Не удалось активировать" },
        { kind: "text", key: "invalidFormatText", label: { ru: "Неверный формат", en: "Invalid format text" }, placeholder: "✕ Только латинские буквы, цифры, дефис" },
      ],
    },
    {
      title: "Проверка кода",
      fields: [
        { kind: "number", key: "minLength", label: { ru: "Минимальная длина кода", en: "Min code length" }, min: 1, max: 64, default: 3 },
      ],
    },
  ],
  defaultPromoActivate: [
    {
      title: { ru: "Тексты", en: "Texts" },
      description: { ru: "POST /api/coupons/redeem.", en: "POST /api/coupons/redeem." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "// Промо-код" },
        { kind: "text", key: "panelHeaderHint", label: { ru: "Хинт", en: "Hint" }, placeholder: "введи и активируй" },
        { kind: "text", key: "placeholder", label: { ru: "Подсказка input", en: "Input placeholder" }, placeholder: "PROMO-XXXX-XXXX" },
        { kind: "text", key: "submitLabel", label: { ru: "Кнопка", en: "Submit label" }, placeholder: "Активировать" },
        { kind: "text", key: "submittingLabel", label: { ru: "Кнопка во время отправки", en: "Submitting label" }, placeholder: "Активируем..." },
        { kind: "text", key: "successText", label: { ru: "Сообщение успеха", en: "Success text" }, placeholder: "✓ Промокод применён" },
        { kind: "text", key: "errorText", label: { ru: "Сообщение ошибки", en: "Error text" }, placeholder: "✕ Неверный или истёкший код" },
      ],
    },
    {
      title: "Проверка промокода",
      fields: [
        { kind: "text", key: "invalidFormatText", label: { ru: "Неверный формат", en: "Invalid format" }, placeholder: "✕ Только латиница и цифры", multiline: true },
        { kind: "number", key: "minLength", label: { ru: "Минимальная длина", en: "Min length" }, min: 1, max: 64, default: 3 },
      ],
    },
  ],
};
