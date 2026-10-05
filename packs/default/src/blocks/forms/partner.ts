import type { PackForm } from "@/components/constructor/packForms";

/** Формы блоков «Стандарта»: партнёрка. */
export const DEFAULT_PARTNER_FORMS: Record<string, PackForm> = {
  defaultFriendsList: [
    {
      title: { ru: "Источник", en: "Source" },
      fields: [
        { kind: "select", key: "mode", label: { ru: "Кого показывать", en: "Whose data" }, options: [
          { value: "referral", label: { ru: "Рефералы", en: "Referrals" } },
          { value: "partner", label: { ru: "Партнёрские клиенты", en: "Partner clients" } },
        ] },
      ],
    },
    {
      title: { ru: "Шапка", en: "Header" },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "// Приведённые друзья" },
        { kind: "text", key: "panelHeaderHintFormat", label: { ru: "Хинт-формат ({count})", en: "Hint format" }, placeholder: "{count} человек" },
        { kind: "text", key: "emptyText", label: { ru: "Пусто", en: "Empty" }, placeholder: "Пока нет рефералов" },
      ],
    },
    {
      title: { ru: "Список друзей", en: "Friends list" },
      description: { ru: "Пока ручной список — позже подключим к API партнёрки.", en: "Manual list for now — will wire to partner API later." },
      fields: [
        { kind: "list", key: "items", label: { ru: "Друзья", en: "Friends" }, addLabel: { ru: "Друг", en: "Friend" }, newItem: { name: "", plan: "", bonus: "", ts: "" }, item: [{ kind: "text", key: "name", label: { ru: "Имя", en: "Name" } }, { kind: "text", key: "plan", label: { ru: "Тариф", en: "Plan" }, placeholder: "PRIME · 12 мес" }, { kind: "text", key: "bonus", label: { ru: "Бонус", en: "Bonus" }, placeholder: "+30 дн" }, { kind: "text", key: "ts", label: { ru: "Когда", en: "When" }, placeholder: "2 дня назад" }] },
      ],
    },
    {
      title: "Статусы и список",
      fields: [
        { kind: "text", key: "activeBadge", label: { ru: "Метка «активен»", en: "Active badge" }, placeholder: "активен" },
        { kind: "text", key: "pendingBadge", label: { ru: "Метка «ждём»", en: "Pending badge" }, placeholder: "ждём" },
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" }, placeholder: "Загрузка..." },
        { kind: "text", key: "allLabelFormat", label: { ru: "Ссылка «все» ({count})", en: "«All» link" }, placeholder: "Все {count}" },
        { kind: "text", key: "allHref", label: { ru: "Куда ведёт «все»", en: "«All» link href" }, placeholder: "/dashboard/referrals" },
        { kind: "number", key: "pageSize", label: { ru: "Друзей на странице", en: "Friends per page" }, min: 1, max: 50, default: 4 },
      ],
    },
  ],
  defaultPartnerPayouts: [
    {
      title: { ru: "Тексты", en: "Texts" },
      description: "adm(\"POST/GET /api/partners/payouts/me — заявки на вывод партнёрского баланса. {balance",
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "// Заявки на вывод" },
        { kind: "text", key: "panelHeaderHintFmt", label: { ru: "Хинт ({balance})", en: "Hint ({balance})" }, placeholder: "баланс {balance} ₽" },
        { kind: "text", key: "amountLabel", label: { ru: "Подпись поля", en: "Amount label" }, placeholder: "Сумма к выводу" },
        { kind: "text", key: "amountPlaceholder", label: { ru: "Подсказка input", en: "Input placeholder" }, placeholder: "0" },
        { kind: "text", key: "submitLabel", label: { ru: "Кнопка", en: "Submit label" }, placeholder: "Запросить" },
        { kind: "text", key: "submittingLabel", label: { ru: "Кнопка во время отправки", en: "Submitting label" }, placeholder: "Отправляем..." },
        { kind: "text", key: "successText", label: { ru: "Сообщение успеха", en: "Success text" }, placeholder: "✓ Заявка создана" },
        { kind: "text", key: "errorText", label: { ru: "Общая ошибка", en: "Error text" }, placeholder: "✕ Не удалось создать заявку" },
        { kind: "text", key: "notEnoughBalanceText", label: { ru: "Недостаточно средств", en: "Not enough balance" }, placeholder: "На балансе недостаточно средств" },
        { kind: "text", key: "belowMinText", label: { ru: "Ниже минимума ({min})", en: "Below minimum ({min})" }, placeholder: "Минимальная сумма {min} ₽" },
        { kind: "text", key: "historyHeader", label: { ru: "Заголовок истории", en: "History header" }, placeholder: "// История" },
        { kind: "text", key: "emptyText", label: { ru: "Пустая история", en: "Empty history" }, placeholder: "Заявок пока нет" },
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" }, placeholder: "Загрузка..." },
      ],
    },
    {
      title: { ru: "Статусы", en: "Statuses" },
      fields: [
        { kind: "text", key: "statusPendingLabel", label: { ru: "В обработке", en: "Pending" }, placeholder: "В ОБРАБОТКЕ" },
        { kind: "text", key: "statusApprovedLabel", label: { ru: "Одобрено", en: "Approved" }, placeholder: "ОДОБРЕНО" },
        { kind: "text", key: "statusRejectedLabel", label: { ru: "Отклонено", en: "Rejected" }, placeholder: "ОТКЛОНЕНО" },
        { kind: "text", key: "statusPaidLabel", label: { ru: "Выплачено", en: "Paid" }, placeholder: "ВЫПЛАЧЕНО" },
      ],
    },
    {
      title: { ru: "Параметры", en: "Parameters" },
      fields: [
        { kind: "number", key: "minAmount", label: { ru: "Минимальная сумма ₽", en: "Minimum amount ₽" }, min: 1, step: 50, default: 100 },
        { kind: "number", key: "maxItems", label: { ru: "Сколько заявок показывать", en: "History items shown" }, min: 1, max: 200, default: 20 },
      ],
    },
    {
      title: "Способ вывода",
      fields: [
        { kind: "text", key: "methodSelectLabel", label: { ru: "Подпись выбора способа", en: "Method select label" }, placeholder: "Способ вывода" },
        { kind: "text", key: "requisitesLabel", label: { ru: "Подпись реквизитов", en: "Requisites label" }, placeholder: "Реквизиты" },
        { kind: "text", key: "saveMethodLabel", label: { ru: "Кнопка сохранения", en: "Save button" }, placeholder: "Сохранить" },
        { kind: "text", key: "savingMethodLabel", label: { ru: "Сохранение в процессе", en: "Saving" }, placeholder: "Сохраняем..." },
        { kind: "text", key: "changeMethodLabel", label: { ru: "Кнопка «изменить»", en: "Change button" }, placeholder: "изменить" },
        { kind: "text", key: "cancelLabel", label: { ru: "Кнопка «Отмена»", en: "Cancel button" }, placeholder: "Отмена" },
        { kind: "text", key: "methodSavedFmt", label: { ru: "Сохранённый способ", en: "Saved method" }, placeholder: "{method} · {masked}" },
        { kind: "text", key: "noMethodsText", label: { ru: "Способы не настроены", en: "No methods" }, placeholder: "Способы вывода не настроены администратором", multiline: true },
        { kind: "text", key: "setupCtaLabel", label: { ru: "Кнопка настройки", en: "Setup button" }, placeholder: "Установить способ вывода" },
        { kind: "text", key: "setupHint", label: { ru: "Подсказка про настройку", en: "Setup hint" }, placeholder: "Чтобы выводить средства, укажите реквизиты", multiline: true },
        { kind: "number", key: "pageSize", label: { ru: "Выплат на странице", en: "Payouts per page" }, min: 1, max: 50, default: 4 },
      ],
    },
  ],
  defaultReferralCard: [
    {
      title: { ru: "Источник", en: "Source" },
      fields: [
        { kind: "select", key: "mode", label: { ru: "Кого показывать", en: "Whose data" }, options: [
          { value: "referral", label: { ru: "Рефералы", en: "Referrals" } },
          { value: "partner", label: { ru: "Партнёрские клиенты", en: "Partner clients" } },
        ] },
      ],
    },
    {
      title: { ru: "Тексты", en: "Texts" },
      description: { ru: "Реферальный код берётся из /api/auth/summary.referral_code.", en: "Referral code comes from API." },
      fields: [
        { kind: "text", key: "panelHint", label: { ru: "Подзаголовок", en: "Hint" }, placeholder: "Ваша реферальная ссылка" },
        { kind: "text", key: "linkPrefix", label: { ru: "Префикс ссылки", en: "Link prefix" }, placeholder: "solonet.io/r/" },
        { kind: "text", key: "copyLabel", label: { ru: "Кнопка копирования", en: "Copy label" }, placeholder: "Копировать" },
        { kind: "text", key: "copiedLabel", label: { ru: "После копирования", en: "Copied label" }, placeholder: "Скопировано" },
        { kind: "text", key: "shareLabel", label: { ru: "Кнопка «Поделиться»", en: "Share label" }, placeholder: "Поделиться" },
        { kind: "text", key: "qrLabel", label: { ru: "Кнопка «QR»", en: "QR label" }, placeholder: "QR-код" },
        { kind: "boolean", key: "showShare", label: { ru: "Кнопка «Поделиться»", en: "Show share" }, default: true },
        { kind: "boolean", key: "showQr", label: { ru: "Кнопка «QR»", en: "Show QR" }, default: true },
      ],
    },
    {
      title: "Заголовок, бонусы и QR",
      fields: [
        { kind: "text", key: "headlineFormat", label: { ru: "Заголовок ({count})", en: "Headline" }, placeholder: "{count} друзей" },
        { kind: "text", key: "bonusText", label: { ru: "Текст про бонус", en: "Bonus text" }, placeholder: "", multiline: true },
        { kind: "text", key: "rewardText", label: { ru: "Текст про награду", en: "Reward text" }, placeholder: "", multiline: true },
        { kind: "text", key: "shareTitle", label: { ru: "Заголовок при отправке", en: "Share title" }, placeholder: "Приглашение" },
        { kind: "text", key: "qrShareHint", label: { ru: "Подпись под QR", en: "QR caption" }, placeholder: "", multiline: true },
        { kind: "text", key: "qrCloseLabel", label: { ru: "Закрыть QR", en: "Close QR" }, placeholder: "Закрыть" },
      ],
    },
  ],
  defaultReferralTier: [
    {
      title: { ru: "Источник", en: "Source" },
      fields: [
        { kind: "select", key: "mode", label: { ru: "Кого показывать", en: "Whose data" }, options: [
          { value: "referral", label: { ru: "Рефералы", en: "Referrals" } },
          { value: "partner", label: { ru: "Партнёрские клиенты", en: "Partner clients" } },
        ] },
      ],
    },
    {
      title: { ru: "Шапка", en: "Header" },
      description: { ru: "Хинт справа можно оставить пустым — обычно tier-системы у нас нет.", en: "Right hint can be empty — there is no tier system." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "// Условия программы" },
        { kind: "text", key: "panelHeaderHint", label: { ru: "Хинт (опц.)", en: "Hint (opt.)" }, placeholder: "" },
      ],
    },
    {
      title: { ru: "Подписи метрик из API", en: "API metric labels" },
      description: { ru: "Числа подтянутся из /api/auth/summary.", en: "Numbers come from /api/auth/summary." },
      fields: [
        { kind: "text", key: "referralsLabel", label: { ru: "Приглашено", en: "Referrals label" }, placeholder: "Приглашено" },
        { kind: "text", key: "activeReferralsLabel", label: { ru: "Активные/оплатили", en: "Active label" }, placeholder: "Активных" },
        { kind: "text", key: "bonusLabel", label: { ru: "Заработано/баланс", en: "Bonus label" }, placeholder: "Заработано" },
      ],
    },
    {
      title: { ru: "Условия программы", en: "Program conditions" },
      description: { ru: "Текст «summary» из /api/referrals/conditions или /api/partners/conditions.", en: "«summary» from /api/referrals/conditions or /api/partners/conditions." },
      fields: [
        { kind: "text", key: "conditionsLabel", label: { ru: "Подпись условий", en: "Conditions label" }, placeholder: "Условия" },
        { kind: "boolean", key: "showConditions", label: { ru: "Показывать строку условий из API", en: "Show conditions row from API" }, default: true },
      ],
    },
    {
      title: { ru: "Fallback строки (preview / API недоступен)", en: "Fallback rows (preview / API unavailable)" },
      fields: [
        { kind: "list", key: "rows", label: { ru: "Строки", en: "Rows" }, addLabel: { ru: "Строка", en: "Row" }, newItem: { label: "", value: "" }, item: [{ kind: "text", key: "label", label: { ru: "Название", en: "Label" } }, { kind: "text", key: "value", label: { ru: "Значение", en: "Value" } }] },
      ],
    },
    {
      title: "Уровни и прогресс",
      fields: [
        { kind: "text", key: "tierName", label: { ru: "Текущий уровень", en: "Current tier" }, placeholder: "Bronze" },
        { kind: "text", key: "nextTierName", label: { ru: "Следующий уровень", en: "Next tier" }, placeholder: "Silver" },
        { kind: "text", key: "progressHeaderFormat", label: { ru: "Заголовок прогресса", en: "Progress header" }, placeholder: "Прогресс до {next}" },
        { kind: "text", key: "progressLineFormat", label: { ru: "Строка прогресса", en: "Progress line" }, placeholder: "{current} / {target} рефералов" },
        { kind: "text", key: "toNextFormat", label: { ru: "Сколько осталось", en: "To next tier" }, placeholder: "Ещё {n} рефералов", multiline: true },
        { kind: "boolean", key: "showProgress", label: { ru: "Показывать прогресс", en: "Show progress" }, default: true },
        { kind: "boolean", key: "showSummary", label: { ru: "Показывать сводку", en: "Show summary" }, default: true },
        { kind: "number", key: "progressTarget", label: { ru: "Цель по рефералам", en: "Referral target" }, min: 1, max: 10000, default: 20 },
      ],
    },
  ],
};
