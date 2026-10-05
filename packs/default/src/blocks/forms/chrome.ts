import type { PackForm } from "@/components/constructor/packForms";

/** Формы блоков «Стандарта»: шапка, футер и навигация. */
export const DEFAULT_CHROME_FORMS: Record<string, PackForm> = {
  defaultBackdrop: [
    {
      title: { ru: "Статус-бар", en: "Status bar" },
      description: { ru: "Три зоны верхней строки. В центр опционально дописываются UTC-часы.", en: "Three zones of the top row. Center can append live UTC." },
      fields: [
        { kind: "text", key: "statusLeft", label: { ru: "Слева", en: "Left" }, placeholder: "● SYS // ONLINE" },
        { kind: "text", key: "statusCenter", label: { ru: "Центр (без часов)", en: "Center (no clock)" }, placeholder: "/ NODE_47 · AMS-03" },
        { kind: "text", key: "statusRight", label: { ru: "Справа", en: "Right" }, placeholder: "V 4.2.0 — STABLE" },
        { kind: "boolean", key: "showStatusBar", label: { ru: "Показывать статус-бар", en: "Show status bar" }, default: true },
        { kind: "boolean", key: "showLiveTime", label: { ru: "Добавлять UTC-часы в центр", en: "Append live UTC to center" }, default: true },
      ],
    },
    {
      title: { ru: "Анимация (canvas)", en: "Animation (canvas)" },
      tab: "effects",
      description: { ru: "Сетка 24×12 + узлы (cols×rows) с лёгким дрейфом, бегущая sine-волна и hub с лучами.", en: "24×12 grid + nodes (cols×rows) with subtle drift, running sine pulse and hub with rays." },
      fields: [
        { kind: "boolean", key: "showCanvas", label: { ru: "Показывать анимацию", en: "Show animation" }, default: true },
        { kind: "number", key: "cols", label: { ru: "Узлов по горизонтали", en: "Nodes cols" }, min: 8, max: 48, default: 18 },
        { kind: "number", key: "rows", label: { ru: "Узлов по вертикали", en: "Nodes rows" }, min: 4, max: 24, default: 9 },
        { kind: "number", key: "hotRatio", label: { ru: "Доля 'hot' узлов (0-0.5)", en: "Hot node ratio (0-0.5)" }, min: 0, max: 0.5, step: 0.01, default: 0.04 },
      ],
    },
    {
      title: { ru: "Шрифт", en: "Font" },
      fields: [
        { kind: "select", key: "monoFontFamily", label: { ru: "Шрифт (пусто = из темы)", en: "Font (empty = from theme)" }, source: "fonts", allowEmpty: true, emptyLabel: { ru: "Из темы", en: "From theme" }, allowCustom: true, customPlaceholder: { ru: "из темы / JetBrains Mono", en: "из темы / JetBrains Mono" } },
      ],
    },
  ],
  defaultCheckout: [
    {
      title: { ru: "Шапка", en: "Header" },
      fields: [
        { kind: "text", key: "headerLabel", label: { ru: "Шапка", en: "Header label" }, placeholder: "▸ К ОПЛАТЕ" },
        { kind: "text", key: "orderId", label: { ru: "Номер заказа", en: "Order ID" }, placeholder: "#SN-241029-7C4F" },
      ],
    },
    {
      title: { ru: "Купон", en: "Coupon" },
      fields: [
        { kind: "text", key: "couponPlaceholder", label: { ru: "Подсказка купона", en: "Coupon placeholder" }, placeholder: "ВВЕДИТЕ КУПОН" },
        { kind: "text", key: "couponApplyLabel", label: { ru: "Кнопка «Применить»", en: "Apply label" }, placeholder: "Применить" },
        { kind: "text", key: "couponLineLabel", label: { ru: "Строка «Купон»", en: "Coupon row label" }, placeholder: "Купон" },
        { kind: "text", key: "couponOkPrefix", label: { ru: "OK-префикс", en: "OK prefix" }, placeholder: "✓" },
        { kind: "text", key: "couponOkSuffix", label: { ru: "OK-суффикс", en: "OK suffix" }, placeholder: "применён" },
        { kind: "text", key: "couponErrorText", label: { ru: "Текст ошибки", en: "Error text" }, placeholder: "✕ код неверен" },
      ],
    },
    {
      title: { ru: "Итог", en: "Total" },
      fields: [
        { kind: "text", key: "totalLabel", label: { ru: "«Итого»", en: "Total label" }, placeholder: "Итого" },
        { kind: "text", key: "totalMeta", label: { ru: "Подпись валюты", en: "Currency meta" }, placeholder: "RUB · единоразово" },
        { kind: "text", key: "currency", label: { ru: "Символ валюты", en: "Currency" }, placeholder: "₽" },
        { kind: "number", key: "baseAmount", label: { ru: "Базовая сумма", en: "Base amount" }, min: 0 },
      ],
    },
    {
      title: { ru: "Кнопка оплаты", en: "Pay button" },
      tab: "data",
      fields: [
        { kind: "text", key: "payLabel", label: { ru: "Кнопка оплаты", en: "Pay label" }, placeholder: "Оплатить" },
        { kind: "text", key: "successRedirect", label: { ru: "Редирект после оплаты", en: "Success redirect" }, placeholder: "/dashboard" },
      ],
    },
    {
      title: { ru: "Footer-метки", en: "Footer marks" },
      fields: [
        { kind: "text", key: "secureLeft", label: { ru: "Footer слева", en: "Footer left" }, placeholder: "AES-256 · 3-D SECURE" },
        { kind: "text", key: "secureRight", label: { ru: "Footer справа", en: "Footer right" }, placeholder: "VISA · MC · МИР · USDT" },
        { kind: "text", key: "termsText", label: { ru: "Условия", en: "Terms" }, placeholder: "Нажимая «Оплатить»…", multiline: true },
      ],
    },
    {
      title: { ru: "Что оплачиваем", en: "Payment subject" },
      description: { ru: "Сценарий, тариф и опции карточка берёт из самой оплаты.", en: "Scenario, tariff and options come from the checkout itself." },
      fields: [
        { kind: "boolean", key: "showSubject", label: { ru: "Показывать предмет оплаты", en: "Show payment subject" }, default: true },
        { kind: "text", key: "subjectPeriodLabel", label: { ru: "Строка «Период»", en: "Period row label" }, placeholder: "Период" },
        { kind: "text", key: "subjectDevicesLabel", label: { ru: "Строка «Устройства»", en: "Devices row label" }, placeholder: "Устройства" },
        { kind: "text", key: "subjectTrafficLabel", label: { ru: "Строка «Трафик»", en: "Traffic row label" }, placeholder: "Трафик" },
        { kind: "select", key: "subjectPreviewMode", label: { ru: "Сценарий в превью", en: "Preview scenario" }, allowEmpty: true, emptyLabel: { ru: "Покупка", en: "Purchase" }, options: [{ value: "purchase", label: { ru: "Покупка", en: "Purchase" } }, { value: "renew", label: { ru: "Продление", en: "Renewal" } }, { value: "balance", label: { ru: "Пополнение баланса", en: "Balance top-up" } }, { value: "addons", label: { ru: "Докупка опций", en: "Add-ons" } }] },
        { kind: "text", key: "subjectPreviewTariff", label: { ru: "Тариф в превью", en: "Preview tariff" }, placeholder: "PRIME" },
      ],
    },
    {
      title: { ru: "Строка баланса", en: "Balance line" },
      fields: [
        { kind: "text", key: "balanceLineLabel", label: { ru: "Подпись строки баланса", en: "Balance line label" }, placeholder: "С баланса" },
      ],
    },
    {
      title: { ru: "Цены и купоны", en: "Prices and coupons" },
      tab: "data",
      description: { ru: "Строки прайс-листа и список рабочих купонов.", en: "Price rows and list of valid coupons." },
      fields: [
        { kind: "list", key: "priceRows", label: { ru: "Строки цен", en: "Price rows" }, addLabel: { ru: "Строка", en: "Row" }, newItem: { label: "", value: "", variant: "base" }, item: [{ kind: "text", key: "label", label: { ru: "Метка", en: "Label" } }, { kind: "text", key: "value", label: { ru: "Значение", en: "Value" } }, { kind: "select", key: "variant", label: { ru: "Вид строки", en: "Row kind" }, default: "base", options: [{ value: "base", label: { ru: "Обычная", en: "Base" } }, { value: "strike", label: { ru: "Зачёркнутая", en: "Strike" } }, { value: "discount", label: { ru: "Скидка", en: "Discount" } }] }] },
        { kind: "note", label: { ru: "Зачёркнутая = старая цена. Скидка = в цвете акцента.", en: "Strike = old price. Discount = accent color." } },
        { kind: "list", key: "coupons", label: { ru: "Рабочие купоны", en: "Valid coupons" }, addLabel: { ru: "Купон", en: "Coupon" }, newItem: { code: "", discountPercent: 10 }, item: [{ kind: "text", key: "code", label: { ru: "Код", en: "Code" } }, { kind: "number", key: "discountPercent", label: { ru: "Скидка %", en: "Discount %" }, min: 0, max: 100, default: 10 }] },
      ],
    },
  ],
  defaultCheckoutBanner: [
    {
      title: { ru: "Источник данных", en: "Data source" },
      tab: "data",
      description: { ru: "Подтягивать ли данные тарифа из checkout-flow.", en: "Whether to pull tariff data from checkout flow." },
      fields: [
        { kind: "boolean", key: "useFlowData", label: { ru: "Использовать данные оплаты", en: "Use checkout flow data" }, default: true },
      ],
    },
    {
      title: { ru: "Шапка", en: "Header" },
      description: { ru: "Breadcrumb-навигация над заголовком.", en: "Breadcrumb above the headline." },
      fields: [
        { kind: "strings", key: "breadcrumb", label: { ru: "Breadcrumb", en: "Breadcrumb" }, addLabel: { ru: "Шаг", en: "Step" } },
      ],
    },
    {
      title: { ru: "Заголовок и подпись", en: "Headline and subtitle" },
      fields: [
        { kind: "text", key: "headlineText", label: { ru: "Заголовок", en: "Headline" }, placeholder: "Оплата" },
        { kind: "text", key: "subtitle", label: { ru: "Подпись (если flow выключен)", en: "Subtitle (when flow is off)" }, placeholder: "Подписка SoloNet PRO · 12 мес. Активация…", multiline: true },
      ],
    },
    {
      title: { ru: "Стрип характеристик", en: "Plan strip" },
      description: { ru: "Ячейки под подписью. Тариф/Период/Устройства подтянутся из flow.", en: "Cells below the subtitle. Tariff/Period/Devices auto-fill from flow." },
      fields: [
        { kind: "list", key: "planCells", label: { ru: "Ячейки", en: "Cells" }, addLabel: { ru: "Ячейка", en: "Cell" }, newItem: { label: "", value: "" }, item: [{ kind: "text", key: "label", label: { ru: "Метка", en: "Label" } }, { kind: "text", key: "value", label: { ru: "Значение", en: "Value" } }, { kind: "boolean", key: "accent", label: { ru: "Акцент", en: "Accent" }, default: false }] },
        { kind: "note", label: { ru: "1-я ячейка → тариф, 2-я → период, 3-я → устройства, 4-я → трафик (из flow). 5-я и дальше — статика.", en: "Cell 1 → tariff, 2 → period, 3 → devices, 4 → traffic (from flow). 5+ are static." } },
      ],
    },
  ],
  defaultLoginForm: [
    {
      title: { ru: "Правовая строка и шрифт", en: "Legal line and font" },
      fields: [
        { kind: "text", key: "legalText", label: { ru: "Правовая строка", en: "Legal text" }, multiline: true, placeholder: "Нажимая «Войти», вы соглашаетесь с условиями" },
        { kind: "select", key: "sansFontFamily", source: "fonts", allowEmpty: true, allowCustom: true, label: { ru: "Шрифт подписей", en: "Label font" }, emptyLabel: { ru: "Из темы", en: "From theme" } },
      ],
    },
    {
      title: { ru: "Способы входа", en: "Login methods" },
      tab: "data",
      fields: [
        { kind: "boolean", key: "showPasswordLogin", label: { ru: "Вход по паролю", en: "Password login" }, default: true },
        { kind: "boolean", key: "showCodeLogin", label: { ru: "Вход по коду на e-mail", en: "Email code login" }, default: true },
        { kind: "boolean", key: "showTelegramLogin", label: { ru: "Telegram", en: "Telegram" }, default: true },
        { kind: "boolean", key: "showGoogleLogin", label: { ru: "Google", en: "Google" }, default: true },
        { kind: "boolean", key: "showYandexLogin", label: { ru: "Yandex", en: "Yandex" }, default: true },
      ],
    },
    {
      title: { ru: "Тексты", en: "Texts" },
      fields: [
        { kind: "text", key: "headTag", label: { ru: "Тег вверху", en: "Head tag" }, placeholder: "SN · /AUTH" },
        { kind: "text", key: "title", label: { ru: "Заголовок", en: "Title" }, placeholder: "Вход" },
        { kind: "text", key: "subtitle", label: { ru: "Подпись", en: "Subtitle" }, placeholder: "▸ Подключение к личному кабинету", multiline: true },
        { kind: "text", key: "tabPasswordLabel", label: { ru: "Таб «Пароль»", en: "Password tab" }, placeholder: "Пароль" },
        { kind: "text", key: "tabCodeLabel", label: { ru: "Таб «Код на e-mail»", en: "Code tab" }, placeholder: "Код на e-mail" },
        { kind: "text", key: "emailLabel", label: { ru: "Лейбл e-mail", en: "Email label" }, placeholder: "E-mail" },
        { kind: "text", key: "emailPlaceholder", label: { ru: "Подсказка e-mail", en: "Email placeholder" }, placeholder: "you@solonet.io" },
        { kind: "text", key: "passwordLabel", label: { ru: "Лейбл пароля", en: "Password label" }, placeholder: "Пароль" },
        { kind: "text", key: "passwordPlaceholder", label: { ru: "Подсказка пароля", en: "Password placeholder" }, placeholder: "••••••••••••" },
        { kind: "text", key: "codeLabel", label: { ru: "Лейбл кода", en: "Code label" }, placeholder: "Код из письма" },
        { kind: "text", key: "codePlaceholder", label: { ru: "Подсказка кода", en: "Code placeholder" }, placeholder: "123456" },
        { kind: "text", key: "showPwLabel", label: { ru: "«Показать»", en: "Show label" }, placeholder: "показать" },
        { kind: "text", key: "hidePwLabel", label: { ru: "«Скрыть»", en: "Hide label" }, placeholder: "скрыть" },
        { kind: "text", key: "rememberLabel", label: { ru: "«Запомнить»", en: "Remember label" }, placeholder: "Запомнить" },
        { kind: "text", key: "forgotLabel", label: { ru: "«Забыли пароль?»", en: "Forgot label" }, placeholder: "Забыли пароль?" },
        { kind: "text", key: "submitLabel", label: { ru: "Кнопка отправки", en: "Submit label" }, placeholder: "Войти" },
        { kind: "text", key: "sendCodeLabel", label: { ru: "Кнопка «Получить код»", en: "Send code label" }, placeholder: "Получить код" },
        { kind: "text", key: "requestNewCodeLabel", label: { ru: "«Запросить новый код»", en: "Request new code" }, placeholder: "Запросить новый код" },
        { kind: "text", key: "successLabel", label: { ru: "Текст успеха", en: "Success label" }, placeholder: "OK · туннель открыт" },
        { kind: "text", key: "orLabel", label: { ru: "«или»", en: "Or label" }, placeholder: "или" },
        { kind: "text", key: "telegramLabel", label: { ru: "Кнопка Telegram", en: "Telegram label" }, placeholder: "Telegram" },
        { kind: "text", key: "googleLabel", label: { ru: "Кнопка Google", en: "Google label" }, placeholder: "Google" },
        { kind: "text", key: "yandexLabel", label: { ru: "Кнопка Yandex", en: "Yandex label" }, placeholder: "Yandex" },
      ],
    },
    {
      title: { ru: "Ссылки входа", en: "Sign-in links" },
      tab: "data",
      fields: [
        { kind: "text", key: "forgotHref", label: { ru: "Ссылка восстановления", en: "Reset href" }, placeholder: "/reset-password" },
        { kind: "text", key: "googleHref", label: { ru: "Google href", en: "Google href" }, placeholder: "/api/auth/google/authorize" },
        { kind: "text", key: "yandexHref", label: { ru: "Yandex href", en: "Yandex href" }, placeholder: "/api/auth/yandex/authorize" },
        { kind: "text", key: "successRedirect", label: { ru: "Куда редирект после входа", en: "Success redirect" }, placeholder: "/dashboard" },
      ],
    },
    {
      title: { ru: "Приглашения и сессия", en: "Invites and session" },
      fields: [
        { kind: "text", key: "inviteReferralTitle", label: { ru: "Реферал: заголовок", en: "Referral title" }, placeholder: "Вас пригласил друг" },
        { kind: "text", key: "inviteReferralText", label: { ru: "Реферал: текст", en: "Referral text" }, placeholder: "Зарегистрируйтесь и получите бонус" },
        { kind: "text", key: "invitePartnerTitle", label: { ru: "Партнёр: заголовок", en: "Partner title" }, placeholder: "Приглашение от партнёра" },
        { kind: "text", key: "invitePartnerText", label: { ru: "Партнёр: текст", en: "Partner text" }, placeholder: "Зарегистрируйтесь по приглашению" },
        { kind: "text", key: "inviteGiftTitle", label: { ru: "Подарок: заголовок", en: "Gift title" }, placeholder: "Вам подарили подписку" },
        { kind: "text", key: "inviteGiftText", label: { ru: "Подарок: текст", en: "Gift text" }, placeholder: "Войдите, чтобы активировать подарок" },
        { kind: "text", key: "inviteOkLabel", label: { ru: "Кнопка «понятно»", en: "Got it label" }, placeholder: "Понятно" },
      ],
    },
    {
      title: { ru: "Проверка полей", en: "Field validation" },
      fields: [
        { kind: "text", key: "errEmailRequired", label: { ru: "Не введён e-mail", en: "Email required" }, placeholder: "Введите e-mail" },
        { kind: "text", key: "errPasswordRequired", label: { ru: "Не введён пароль", en: "Password required" }, placeholder: "Введите пароль" },
        { kind: "text", key: "errCodeRequired", label: { ru: "Не введён код", en: "Code required" }, placeholder: "Введите код" },
        { kind: "text", key: "errCaptchaRequired", label: { ru: "Не пройдена капча", en: "Captcha required" }, placeholder: "Подтвердите, что вы не робот" },
      ],
    },
    {
      title: { ru: "Ошибки входа", en: "Login errors" },
      fields: [
        { kind: "text", key: "errWrongCredentials", label: { ru: "Неверная пара e-mail/пароль", en: "Wrong credentials" }, placeholder: "Неверный e-mail или пароль" },
        { kind: "text", key: "errSendCodeFailed", label: { ru: "Код не отправился", en: "Send code failed" }, placeholder: "Не удалось отправить код" },
        { kind: "text", key: "errWrongCode", label: { ru: "Неверный код", en: "Wrong code" }, placeholder: "Неверный код" },
      ],
    },
  ],
  defaultPageHeader: [
    {
      title: { ru: "Источник заголовка", en: "Title source" },
      tab: "data",
      description: { ru: "«Авто» — title подтянется из URL раздела (профиль/подписки/рефералы и т.д.). «Ручной» — admin задаёт текст вручную.", en: "«Auto» — title auto-pulled from URL. «Manual» — admin sets text." },
      fields: [
        { kind: "select", key: "mode", label: { ru: "Режим", en: "Mode" }, default: "auto", options: [{ value: "auto", label: { ru: "Авто из URL раздела", en: "Auto from URL" } }, { value: "manual", label: { ru: "Ручной (admin задаёт текст)", en: "Manual (admin sets text)" } }] },
      ],
    },
    {
      title: { ru: "Авто-режим", en: "Auto mode" },
      fields: [
        { kind: "text", key: "meta", label: { ru: "Метa справа (опц.)", en: "Right meta (opt.)" }, placeholder: "" },
      ],
    },
    {
      title: { ru: "Ручной режим", en: "Manual mode" },
      fields: [
        { kind: "text", key: "title", label: { ru: "Заголовок (uppercase)", en: "Title" }, placeholder: "Профиль" },
        { kind: "text", key: "meta", label: { ru: "Метa справа", en: "Right meta" }, placeholder: "ID · SK-9F3A2C" },
      ],
    },
    {
      title: "Подзаголовок",
      fields: [
        { kind: "text", key: "subtitle", label: { ru: "Подзаголовок", en: "Subtitle" }, placeholder: "" },
      ],
    },
  ],
  defaultSidebar: [
    {
      title: { ru: "Бренд", en: "Brand" },
      fields: [
        { kind: "text", key: "brandName", label: { ru: "Название", en: "Brand name" }, placeholder: "SOLONET" },
        { kind: "text", key: "brandVersion", label: { ru: "Версия", en: "Version" }, placeholder: "v 4.2.0" },
      ],
    },
    {
      title: { ru: "Статус соединения", en: "Connection" },
      fields: [
        { kind: "text", key: "connTunnelLabel", label: { ru: "Метка туннеля", en: "Tunnel label" }, placeholder: "// TUNNEL" },
        { kind: "text", key: "connTunnelValue", label: { ru: "Туннель ID", en: "Tunnel ID" }, placeholder: "WG-01" },
        { kind: "text", key: "connStatusConnectedLabel", label: { ru: "Статус: подключён (есть подписки)", en: "Status: connected (has keys)" }, placeholder: "CONNECTED" },
        { kind: "text", key: "connStatusDisconnectedLabel", label: { ru: "Статус: отключён (нет подписок)", en: "Status: disconnected (no keys)" }, placeholder: "DISCONNECTED" },
        { kind: "text", key: "connLocLabel", label: { ru: "Локация", en: "Location" }, placeholder: "NL · AMS-03 · 14 ms" },
        { kind: "boolean", key: "showConnCard", label: { ru: "Показать карту соединения", en: "Show conn card" }, default: true },
      ],
    },
    {
      title: { ru: "Табы навигации", en: "Nav tabs" },
      tab: "data",
      description: { ru: "ID таба = screenId экрана. Тот же список, что и в облаке → «Кабинет» → «Меню кабинета» — правки применяются к сайдбару и мобильному бару.", en: "Tab id = screen's screenId. The same list as in the theme cloud → “Cabinet” → “Cabinet menu” — edits apply to the sidebar and the mobile bar." },
      fields: [
        { kind: "text", key: "tabGroupId", label: { ru: "Группа табов", en: "tabGroupId" }, placeholder: "cabinet" },
        { kind: "select", key: "iconPack", label: { ru: "Пак иконок (стиль)", en: "Icon pack (style)" }, default: "line", source: "iconPacks" },
        { kind: "list", key: "tabs", label: { ru: "Табы", en: "Tabs" }, addLabel: { ru: "Таб", en: "Tab" }, newItem: { id: "profile", label: "Раздел", num: "01" }, item: [{ kind: "text", key: "id", label: { ru: "ID (screenId)", en: "ID" }, placeholder: "profile" }, { kind: "text", key: "num", label: { ru: "Номер", en: "Num" }, placeholder: "01" }, { kind: "text", key: "label", label: { ru: "Подпись", en: "Label" } }, { kind: "select", key: "icon", label: { ru: "Иконка", en: "Icon" }, source: "sidebarIcons", allowEmpty: true }, { kind: "select", key: "badgeKey", label: { ru: "Бейдж (api)", en: "Badge (api)" }, options: [{ value: "unreadNotifications", label: { ru: "Непрочитанные уведомления", en: "Unread notifications" } }, { value: "giftsClaimed", label: { ru: "Получено подарков", en: "Gifts claimed" } }], allowEmpty: true, emptyLabel: { ru: "Нет бейджа", en: "No badge" } }, { kind: "text", key: "badge", label: { ru: "Бейдж (статика)", en: "Badge (static)" }, placeholder: "2" }, { kind: "boolean", key: "visible", label: { ru: "Показывать", en: "Visible" }, default: true }] },
      ],
    },
    {
      title: { ru: "Подвал пользователя", en: "User footer" },
      fields: [
        { kind: "text", key: "userPlanFormat", label: { ru: "Подпись плана", en: "Plan label" }, placeholder: "PRIME · 247 дн" },
        { kind: "text", key: "logoutLabel", label: { ru: "Иконка выхода", en: "Logout icon" }, placeholder: "↗" },
        { kind: "boolean", key: "showUserFooter", label: { ru: "Показать подвал", en: "Show user footer" }, default: true },
      ],
    },
    {
      title: "Статус связи и подписка",
      fields: [
        { kind: "text", key: "connStatusLabel", label: { ru: "Подпись статуса связи", en: "Connection status label" }, placeholder: "На связи" },
        { kind: "text", key: "connStatusOfflineLabel", label: { ru: "Статус «не в сети»", en: "Offline status" }, placeholder: "Не в сети" },
        { kind: "text", key: "menuLabel", label: { ru: "Заголовок меню", en: "Menu label" }, placeholder: "Меню" },
        { kind: "text", key: "userPlanEmpty", label: { ru: "Нет подписки", en: "No subscription" }, placeholder: "Нет подписки" },
        { kind: "boolean", key: "edgeRail", label: { ru: "Узкая полоса у края", en: "Edge rail" } },
      ],
    },
  ],
  defaultStatRow: [
    {
      title: { ru: "Показатели", en: "Stats" },
      fields: [
        { kind: "list", key: "items", label: { ru: "Показатели", en: "Stats" }, addLabel: { ru: "Показатель", en: "Stat" }, newItem: { label: "", value: "", suffix: "" }, item: [
          { kind: "text", key: "label", label: { ru: "Подпись", en: "Label" } },
          { kind: "text", key: "value", label: { ru: "Значение", en: "Value" } },
          { kind: "text", key: "suffix", label: { ru: "Приписка", en: "Suffix" } },
        ] },
      ],
    },
    {
      title: "Оформление",
      tab: "effects",
      fields: [
        { kind: "boolean", key: "accentStyle", label: { ru: "Акцентный стиль", en: "Accent style" } },
      ],
    },
    {
      title: { ru: "Геометрия", en: "Geometry" },
      tab: "style",
      fields: [
        { kind: "number", key: "borderWidth", label: { ru: "Толщина рамки (px)", en: "Border width (px)" }, min: 0, max: 8, default: 1 },
        { kind: "number", key: "radius", label: { ru: "Скругление (px)", en: "Radius (px)" }, min: 0, max: 48, default: 0 },
        { kind: "number", key: "padding", label: { ru: "Внутренний отступ (px)", en: "Padding (px)" }, min: 0, max: 64, default: 20 },
        { kind: "number", key: "gap", label: { ru: "Промежуток между тайлами (px)", en: "Gap (px)" }, min: 0, max: 64, default: 24 },
      ],
    },
    {
      title: { ru: "Шрифты", en: "Fonts" },
      fields: [
        { kind: "select", key: "monoFontFamily", label: { ru: "Шрифт label (пусто = из темы)", en: "Label font (empty = from theme)" }, source: "fonts", allowEmpty: true, emptyLabel: { ru: "Из темы", en: "From theme" }, allowCustom: true, customPlaceholder: { ru: "из темы / JetBrains Mono", en: "из темы / JetBrains Mono" } },
      ],
    },
  ],
  defaultTopbar: [
    {
      title: { ru: "Поиск", en: "Search" },
      fields: [
        { kind: "text", key: "searchPlaceholder", label: { ru: "Подсказка", en: "Placeholder" }, placeholder: "Поиск серверов, устройств…" },
        { kind: "boolean", key: "showSearch", label: { ru: "Показать поиск", en: "Show search" }, default: true },
      ],
    },
    {
      title: { ru: "Поиск: быстрые действия", en: "Search: quick actions" },
      tab: "data",
      description: { ru: "Действия в выпадашке поиска. Могут переключать вкладку, открывать экран блока (группа:экран, напр. tariffPanel:switch, profile:settings), запускать путь клиента или вести по ссылке. Если список пуст — используется стандартный набор.", en: "Quick actions in the search dropdown. Can switch tab, open a block screen (group:screen, e.g. tariffPanel:switch), start a customer flow or follow a link. Empty list = default set." },
      fields: [
        { kind: "list", key: "searchActions", label: { ru: "Быстрые действия", en: "Quick actions" }, addLabel: { ru: "Действие", en: "Action" }, newItem: { label: "", sublabel: "", keywords: "" }, item: [{ kind: "text", key: "label", label: { ru: "Название", en: "Label" } }, { kind: "text", key: "sublabel", label: { ru: "Подпись", en: "Sublabel" } }, { kind: "text", key: "keywords", label: { ru: "Ключевые слова (через пробел)", en: "Keywords" } }, { kind: "select", key: "tabId", label: { ru: "Вкладка кабинета", en: "Cabinet tab" }, source: "cabinetTabs", allowEmpty: true, emptyLabel: { ru: "— не переключать —", en: "— keep —" } }, { kind: "text", key: "screenGroup", label: { ru: "Экран блока (группа:экран)", en: "Block screen (group:screen)" }, placeholder: "tariffPanel:switch" }, { kind: "select", key: "flowId", label: { ru: "Путь клиента (flow id)", en: "Customer flow id" }, source: "flows", allowEmpty: true, emptyLabel: { ru: "Без сценария", en: "No flow" }, allowCustom: true, customPlaceholder: { ru: "purchase", en: "purchase" } }, { kind: "text", key: "href", label: { ru: "Ссылка (если без вкладки/flow)", en: "Link (fallback)" }, placeholder: "/tariffs" }] },
      ],
    },
    {
      title: { ru: "Окно поддержки", en: "Support dialog" },
      description: { ru: "Показывается при клике на круглую иконку оператора.", en: "Shown when the round support icon is clicked." },
      fields: [
        { kind: "text", key: "supportConfirmTitle", label: { ru: "Заголовок", en: "Title" }, placeholder: "Возникли проблемы?" },
        { kind: "text", key: "supportConfirmText", label: { ru: "Текст", en: "Text" }, placeholder: "Если что-то не работает — поддержка поможет." },
        { kind: "text", key: "supportConfirmYes", label: { ru: "Кнопка обращения", en: "Confirm label" }, placeholder: "Обратиться в поддержку" },
        { kind: "text", key: "supportConfirmNo", label: { ru: "Кнопка отказа", en: "Decline label" }, placeholder: "Нет, всё в порядке" },
        { kind: "list", key: "supportLinks", label: { ru: "Ссылки", en: "Links" }, addLabel: { ru: "Ссылка", en: "Link" }, newItem: { label: "Кнопка", href: "https://" }, item: [{ kind: "text", key: "label", label: { ru: "Подпись", en: "Label" } }, { kind: "text", key: "href", label: { ru: "Ссылка", en: "Href" }, placeholder: "https://telegram.me/... или /dashboard" }] },
      ],
    },
    {
      title: { ru: "Кнопки", en: "Action buttons" },
      tab: "data",
      fields: [
        { kind: "list", key: "actions", label: { ru: "Кнопки", en: "Buttons" }, addLabel: { ru: "Кнопка", en: "Button" }, newItem: { label: "Кнопка", variant: "ghost", href: "#" }, item: [{ kind: "text", key: "label", label: { ru: "Подпись", en: "Label" } }, { kind: "select", key: "variant", label: { ru: "Стиль", en: "Variant" }, default: "ghost", options: [{ value: "ghost", label: { ru: "Призрачная", en: "Ghost" } }, { value: "red", label: { ru: "Акцентная (красная)", en: "Accent (red)" } }, { value: "supportIcon", label: { ru: "Круглая иконка оператора", en: "Round support icon" } }] }, { kind: "select", key: "visibility", label: { ru: "Когда показывать", en: "Visibility" }, default: "always", options: [{ value: "always", label: { ru: "Всегда показывать", en: "Always show" } }, { value: "if_no_keys", label: { ru: "Скрывать, если есть подписки", en: "Hide if user has subscriptions" } }, { value: "hidden", label: { ru: "Выключена (всегда скрыта)", en: "Disabled (always hidden)" } }] }, { kind: "select", key: "linkType", label: { ru: "Тип ссылки", en: "Link type" }, default: "url", options: [{ value: "url", label: { ru: "Произвольная ссылка", en: "Custom URL" } }, { value: "telegram", label: { ru: "Telegram-профиль", en: "Telegram profile" } }, { value: "tab", label: { ru: "Раздел кабинета", en: "Cabinet tab" } }] }, { kind: "text", key: "href", label: { ru: "Ссылка", en: "Href" }, placeholder: "/dashboard или https://...", when: { key: "linkType", equals: "url" } }, { kind: "text", key: "telegramUsername", label: { ru: "Telegram username (без @)", en: "Telegram username (no @)" }, placeholder: "durov", when: { key: "linkType", equals: "telegram" } }, { kind: "select", key: "tabId", label: { ru: "Вкладка кабинета", en: "Cabinet tab" }, source: "cabinetTabs", allowEmpty: true, when: { key: "linkType", equals: "tab" } }, { kind: "boolean", key: "trialMode", label: { ru: "Показывать текст пробного периода", en: "Show trial label" } }, { kind: "text", key: "trialLabel", label: { ru: "Текст, когда триал доступен", en: "Label when trial available" }, placeholder: "Попробовать", when: { key: "trialMode", truthy: true } }] },
      ],
    },
    {
      title: "Приветствие и иконки",
      fields: [
        { kind: "text", key: "greetingFormat", label: { ru: "Приветствие ({name})", en: "Greeting" }, placeholder: "С возвращением, {name} 👋" },
        { kind: "boolean", key: "showBell", label: { ru: "Колокольчик уведомлений", en: "Notifications bell" }, default: true },
        { kind: "boolean", key: "showGear", label: { ru: "Шестерёнка настроек", en: "Settings gear" }, default: true },
      ],
    },
    {
      title: "Ошибка пробной подписки",
      fields: [
        { kind: "text", key: "trialErrorText", label: { ru: "Ошибка активации", en: "Activation error" }, placeholder: "Не удалось активировать пробную подписку", multiline: true },
      ],
    },
    {
      title: "Поиск по кабинету",
      fields: [
        { kind: "text", key: "searchSectionHint", label: { ru: "Подпись найденного раздела", en: "Section hit caption" }, placeholder: "Раздел кабинета" },
      ],
    },
  ],
};
