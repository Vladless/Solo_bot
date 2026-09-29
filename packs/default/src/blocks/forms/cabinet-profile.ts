import type { PackForm } from "@/components/constructor/packForms";

/** Формы блоков «Стандарта»: профиль кабинета. */
export const DEFAULT_CABINET_PROFILE_FORMS: Record<string, PackForm> = {
  defaultLoginHistory: [
    {
      title: { ru: "Тексты", en: "Texts" },
      description: { ru: "Содержимое блока. Сейчас сессии — заглушка, ждём API.", en: "Block labels. Sessions are mocked, awaiting API." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "// История входов" },
        { kind: "text", key: "currentSuffix", label: { ru: "Подпись «текущая»", en: "Current suffix" }, placeholder: "сейчас" },
        { kind: "text", key: "revokeLabel", label: { ru: "Кнопка завершить", en: "Revoke label" }, placeholder: "Завершить" },
        { kind: "text", key: "emptyText", label: { ru: "Пусто", en: "Empty" }, placeholder: "История пуста" },
        { kind: "number", key: "maxItems", label: { ru: "Максимум сессий", en: "Max items" }, min: 1, max: 100, default: 10 },
      ],
    },
    {
      title: "Пагинация, ссылка и отзыв сессии",
      fields: [
        { kind: "text", key: "allLabel", label: { ru: "Подпись ссылки «все»", en: "«All» label" }, placeholder: "Все сессии" },
        { kind: "text", key: "allHref", label: { ru: "Куда ведёт «все»", en: "«All» href" }, placeholder: "/dashboard/profile" },
        { kind: "text", key: "prevLabel", label: { ru: "Пагинация: назад", en: "Pager: previous" }, placeholder: "Назад" },
        { kind: "text", key: "nextLabel", label: { ru: "Пагинация: вперёд", en: "Pager: next" }, placeholder: "Вперёд" },
        { kind: "text", key: "pageLabelFormat", label: { ru: "Пагинация: счётчик", en: "Pager: counter" }, placeholder: "{page} / {total}" },
        { kind: "text", key: "revokeBusyLabel", label: { ru: "Отзыв в процессе", en: "Revoking" }, placeholder: "..." },
        { kind: "text", key: "revokeErrorText", label: { ru: "Ошибка отзыва", en: "Revoke error" }, placeholder: "Не удалось" },
        { kind: "number", key: "pageSize", label: { ru: "Сессий на странице", en: "Sessions per page" }, min: 1, max: 50, default: 4 },
      ],
    },
  ],
  defaultProfileCard: [
    {
      title: { ru: "Шапка", en: "Header" },
      description: { ru: "Заголовок панели и подсказка.", en: "Panel title and hint." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "// Учётная запись" },
        { kind: "text", key: "joinedPrefix", label: { ru: "Префикс «с»", en: "Joined prefix" }, placeholder: "с" },
        { kind: "text", key: "daysSinceSuffix", label: { ru: "Суффикс «дн»", en: "Days suffix" }, placeholder: "дн" },
        { kind: "text", key: "verifiedBadgeText", label: { ru: "Бейдж VERIFIED", en: "Verified badge" }, placeholder: "VERIFIED" },
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" }, placeholder: "Загрузка..." },
        { kind: "text", key: "emptyAuthText", label: { ru: "Не авторизован", en: "Not authed" }, placeholder: "Войдите в аккаунт" },
        { kind: "boolean", key: "showAvatar", label: { ru: "Показать аватар", en: "Show avatar" }, default: true },
        { kind: "boolean", key: "showJoinedSince", label: { ru: "Дата регистрации", en: "Show joined since" }, default: true },
      ],
    },
    {
      title: { ru: "Поля", en: "Fields" },
      description: { ru: "Список полей профиля. Для email/telegram значения берутся из API.", en: "Profile field list. For email/telegram values come from API." },
      fields: [
        { kind: "list", key: "fields", label: { ru: "Поля профиля", en: "Profile fields" }, addLabel: { ru: "Поле", en: "Field" }, newItem: { kind: "static", label: "—" }, item: [{ kind: "select", key: "kind", label: { ru: "Тип", en: "Kind" }, default: "static", options: [{ value: "email", label: { ru: "E-mail", en: "Email" } }, { value: "telegram", label: { ru: "Telegram", en: "Telegram" } }, { value: "phone", label: { ru: "Телефон (статика)", en: "Phone (static)" } }, { value: "language", label: { ru: "Язык (статика)", en: "Language (static)" } }, { value: "identity", label: { ru: "ID (api)", en: "Identity (api)" } }, { value: "balance", label: { ru: "Баланс (api)", en: "Balance (api)" } }, { value: "static", label: { ru: "Своё значение", en: "Static" } }] }, { kind: "text", key: "label", label: { ru: "Подпись", en: "Label" } }, { kind: "text", key: "value", label: { ru: "Значение", en: "Value" }, when: { key: "kind", oneOf: ["static", "phone", "language"] } }, { kind: "boolean", key: "showVerifiedBadge", label: { ru: "Показывать бейдж VERIFIED", en: "Show VERIFIED badge" }, when: { key: "kind", oneOf: ["email", "telegram"] } }] },
      ],
    },
    {
      title: "Привязки и дата регистрации",
      fields: [
        { kind: "text", key: "bindLabel", label: { ru: "Кнопка «Привязать»", en: "«Bind» button" }, placeholder: "Привязать" },
        { kind: "text", key: "joinedSincePrefix", label: { ru: "Префикс даты регистрации", en: "Joined since prefix" }, placeholder: "С нами" },
      ],
    },
    {
      title: "Значения и сообщения привязки",
      fields: [
        { kind: "text", key: "notLinkedText", label: { ru: "Значение «не привязан»", en: "Not linked value" }, placeholder: "не привязан" },
        { kind: "select", key: "languageFallback", label: { ru: "Язык по умолчанию", en: "Default language" }, source: "locales", allowEmpty: true, emptyLabel: { ru: "Из настроек сайта", en: "From site settings" }, allowCustom: true, customPlaceholder: { ru: "Русский", en: "Русский" } },
        { kind: "text", key: "okBindTelegram", label: { ru: "Telegram привязан", en: "Telegram linked" }, placeholder: "Telegram успешно привязан" },
        { kind: "text", key: "okSendCode", label: { ru: "Код отправлен", en: "Code sent" }, placeholder: "Код подтверждения отправлен на почту" },
        { kind: "text", key: "errEmailMissing", label: { ru: "Ошибка: нет почты", en: "Email missing" }, placeholder: "Укажите email" },
        { kind: "text", key: "errCodeMissing", label: { ru: "Ошибка: нет кода", en: "Code missing" }, placeholder: "Укажите email и код" },
        { kind: "text", key: "errLinkTelegram", label: { ru: "Ошибка привязки Telegram", en: "Telegram link error" }, placeholder: "Не удалось привязать Telegram" },
      ],
    },
  ],
  defaultSecurityPanel: [
    {
      title: { ru: "Шапка", en: "Header" },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "// Безопасность" },
        { kind: "text", key: "panelHeaderHint", label: { ru: "Хинт", en: "Hint" }, placeholder: "защита" },
      ],
    },
    {
      title: { ru: "Пароль, привязки и сессии", en: "Password, bindings and sessions" },
      fields: [
        { kind: "text", key: "passwordLabel", label: { ru: "Строка «Пароль»", en: "Password row" }, placeholder: "Пароль" },
        { kind: "text", key: "passwordSetDesc", label: { ru: "Пароль установлен", en: "Password set" }, placeholder: "Установлен. Можно сменить в любой момент", multiline: true },
        { kind: "text", key: "passwordEmptyDesc", label: { ru: "Пароля нет", en: "No password" }, placeholder: "Не установлен", multiline: true },
        { kind: "text", key: "changePasswordLabel", label: { ru: "Кнопка «Сменить»", en: "Change button" }, placeholder: "Сменить" },
        { kind: "text", key: "setPasswordLabel", label: { ru: "Кнопка «Установить»", en: "Set button" }, placeholder: "Установить" },
        { kind: "text", key: "passwordHref", label: { ru: "Ссылка смены пароля", en: "Password link" }, placeholder: "/forgot-password" },
        { kind: "text", key: "bindingsLabel", label: { ru: "Строка «Привязки»", en: "Bindings row" }, placeholder: "Привязки" },
        { kind: "text", key: "bindingsDescFmt", label: { ru: "Описание привязок", en: "Bindings description" }, placeholder: "Email: {email} · Telegram: {tg}", multiline: true },
        { kind: "text", key: "bindingsManageLabel", label: { ru: "Кнопка «Управлять»", en: "Manage button" }, placeholder: "Управлять" },
        { kind: "text", key: "bindingsHref", label: { ru: "Ссылка управления", en: "Manage link" }, placeholder: "#bind-account" },
        { kind: "text", key: "presentLabel", label: { ru: "Значок «есть»", en: "Present mark" }, placeholder: "✓" },
        { kind: "text", key: "absentLabel", label: { ru: "Значок «нет»", en: "Absent mark" }, placeholder: "—" },
        { kind: "text", key: "sessionsLabel", label: { ru: "Строка «Сессии»", en: "Sessions row" }, placeholder: "Активных сессий" },
        { kind: "text", key: "sessionsDesc", label: { ru: "Описание сессий", en: "Sessions description" }, placeholder: "Сколько устройств входят в аккаунт", multiline: true },
        { kind: "text", key: "revokeOthersLabel", label: { ru: "Завершить другие", en: "Revoke others" }, placeholder: "Завершить другие" },
        { kind: "text", key: "revokeOnlyLabel", label: { ru: "Только эта сессия", en: "Only this session" }, placeholder: "Только эта" },
        { kind: "text", key: "revokeBusyLabel", label: { ru: "Завершение в процессе", en: "Revoking" }, placeholder: "Завершаем..." },
      ],
    },
    {
      title: { ru: "Результат завершения сессий", en: "Session revoke result" },
      fields: [
        { kind: "text", key: "revokeOkText", label: { ru: "Успех", en: "Success" }, placeholder: "Другие сессии завершены" },
        { kind: "text", key: "revokeErrorText", label: { ru: "Ошибка", en: "Error" }, placeholder: "Не удалось завершить сессии" },
      ],
    },
  ],
  defaultStatStrip: [
    {
      title: { ru: "Метрики", en: "Metrics" },
      description: { ru: "Каждая ячейка может тянуть данные из API или показывать своё значение.", en: "Each tile may bind to API or show static text." },
      fields: [
        { kind: "list", key: "tiles", label: { ru: "Плитки", en: "Tiles" }, addLabel: { ru: "Плитка", en: "Tile" }, newItem: { metric: "static", label: "—" }, item: [{ kind: "select", key: "metric", label: { ru: "Источник", en: "Source" }, default: "static", options: [{ value: "static", label: { ru: "Свободное значение", en: "Static" } }, { value: "balance", label: { ru: "Баланс", en: "Balance" } }, { value: "keysTotal", label: { ru: "Подписки", en: "Keys total" } }, { value: "trialStatus", label: { ru: "Пробник", en: "Trial status" } }, { value: "referralsTotal", label: { ru: "Рефералы", en: "Referrals" } }, { value: "referralsActive", label: { ru: "Активные рефералы", en: "Active referrals" } }, { value: "referralBonusTotal", label: { ru: "Бонус рефералы", en: "Ref bonus" } }, { value: "giftsSent", label: { ru: "Подарков отправлено", en: "Gifts sent" } }, { value: "giftsClaimed", label: { ru: "Подарков получено", en: "Gifts claimed" } }, { value: "couponsUsed", label: { ru: "Купонов", en: "Coupons used" } }, { value: "partnerBalance", label: { ru: "Партнёр баланс", en: "Partner balance" } }, { value: "partnerReferredTotal", label: { ru: "Партнёр приглашений", en: "Partner refs" } }, { value: "partnerPercent", label: { ru: "Партнёр %", en: "Partner %" } }, { value: "unreadNotifications", label: { ru: "Непрочитанных", en: "Unread" } }] }, { kind: "text", key: "label", label: { ru: "Подпись", en: "Label" } }, { kind: "text", key: "value", label: { ru: "Значение", en: "Value" }, when: { key: "metric", equals: "static" } }, { kind: "text", key: "unit", label: { ru: "Единицы", en: "Unit" }, placeholder: "₽" }, { kind: "text", key: "delta", label: { ru: "Дельта (необязательно)", en: "Delta (optional)" } }, { kind: "select", key: "deltaDirection", label: { ru: "Направление дельты", en: "Delta direction" }, default: "up", options: [{ value: "up", label: { ru: "Рост", en: "Up" } }, { value: "down", label: { ru: "Спад", en: "Down" } }] }] },
      ],
    },
  ],
};
