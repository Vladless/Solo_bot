import type { PackForm } from "@/components/constructor/packForms";

/** Формы блоков «Стандарта», которые раньше лежали в общих файлах форм ядра. */
export const DEFAULT_CORE_FORMS: Record<string, PackForm> = {
  defaultAdminTools: [
    {
      title: { ru: "Тексты", en: "Texts" },
      description: { ru: "Заголовок и подписи кнопок. Блок виден только администраторам и только на мобильном.", en: "Header and button labels. The block is shown only to admins and only on mobile." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "// Администратор" },
        { kind: "text", key: "hint", label: { ru: "Hint", en: "Hint" }, placeholder: "admin" },
        { kind: "text", key: "note", label: { ru: "Примечание", en: "Note" }, placeholder: "Видно только администраторам сайта", multiline: true },
        { kind: "text", key: "adminPanelLabel", label: { ru: "Кнопка веб-админки", en: "Web admin button" }, placeholder: "Веб-админка" },
        { kind: "text", key: "adminPanelHref", label: { ru: "Ссылка на админку", en: "Admin link" }, placeholder: "/admin" },
        { kind: "text", key: "analyticsLabel", label: { ru: "Аналитика", en: "Analytics" }, placeholder: "АНАЛИТИКА" },
        { kind: "text", key: "logsLabel", label: { ru: "Логи", en: "Logs" }, placeholder: "ЛОГИ И ЗДОРОВЬЕ" },
        { kind: "text", key: "auditLabel", label: { ru: "Журнал", en: "Audit" }, placeholder: "ЖУРНАЛ ДЕЙСТВИЙ" },
        { kind: "select", key: "monoFontFamily", label: { ru: "Шрифт (пусто = из темы)", en: "Font (empty = from theme)" }, source: "fonts", allowEmpty: true, emptyLabel: { ru: "Из темы", en: "From theme" }, allowCustom: true, customPlaceholder: { ru: "из темы / JetBrains Mono", en: "из темы / JetBrains Mono" } },
      ],
    },
  ],
  defaultAppDownload: [
    {
      title: { ru: "Вид блока", en: "Block view" },
      tab: "style",
      fields: [
        { kind: "widget", widget: "viewMode" },
      ],
    },
    {
      title: { ru: "Заголовки", en: "Titles" },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок панели", en: "Panel header" }, placeholder: "Установка приложения" },
        { kind: "text", key: "platformTitle", label: { ru: "Экран устройства", en: "Device screen" }, placeholder: "Выберите устройство" },
        { kind: "text", key: "clientTitle", label: { ru: "Экран приложения", en: "App screen" }, placeholder: "Выберите приложение" },
        { kind: "text", key: "stepsTitle", label: { ru: "Экран инструкции", en: "Guide screen" }, placeholder: "Что делать" },
      ],
    },
    {
      title: { ru: "Названия платформ", en: "Platform labels" },
      fields: [
        { kind: "widget", widget: "platformLabels" },
      ],
    },
    {
      title: { ru: "Приложения", en: "Apps" },
      description: { ru: "Список, из которого клиент выбирает клиент VPN.", en: "The list the client picks a VPN app from." },
      tab: "data",
      fields: [
        { kind: "widget", widget: "appCatalog" },
      ],
    },
    {
      title: { ru: "Шаги установки", en: "Install steps" },
      description: { ru: "Шаг можно привязать к устройству и приложению — тогда он покажется только им.", en: "A step can be bound to a device and an app — then only they show it." },
      fields: [
        { kind: "widget", widget: "appSteps" },
      ],
    },
    {
      title: { ru: "Кнопки и ссылки", en: "Buttons & links" },
      tab: "data",
      fields: [
        { kind: "text", key: "downloadLabel", label: { ru: "Ссылка «Скачать»", en: "«Download» link" }, placeholder: "Скачать" },
        { kind: "text", key: "installLabel", label: { ru: "Кнопка установки", en: "Install button" }, placeholder: "Установить {app}" },
        { kind: "boolean", key: "showDownload", label: { ru: "Кнопка установки на экране инструкции", en: "Install button on the guide screen" }, default: true },
        { kind: "boolean", key: "showSteps", label: { ru: "Показывать шаги", en: "Show steps" }, default: true },
      ],
    },
    {
      title: { ru: "Навигация мастера", en: "Wizard navigation" },
      fields: [
        { kind: "text", key: "backLabel", label: { ru: "Назад", en: "Back" }, placeholder: "Назад" },
        { kind: "text", key: "restartLabel", label: { ru: "Сменить приложение", en: "Change app" }, placeholder: "Выбрать другое приложение" },
        { kind: "text", key: "counterFormat", label: { ru: "Счётчик шагов", en: "Step counter" }, placeholder: "Шаг {n} из {total}" },
      ],
    },
    {
      title: { ru: "Пустые состояния", en: "Empty states" },
      fields: [
        { kind: "text", key: "noClientsText", label: { ru: "Нет приложений", en: "No apps" }, placeholder: "Для этой платформы приложений пока нет" },
        { kind: "text", key: "noStepsText", label: { ru: "Нет шагов", en: "No steps" }, placeholder: "Инструкция появится после выбора приложения" },
        { kind: "text", key: "hint", label: { ru: "Подсказка", en: "Hint" } },
      ],
    },
  ],
  defaultBalanceTopUp: [
    {
      title: { ru: "Переход", en: "Navigation" },
      fields: [
        { kind: "select", key: "checkoutSlug", source: "pages", allowEmpty: true, allowCustom: true, label: { ru: "Страница оплаты", en: "Checkout page" }, emptyLabel: { ru: "— по умолчанию —", en: "— default —" } },
      ],
    },
    {
      title: { ru: "Тексты", en: "Texts" },
      fields: [
        { kind: "text", key: "amountLabel", label: { ru: "Подпись поля суммы", en: "Amount field label" }, placeholder: "Сумма, ₽" },
        { kind: "text", key: "amountPlaceholder", label: { ru: "Плейсхолдер суммы", en: "Amount placeholder" }, placeholder: "500" },
        { kind: "text", key: "submitLabel", label: { ru: "Кнопка пополнения", en: "Top-up button" }, placeholder: "Пополнить" },
      ],
    },
    {
      title: { ru: "Суммы", en: "Amounts" },
      description: { ru: "Кнопки быстрых сумм и минимальная сумма пополнения.", en: "Quick amount buttons and the minimum top-up amount." },
      fields: [
        { kind: "number", key: "minAmount", label: { ru: "Минимальная сумма", en: "Minimum amount" }, min: 1, max: 1000000, default: 50 },
        { kind: "numbers", key: "presetAmounts", label: { ru: "Быстрые суммы", en: "Quick amounts" } },
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок панели", en: "Panel header" } },
        { kind: "text", key: "panelHeaderHint", label: { ru: "Подпись заголовка", en: "Header hint" } },
      ],
    },
  ],
  defaultChangePassword: [
    {
      title: { ru: "Заголовок и поля", en: "Header & fields" },
      description: { ru: "Подписи полей формы смены пароля.", en: "Labels of the password form fields." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "Смена пароля" },
        { kind: "text", key: "currentLabel", label: { ru: "Текущий пароль", en: "Current password" }, placeholder: "Текущий пароль" },
        { kind: "text", key: "newLabel", label: { ru: "Новый пароль", en: "New password" }, placeholder: "Новый пароль" },
        { kind: "text", key: "confirmLabel", label: { ru: "Повтор пароля", en: "Repeat password" }, placeholder: "Повторите новый пароль" },
      ],
    },
    {
      title: { ru: "Кнопка", en: "Button" },
      description: { ru: "Подпись меняется в зависимости от того, есть ли у клиента пароль.", en: "The label depends on whether the client already has a password." },
      fields: [
        { kind: "text", key: "submitLabel", label: { ru: "Пароль уже есть", en: "Password exists" }, placeholder: "Сменить пароль" },
        { kind: "text", key: "setSubmitLabel", label: { ru: "Пароля ещё нет", en: "No password yet" }, placeholder: "Установить пароль" },
      ],
    },
    {
      title: { ru: "Сообщения", en: "Messages" },
      fields: [
        { kind: "text", key: "tooShortLabel", label: { ru: "Пароль короткий", en: "Password too short" }, placeholder: "Минимум 8 символов" },
        { kind: "text", key: "mismatchLabel", label: { ru: "Пароли не совпадают", en: "Passwords do not match" }, placeholder: "Пароли не совпадают" },
        { kind: "text", key: "successLabel", label: { ru: "Пароль изменён", en: "Password changed" }, placeholder: "Пароль изменён" },
        { kind: "text", key: "setSuccessLabel", label: { ru: "Пароль установлен", en: "Password set" }, placeholder: "Пароль установлен" },
        { kind: "text", key: "failureLabel", label: { ru: "Ошибка сохранения", en: "Save error" }, placeholder: "Не удалось сохранить пароль" },
        { kind: "select", key: "monoFontFamily", label: { ru: "Шрифт (пусто = из темы)", en: "Font (empty = from theme)" }, source: "fonts", allowEmpty: true, emptyLabel: { ru: "Из темы", en: "From theme" }, allowCustom: true, customPlaceholder: { ru: "из темы / JetBrains Mono", en: "из темы / JetBrains Mono" } },
        { kind: "text", key: "hint", label: { ru: "Подсказка", en: "Hint" } },
      ],
    },
  ],
  defaultCheckoutProvider: [
    {
      title: { ru: "Способ оплаты", en: "Payment method" },
      description: { ru: "Список касс подтягивается из подключённых в админке. Здесь — тексты и поведение.", en: "Providers are loaded from the ones enabled in admin. Here — texts and behaviour." },
      tab: "content",
      fields: [
        { kind: "text", key: "title", label: { ru: "Заголовок", en: "Title" }, placeholder: "Способ оплаты" },
        { kind: "text", key: "subtitle", label: { ru: "Подзаголовок", en: "Subtitle" } },
        { kind: "text", key: "noProvidersText", label: { ru: "Текст «нет касс»", en: "No-providers text" }, placeholder: "Нет доступных способов оплаты" },
        { kind: "text", key: "balanceCoversText", label: { ru: "Хватает баланса", en: "Balance covers" }, placeholder: "Спишется с баланса — выбор кассы не нужен.", multiline: true },
      ],
    },
    {
      title: { ru: "Кассы", en: "Providers" },
      description: { ru: "Порядок, названия и предвыбранная касса.", en: "Order, labels and the pre-selected provider." },
      tab: "data",
      fields: [
        { kind: "select", key: "defaultProvider", label: { ru: "Касса по умолчанию", en: "Default provider" }, source: "paymentProviders", allowEmpty: true, emptyLabel: { ru: "Автоматически (первая включённая)", en: "Automatic (first enabled)" } },
        { kind: "boolean", key: "showSingleProvider", label: { ru: "Показывать кассу, даже если она одна", en: "Show provider even if only one" }, default: true },
        { kind: "widget", widget: "paymentProviders" },
      ],
    },
  ],
  defaultCheckoutTrust: [
    {
      title: { ru: "Гарантии оплаты", en: "Payment trust" },
      description: { ru: "Карточки доверия и принимаемые способы оплаты.", en: "Trust cards and accepted payment methods." },
      tab: "content",
      fields: [
        {
          kind: "list",
          key: "items",
          label: { ru: "Карточки", en: "Cards" },
          addLabel: { ru: "Карточка", en: "Card" },
          newItem: { title: "", desc: "" },
          item: [
            { kind: "text", key: "title", label: { ru: "Заголовок", en: "Title" }, placeholder: "Безопасная оплата" },
            { kind: "text", key: "desc", label: { ru: "Описание", en: "Description" }, placeholder: "Данные карты мы не видим и не храним" },
          ],
        },
        { kind: "text", key: "methodsLabel", label: { ru: "Подпись способов оплаты", en: "Payment methods label" }, placeholder: "Принимаем" },
        { kind: "text", key: "methodsText", label: { ru: "Список способов (через ·)", en: "Methods list (· separated)" }, placeholder: "Visa · Mastercard · МИР · СБП · USDT" },
        { kind: "note", label: { ru: "Каждый способ — отдельная плашка. Разделяйте точкой «·».", en: "Each method becomes a chip. Separate with «·»." } },
      ],
    },
  ],
  defaultConnectCta: [
    {
      title: { ru: "Вид блока", en: "Block view" },
      tab: "style",
      fields: [
        { kind: "widget", widget: "viewMode" },
      ],
    },
    {
      title: { ru: "Шапка и заголовок", en: "Header & title" },
      description: { ru: "Подпись сверху и заголовок, если у ключа нет имени.", en: "Top hint and the title used when the key has no alias." },
      fields: [
        { kind: "text", key: "subheader", label: { ru: "Подпись", en: "Caption" }, placeholder: "" },
        { kind: "text", key: "fallbackTitle", label: { ru: "Заголовок по умолчанию", en: "Fallback title" }, placeholder: "VPN" },
        { kind: "text", key: "subtitleText", label: { ru: "Подзаголовок", en: "Subtitle" }, placeholder: "Ключ доступа к подписке" },
      ],
    },
    {
      title: { ru: "Экраны мастера", en: "Wizard screens" },
      fields: [
        { kind: "text", key: "platformTitle", label: { ru: "Экран устройства", en: "Device screen" }, placeholder: "Ваше устройство" },
        { kind: "text", key: "clientTitle", label: { ru: "Экран приложения", en: "App screen" }, placeholder: "Приложение" },
        { kind: "text", key: "connectTitle", label: { ru: "Экран подключения", en: "Connect screen" }, placeholder: "Подключение" },
        { kind: "text", key: "stepsTitle", label: { ru: "Заголовок шагов", en: "Steps title" }, placeholder: "Что делать" },
      ],
    },
    {
      title: { ru: "Названия платформ", en: "Platform labels" },
      fields: [
        { kind: "widget", widget: "platformLabels" },
      ],
    },
    {
      title: { ru: "Приложения", en: "Apps" },
      description: { ru: "Ссылка импорта выбранного приложения открывает подписку прямо в нём.", en: "The import link of the selected app opens the subscription inside it." },
      tab: "data",
      fields: [
        { kind: "widget", widget: "appCatalog" },
      ],
    },
    {
      title: { ru: "Шаги подключения", en: "Connect steps" },
      description: { ru: "Показываются на последнем экране мастера.", en: "Shown on the last wizard screen." },
      fields: [
        { kind: "widget", widget: "appSteps" },
      ],
    },
    {
      title: { ru: "Состояния", en: "States" },
      fields: [
        { kind: "text", key: "noKeysText", label: { ru: "Нет подписки", en: "No subscription" }, placeholder: "Нет активной подписки" },
        { kind: "text", key: "noKeysCtaLabel", label: { ru: "Кнопка «нет подписки» (пусто = скрыть)", en: "No-subscription button (empty = hide)" }, placeholder: "Оформить подписку" },
        { kind: "text", key: "noKeysCtaHref", label: { ru: "Ссылка кнопки (пусто = /tariffs)", en: "Button href (empty = /tariffs)" }, placeholder: "/tariffs" },
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" }, placeholder: "Загрузка..." },
        { kind: "text", key: "qrErrorText", label: { ru: "Ошибка QR", en: "QR error" }, placeholder: "Не удалось загрузить QR" },
        { kind: "text", key: "noClientsText", label: { ru: "Нет приложений", en: "No apps" }, placeholder: "Для этой платформы приложений пока нет" },
      ],
    },
    {
      title: { ru: "Выбор приложения", en: "App picker" },
      description: { ru: "В виде «Всё сразу» переключатели устройства и приложения показываются прямо в карточке.", en: "In the «All at once» view the device and app switchers sit inside the card." },
      fields: [
        { kind: "text", key: "downloadLabel", label: { ru: "Ссылка «Скачать»", en: "«Download» link" }, placeholder: "Скачать" },
        { kind: "text", key: "backLabel", label: { ru: "Назад", en: "Back" }, placeholder: "Назад" },
        { kind: "text", key: "counterFormat", label: { ru: "Счётчик шагов", en: "Step counter" }, placeholder: "Шаг {n} из {total}" },
        { kind: "boolean", key: "showAppPicker", label: { ru: "Показывать переключатели в карточке", en: "Show switchers in the card" }, default: true },
      ],
    },
    {
      title: { ru: "Кнопка «Подключить»", en: "«Connect» button" },
      description: { ru: "Открывает подписку в выбранном приложении. Если приложение не выбрано — ссылку панели.", en: "Opens the subscription in the selected app. With no app selected — the panel link." },
      fields: [
        { kind: "text", key: "connectLabel", label: { ru: "Текст кнопки", en: "Label" }, placeholder: "Подключить" },
        { kind: "boolean", key: "showConnect", label: { ru: "Показывать кнопку", en: "Show button" }, default: true },
      ],
    },
    {
      title: { ru: "Ссылка подписки", en: "Subscription link" },
      fields: [
        { kind: "text", key: "copyLabel", label: { ru: "До копирования", en: "Before copy" }, placeholder: "Копировать" },
        { kind: "text", key: "copiedLabel", label: { ru: "После копирования", en: "After copy" }, placeholder: "Скопировано" },
        { kind: "boolean", key: "showLink", label: { ru: "Показывать строку со ссылкой", en: "Show link row" }, default: true },
        { kind: "boolean", key: "showCopy", label: { ru: "Кнопка «Копировать»", en: "«Copy» button" }, default: true },
      ],
    },
    {
      title: { ru: "Поделиться и QR", en: "Share & QR" },
      fields: [
        { kind: "text", key: "shareLabel", label: { ru: "Поделиться", en: "Share" }, placeholder: "Поделиться" },
        { kind: "text", key: "shareTitle", label: { ru: "Заголовок Web Share", en: "Web Share title" }, placeholder: "Подключение к VPN" },
        { kind: "text", key: "qrLabel", label: { ru: "Открыть QR", en: "Open QR" }, placeholder: "QR-код" },
        { kind: "text", key: "qrCloseLabel", label: { ru: "Закрыть QR", en: "Close QR" }, placeholder: "Закрыть QR" },
        { kind: "text", key: "qrShareHint", label: { ru: "Подпись под QR", en: "QR caption" }, placeholder: "", multiline: true },
        { kind: "text", key: "renamePlaceholder", label: { ru: "Плейсхолдер имени", en: "Rename placeholder" }, placeholder: "Имя подписки" },
        { kind: "boolean", key: "showShare", label: { ru: "Кнопка «Поделиться»", en: "«Share» button" }, default: true },
        { kind: "boolean", key: "showQr", label: { ru: "Кнопка «QR-код»", en: "«QR code» button" }, default: true },
        { kind: "boolean", key: "showRename", label: { ru: "Переименование подписки", en: "Subscription rename" }, default: true },
        { kind: "text", key: "panelHint", label: { ru: "Подпись сверху", en: "Top hint" } },
      ],
    },
  ],
  defaultCtaBanner: [
    {
      title: { ru: "Финальный CTA", en: "Final CTA" },
      description: { ru: "Акцентный баннер в конце лендинга. Кнопка запускает flow (по умолчанию trial) — очистите flow, чтобы использовать обычную ссылку.", en: "Accent banner at the end of the landing. The button starts a flow (trial by default) — clear the flow to use a plain link." },
      tab: "content",
      fields: [
        { kind: "text", key: "title", label: { ru: "Заголовок", en: "Title" }, placeholder: "Готовы попробовать?" },
        { kind: "text", key: "subtitle", label: { ru: "Подзаголовок", en: "Subtitle" }, placeholder: "Пробный период бесплатно…" },
        { kind: "text", key: "ctaLabel", label: { ru: "Текст кнопки", en: "Button label" }, placeholder: "Начать бесплатно" },
        { kind: "text", key: "noteText", label: { ru: "Примечание под кнопкой", en: "Note under button" }, placeholder: "Карта не требуется" },
        { kind: "select", key: "ctaFlowId", label: { ru: "Flow кнопки", en: "Button flow id" }, source: "flows", allowEmpty: true, emptyLabel: { ru: "Без сценария", en: "No flow" }, allowCustom: true, customPlaceholder: { ru: "trial", en: "trial" } },
        { kind: "text", key: "ctaLink", label: { ru: "Ссылка (если без flow)", en: "Link (fallback)" }, placeholder: "/login" },
        { kind: "widget", widget: "buttonAction", prefix: "cta" },
      ],
    },
  ],
  defaultDailyBonus: [
    {
      title: { ru: "Бонус", en: "Bonus" },
      description: { ru: "Заголовок, вступление и подписи суммы. Сами суммы, кулдаун и условия выдачи задаются в настройках бота, раздел «Бонусы».", en: "Header, intro and amount labels. Amounts, cooldown and eligibility live in bot settings → Bonuses." },
      tab: "content",
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "Ежедневный бонус" },
        { kind: "text", key: "panelHeaderHintFmt", label: { ru: "Хинт серии ({streak})", en: "Streak hint ({streak})" }, placeholder: "серия {streak}" },
        { kind: "text", key: "introText", label: { ru: "Вступление", en: "Intro" }, placeholder: "Забирай бонус каждый день" },
        { kind: "text", key: "amountFmt", label: { ru: "Сумма ({amount})", en: "Amount ({amount})" }, placeholder: "{amount} ₽" },
        { kind: "text", key: "amountRangeFmt", label: { ru: "Диапазон ({min}, {max})", en: "Range ({min}, {max})" }, placeholder: "{min}–{max} ₽" },
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" }, placeholder: "Загрузка..." },
      ],
    },
    {
      title: { ru: "Кнопка и таймер", en: "Button and timer" },
      description: { ru: "Подписи получения бонуса и обратного отсчёта до следующего.", en: "Claim labels and the countdown to the next bonus." },
      tab: "content",
      fields: [
        { kind: "text", key: "claimLabel", label: { ru: "Кнопка", en: "Button" }, placeholder: "Забрать бонус" },
        { kind: "text", key: "claimingLabel", label: { ru: "Кнопка в работе", en: "Button busy" }, placeholder: "Начисляем..." },
        { kind: "text", key: "successFmt", label: { ru: "Успех ({amount})", en: "Success ({amount})" }, placeholder: "✓ Начислено {amount} ₽" },
        { kind: "text", key: "errorText", label: { ru: "Ошибка", en: "Error" }, placeholder: "✕ Не удалось начислить бонус" },
        { kind: "text", key: "waitLabel", label: { ru: "Подпись таймера", en: "Timer label" }, placeholder: "Следующий бонус через" },
        { kind: "text", key: "countdownFmt", label: { ru: "Таймер ({hh}:{mm}:{ss})", en: "Countdown ({hh}:{mm}:{ss})" }, placeholder: "{hh}:{mm}:{ss}" },
      ],
    },
    {
      title: { ru: "Почему бонус недоступен", en: "Why the bonus is locked" },
      description: { ru: "Текст под кнопкой для каждой причины отказа с сервера.", en: "Text shown for every refusal reason returned by the server." },
      tab: "content",
      fields: [
        { kind: "text", key: "reasonCooldownText", label: { ru: "Кулдаун", en: "Cooldown" }, placeholder: "Бонус на сегодня уже получен" },
        { kind: "text", key: "reasonLimitText", label: { ru: "Лимит за период", en: "Period limit" }, placeholder: "Лимит бонусов за период исчерпан" },
        { kind: "text", key: "reasonSubscriptionText", label: { ru: "Нужна подписка", en: "Subscription required" }, placeholder: "Бонус доступен с активной подпиской" },
        { kind: "text", key: "reasonAccountAgeText", label: { ru: "Аккаунт слишком новый", en: "Account too new" }, placeholder: "Бонус откроется чуть позже" },
        { kind: "text", key: "reasonDisabledText", label: { ru: "Бонус выключен", en: "Bonus disabled" }, placeholder: "Ежедневный бонус сейчас недоступен" },
      ],
    },
    {
      title: { ru: "Серия и итоги", en: "Streak and totals" },
      description: { ru: "Что показывать рядом с суммой: лестницу серии, всего получено, остаток бонусов.", en: "What to show next to the amount: the streak ladder, lifetime total, bonuses left." },
      tab: "content",
      fields: [
        { kind: "text", key: "ladderTitle", label: { ru: "Заголовок лестницы", en: "Ladder title" }, placeholder: "Лестница серии" },
        { kind: "text", key: "ladderDayFmt", label: { ru: "День лестницы ({days} — день серии, {day} — номер ступени)", en: "Ladder day ({days} — day of series, {day} — step number)" }, placeholder: "{days} д" },
        { kind: "text", key: "totalFmt", label: { ru: "Всего ({total})", en: "Total ({total})" }, placeholder: "всего получено {total} ₽" },
        { kind: "text", key: "claimsLeftFmt", label: { ru: "Остаток ({left})", en: "Left ({left})" }, placeholder: "осталось бонусов: {left}" },
        { kind: "boolean", key: "showStreak", label: { ru: "Показывать серию", en: "Show streak" }, default: true },
        { kind: "boolean", key: "showLadder", label: { ru: "Показывать лестницу серии", en: "Show streak ladder" }, default: true },
        { kind: "boolean", key: "showTotal", label: { ru: "Показывать всего получено", en: "Show lifetime total" }, default: true },
        { kind: "boolean", key: "showClaimsLeft", label: { ru: "Показывать остаток бонусов", en: "Show bonuses left" }, default: true },
        { kind: "number", key: "ladderMaxItems", label: { ru: "Ступеней лестницы в блоке", en: "Ladder steps shown" }, min: 1, max: 30, default: 7 },
      ],
    },
  ],
  defaultDeviceList: [
    {
      title: { ru: "Тексты", en: "Texts" },
      description: { ru: "Заголовок, счётчик устройств и состояния списка.", en: "Header, device counter and list states." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "Устройства" },
        { kind: "text", key: "hintFormat", label: { ru: "Счётчик", en: "Counter" }, placeholder: "{count} / {limit}" },
        { kind: "text", key: "emptyText", label: { ru: "Список пуст", en: "Empty list" }, placeholder: "Нет подключённых устройств" },
        { kind: "text", key: "noKeysText", label: { ru: "Нет подписки", en: "No subscription" }, placeholder: "Нет активной подписки" },
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" }, placeholder: "Загрузка..." },
        { kind: "text", key: "errorText", label: { ru: "Ошибка загрузки", en: "Load error" }, placeholder: "Не удалось загрузить устройства" },
        { kind: "text", key: "deviceFallbackName", label: { ru: "Имя без модели", en: "Fallback device name" }, placeholder: "Устройство" },
      ],
    },
    {
      title: { ru: "Отвязка устройства", en: "Device unbind" },
      description: { ru: "Подписи кнопки и результата отвязки.", en: "Button and result labels for unbinding." },
      fields: [
        { kind: "text", key: "deleteLabel", label: { ru: "Кнопка", en: "Button" }, placeholder: "Отвязать" },
        { kind: "text", key: "deleteBusyLabel", label: { ru: "В процессе", en: "In progress" }, placeholder: "…" },
        { kind: "text", key: "deleteSuccessText", label: { ru: "Успех", en: "Success" }, placeholder: "Устройство отвязано" },
        { kind: "text", key: "deleteErrorText", label: { ru: "Ошибка", en: "Error" }, placeholder: "Не удалось отвязать устройство" },
      ],
    },
    {
      title: { ru: "Сброс всех устройств", en: "Reset all devices" },
      description: { ru: "Кнопка в футере блока сбрасывает привязку всех устройств подписки. Показывается, только если сброс разрешён в настройках бота и устройства есть. Нажатие подтверждается вторым кликом.", en: "The footer button unbinds every device of the subscription. Shown only when reset is enabled in bot settings and devices exist. Requires a second click to confirm." },
      fields: [
        { kind: "text", key: "resetLabel", label: { ru: "Кнопка", en: "Button" }, placeholder: "Сбросить все" },
        { kind: "text", key: "resetConfirmLabel", label: { ru: "Подтверждение", en: "Confirmation" }, placeholder: "Точно сбросить?" },
        { kind: "text", key: "resetBusyLabel", label: { ru: "В процессе", en: "In progress" }, placeholder: "Сброс…" },
        { kind: "text", key: "resetSuccessFormat", label: { ru: "Успех ({n})", en: "Success ({n})" }, placeholder: "Сброшено устройств: {n}" },
        { kind: "text", key: "resetErrorText", label: { ru: "Ошибка", en: "Error" }, placeholder: "Не удалось сбросить устройства" },
        { kind: "boolean", key: "showReset", label: { ru: "Показывать кнопку сброса", en: "Show reset button" }, default: true },
      ],
    },
    {
      title: { ru: "Список и докупка", en: "List & add-on" },
      fields: [
        { kind: "text", key: "addonLabel", label: { ru: "Кнопка «Докупить устройства»", en: "«Buy more devices» button" }, placeholder: "Докупить устройства" },
        { kind: "number", key: "pageSize", label: { ru: "Устройств на странице", en: "Devices per page" }, min: 1, max: 50, default: 4 },
      ],
    },
    {
      title: { ru: "Экран докупки устройств", en: "Add-on screen" },
      description: { ru: "Тексты экрана, который открывается по кнопке «Докупить устройства».", en: "Texts of the screen opened by the «Buy more devices» button." },
      fields: [
        { kind: "text", key: "addonsHint", label: { ru: "Подзаголовок", en: "Subtitle" }, placeholder: "Оставь пустым, чтобы не показывать" },
        { kind: "text", key: "addonsDevicesLabel", label: { ru: "Заголовок списка", en: "Options label" }, placeholder: "Устройства" },
        { kind: "text", key: "addonsDeviceUnitFormat", label: { ru: "Вариант ({n})", en: "Option ({n})" }, placeholder: "{n} устр." },
        { kind: "text", key: "addonsDeviceUnlimitedText", label: { ru: "Безлимит-вариант", en: "Unlimited option" }, placeholder: "∞ устр." },
        { kind: "text", key: "addonsUnavailableText", label: { ru: "Опций нет", en: "No options" }, placeholder: "Для текущего тарифа нет доступных опций", multiline: true },
        { kind: "text", key: "addonsTotalLabel", label: { ru: "Итого", en: "Total" }, placeholder: "Итого к оплате" },
        { kind: "text", key: "addonsSubmitLabel", label: { ru: "Кнопка оплаты", en: "Submit button" }, placeholder: "Оформить" },
        { kind: "text", key: "addonsSubmitBusyLabel", label: { ru: "Оплата в процессе", en: "Submitting" }, placeholder: "..." },
        { kind: "text", key: "addonsCancelLabel", label: { ru: "Кнопка отмены", en: "Cancel button" }, placeholder: "Отмена" },
        { kind: "select", key: "monoFontFamily", label: { ru: "Шрифт (пусто = из темы)", en: "Font (empty = from theme)" }, source: "fonts", allowEmpty: true, emptyLabel: { ru: "Из темы", en: "From theme" }, allowCustom: true, customPlaceholder: { ru: "из темы / JetBrains Mono", en: "из темы / JetBrains Mono" } },
      ],
    },
  ],
  defaultFaq: [
    {
      title: { ru: "Раскладка", en: "Layout" },
      fields: [
        { kind: "number", key: "gap", label: { ru: "Отступ между вопросами (px)", en: "Gap between items (px)" }, min: 0, max: 40, step: 1, default: 14 },
      ],
    },
    {
      title: { ru: "Вопросы и ответы", en: "FAQ" },
      description: { ru: "Аккордеон: один открытый пункт за раз.", en: "Accordion: one open item at a time." },
      tab: "content",
      fields: [
        {
          kind: "list",
          key: "items",
          label: { ru: "Вопросы", en: "Questions" },
          addLabel: { ru: "Вопрос", en: "Question" },
          newItem: { title: "", content: "" },
          item: [
            { kind: "text", key: "title", label: { ru: "Вопрос", en: "Question" }, placeholder: "Что будет после пробного периода?" },
            { kind: "text", key: "content", label: { ru: "Ответ", en: "Answer" }, placeholder: "Ничего. Подписка не продлевается автоматически.", multiline: true },
          ],
        },
      ],
    },
  ],
  defaultHero: [
    {
      title: { ru: "Раскладка", en: "Layout" },
      fields: [
        { kind: "select", key: "align", label: { ru: "Выравнивание", en: "Alignment" }, options: [
          { value: "left", label: { ru: "По левому краю", en: "Left" } },
          { value: "center", label: { ru: "По центру", en: "Center" } },
        ] },
      ],
    },
    {
      title: "Hero",
      description: { ru: "Главный экран лендинга: тексты и кнопки.", en: "Landing hero: texts and buttons." },
      tab: "content",
      fields: [
        { kind: "text", key: "pillText", label: { ru: "Бейдж", en: "Pill" }, placeholder: "Без логов · Без ограничений" },
        { kind: "text", key: "title", label: { ru: "Заголовок", en: "Title" }, placeholder: "Быстрый и безопасный интернет" },
        { kind: "text", key: "titleAccent", label: { ru: "Акцентное слово", en: "Accent word" }, placeholder: "без границ" },
        { kind: "text", key: "subtitle", label: { ru: "Подзаголовок", en: "Subtitle" }, placeholder: "Подключение за пару минут…" },
        { kind: "text", key: "trustText", label: { ru: "Строка доверия", en: "Trust line" }, placeholder: "Пробный период — бесплатно" },
      ],
    },
    {
      title: { ru: "Кнопки", en: "Buttons" },
      description: { ru: "Главная кнопка запускает flow (по умолчанию trial). Очистите flow, чтобы использовать обычную ссылку.", en: "Primary button starts a flow (trial by default). Clear the flow to use a plain link." },
      tab: "content",
      fields: [
        { kind: "text", key: "primaryLabel", label: { ru: "Текст главной кнопки", en: "Primary label" }, placeholder: "Попробовать бесплатно" },
        { kind: "select", key: "primaryFlowId", label: { ru: "Flow главной кнопки", en: "Primary flow id" }, source: "flows", allowEmpty: true, emptyLabel: { ru: "Без сценария", en: "No flow" }, allowCustom: true, customPlaceholder: { ru: "trial", en: "trial" } },
        { kind: "text", key: "primaryLink", label: { ru: "Ссылка (если без flow)", en: "Link (fallback)" }, placeholder: "/login" },
        { kind: "text", key: "secondaryLabel", label: { ru: "Текст вторичной кнопки", en: "Secondary label" }, placeholder: "Смотреть тарифы" },
        { kind: "text", key: "secondaryLink", label: { ru: "Ссылка вторичной (якорь)", en: "Secondary link (anchor)" }, placeholder: "#Тарифы" },
        { kind: "widget", widget: "buttonAction", prefix: "primary" },
      ],
    },
  ],
  defaultLoginBanner: [
    {
      title: { ru: "Баннер входа", en: "Login banner" },
      description: { ru: "Левая панель страницы входа: приветствие и преимущества.", en: "Left panel of the login page: welcome and benefits." },
      tab: "content",
      fields: [
        { kind: "text", key: "logoText", label: { ru: "Логотип (текст)", en: "Logo text" }, placeholder: "SoloNet" },
        { kind: "text", key: "title", label: { ru: "Заголовок", en: "Title" }, placeholder: "Рады видеть вас" },
        { kind: "text", key: "subtitle", label: { ru: "Подзаголовок", en: "Subtitle" }, placeholder: "Войдите, чтобы управлять подписками…" },
        { kind: "text", key: "footnote", label: { ru: "Подпись внизу", en: "Footnote" }, placeholder: "Подключение занимает пару минут" },
        {
          kind: "list",
          key: "bullets",
          label: { ru: "Преимущества", en: "Benefits" },
          addLabel: { ru: "Пункт", en: "Item" },
          newItem: { title: "", desc: "" },
          item: [
            { kind: "text", key: "title", label: { ru: "Заголовок", en: "Title" }, placeholder: "Без логов" },
            { kind: "text", key: "desc", label: { ru: "Описание", en: "Description" }, placeholder: "Мы не храним историю подключений" },
          ],
        },
      ],
    },
  ],
  defaultNodeStatus: [
    {
      title: { ru: "Шапка и подписи", en: "Header and labels" },
      description: { ru: "Статусы тянутся из /api/web/node-status. Выключенные вручную ноды клиенту не показываются.", en: "Statuses from /api/web/node-status. Manually disabled nodes are hidden from clients." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "Статус серверов" },
        { kind: "text", key: "hint", label: { ru: "Подсказка", en: "Hint" } },
        { kind: "text", key: "totalFormat", label: { ru: "Сводка ({total}/{online}/{offline})", en: "Summary ({total}/{online}/{offline})" }, placeholder: "Всего {total} · онлайн {online} · недоступно {offline}" },
        { kind: "text", key: "onlineLabel", label: { ru: "Статус «работает»", en: "Online label" }, placeholder: "Работает" },
        { kind: "text", key: "offlineLabel", label: { ru: "Статус «не работает»", en: "Offline label" }, placeholder: "Не работает" },
        { kind: "text", key: "checkingLabel", label: { ru: "«Проверка…»", en: "Checking label" }, placeholder: "Проверка…" },
        { kind: "text", key: "pingFormat", label: { ru: "Формат пинга ({ms})", en: "Ping format ({ms})" }, placeholder: "≈ {ms} мс" },
        { kind: "text", key: "fastestFormat", label: { ru: "Быстрейший ({name}/{ms})", en: "Fastest ({name}/{ms})" }, placeholder: "⚡ Самый быстрый: {name} · ≈ {ms} мс" },
        { kind: "text", key: "recommendFormat", label: { ru: "Рекомендация по нагрузке ({name})", en: "Recommend by load ({name})" }, placeholder: "🚀 Рекомендуем: {name} (меньше всего нагрузка)" },
        { kind: "text", key: "prevLabel", label: { ru: "Кнопка «назад»", en: "Prev label" }, placeholder: "Назад" },
        { kind: "text", key: "nextLabel", label: { ru: "Кнопка «вперёд»", en: "Next label" }, placeholder: "Вперёд" },
        { kind: "text", key: "emptyText", label: { ru: "Пусто", en: "Empty" } },
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" } },
        { kind: "number", key: "pageSize", label: { ru: "Серверов на странице", en: "Servers per page" }, min: 1, max: 30, default: 5 },
        { kind: "number", key: "probePort", label: { ru: "Резервный порт пробы", en: "Fallback probe port" }, min: 1, max: 65535, default: 443 },
      ],
    },
    {
      title: "Проверка доступности",
      tab: "interaction",
      fields: [
        { kind: "boolean", key: "probeEnabled", label: { ru: "Пинговать серверы из браузера", en: "Probe servers from the browser" }, default: true },
        { kind: "list", key: "items", label: { ru: "Названия серверов", en: "Server names" }, addLabel: { ru: "Сервер", en: "Server" }, item: [{ kind: "select", key: "uuid", label: { ru: "Сервер", en: "Node" }, source: "nodes" }, { kind: "text", key: "label", label: { ru: "Своё название", en: "Custom name" } }, { kind: "text", key: "description", label: { ru: "Описание", en: "Description" } }] },
      ],
    },
  ],
  defaultOrbit: [
    {
      title: { ru: "Орбита серверов", en: "Server orbit" },
      description: { ru: "Анимированная орбита: центральный узел с пульсом и кольца с локациями.", en: "Animated orbit: pulsing center node and rings with locations." },
      tab: "content",
      fields: [
        { kind: "text", key: "nodeLabels", label: { ru: "Локации (через запятую)", en: "Locations (comma-separated)" }, placeholder: "AMS, FRA, NYC, LON, TYO, SGP" },
        { kind: "text", key: "centerLabel", label: { ru: "Подпись под центром", en: "Center caption" } },
        { kind: "number", key: "spinSeconds", label: { ru: "Оборот, сек", en: "Rotation, sec" }, min: 8, max: 180, default: 46 },
        { kind: "boolean", key: "showPulse", label: { ru: "Пульс центра", en: "Center pulse" }, default: true },
      ],
    },
  ],
  defaultPayoutHistory: [
    {
      title: { ru: "История выводов", en: "Payout history" },
      description: { ru: "Заголовок, состояния списка и подписи статусов заявок.", en: "Header, list states and payout status labels." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "История выводов" },
        { kind: "text", key: "panelHeaderHintFmt", label: { ru: "Хинт ({count})", en: "Hint ({count})" }, placeholder: "всего {count}" },
        { kind: "text", key: "emptyText", label: { ru: "Пустая история", en: "Empty history" }, placeholder: "Заявок пока нет" },
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" }, placeholder: "Загрузка..." },
        { kind: "text", key: "statusPendingLabel", label: { ru: "Статус «в обработке»", en: "Pending" }, placeholder: "В обработке" },
        { kind: "text", key: "statusApprovedLabel", label: { ru: "Статус «одобрено»", en: "Approved" }, placeholder: "Одобрено" },
        { kind: "text", key: "statusRejectedLabel", label: { ru: "Статус «отклонено»", en: "Rejected" }, placeholder: "Отклонено" },
        { kind: "text", key: "statusPaidLabel", label: { ru: "Статус «выплачено»", en: "Paid" }, placeholder: "Выплачено" },
      ],
    },
    {
      title: { ru: "Страницы", en: "Pages" },
      description: { ru: "Сколько заявок на страницу и подписи листалки.", en: "Payouts per page and pager labels." },
      fields: [
        { kind: "text", key: "pagePrevLabel", label: { ru: "Кнопка «Назад»", en: "Prev button" }, placeholder: "Назад" },
        { kind: "text", key: "pageNextLabel", label: { ru: "Кнопка «Вперёд»", en: "Next button" }, placeholder: "Вперёд" },
        { kind: "text", key: "pageLabelFormat", label: { ru: "Счётчик ({page}/{total})", en: "Counter ({page}/{total})" }, placeholder: "{page} / {total}" },
        { kind: "number", key: "pageSize", label: { ru: "Заявок на странице", en: "Payouts per page" }, min: 1, max: 50, default: 4 },
        { kind: "number", key: "maxItems", label: { ru: "Сколько заявок загружать", en: "Payouts to load" }, min: 1, max: 100, default: 20 },
        { kind: "select", key: "monoFontFamily", label: { ru: "Шрифт (пусто = из темы)", en: "Font (empty = from theme)" }, source: "fonts", allowEmpty: true, emptyLabel: { ru: "Из темы", en: "From theme" }, allowCustom: true, customPlaceholder: { ru: "из темы / JetBrains Mono", en: "из темы / JetBrains Mono" } },
      ],
    },
  ],
  defaultPayoutRequest: [
    {
      title: { ru: "Проверки", en: "Validation" },
      fields: [
        { kind: "text", key: "requisitesRequiredText", label: { ru: "Реквизиты не заполнены", en: "Requisites missing" }, placeholder: "Укажите реквизиты" },
      ],
    },
    {
      title: { ru: "Запрос на вывод", en: "Payout request" },
      description: { ru: "Заголовок, поле суммы и сообщения формы.", en: "Header, amount field and form messages." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "Запрос на вывод" },
        { kind: "text", key: "panelHeaderHintFmt", label: { ru: "Хинт ({balance})", en: "Hint ({balance})" }, placeholder: "баланс {balance} ₽" },
        { kind: "text", key: "amountPlaceholder", label: { ru: "Подсказка поля суммы", en: "Amount placeholder" }, placeholder: "Сумма к выводу" },
        { kind: "text", key: "submitLabel", label: { ru: "Кнопка", en: "Submit label" }, placeholder: "Запросить" },
        { kind: "text", key: "submittingLabel", label: { ru: "Кнопка во время отправки", en: "Submitting label" }, placeholder: "Отправляем..." },
        { kind: "text", key: "successText", label: { ru: "Сообщение успеха", en: "Success text" }, placeholder: "✓ Заявка создана" },
        { kind: "text", key: "errorText", label: { ru: "Общая ошибка", en: "Error text" }, placeholder: "✕ Не удалось создать заявку" },
        { kind: "text", key: "notEnoughBalanceText", label: { ru: "Недостаточно средств", en: "Not enough balance" }, placeholder: "На балансе недостаточно средств" },
        { kind: "text", key: "belowMinText", label: { ru: "Ниже минимума ({min})", en: "Below minimum ({min})" }, placeholder: "Минимальная сумма {min} ₽" },
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" }, placeholder: "Загрузка..." },
        { kind: "number", key: "minAmount", label: { ru: "Минимальная сумма вывода", en: "Minimum payout" }, min: 1, max: 1000000, default: 100 },
      ],
    },
    {
      title: { ru: "Способ вывода", en: "Payout method" },
      description: { ru: "Тексты настройки реквизитов: выбор способа, сохранение, изменение.", en: "Requisites setup texts: method choice, saving, changing." },
      fields: [
        { kind: "text", key: "setupHint", label: { ru: "Подсказка про настройку", en: "Setup hint" }, placeholder: "Чтобы выводить средства, укажите способ вывода и реквизиты.", multiline: true },
        { kind: "text", key: "setupCtaLabel", label: { ru: "Кнопка настройки", en: "Setup button" }, placeholder: "Установить способ вывода" },
        { kind: "text", key: "noMethodsText", label: { ru: "Способы не настроены", en: "No methods" }, placeholder: "Способы вывода не настроены администратором.", multiline: true },
        { kind: "text", key: "methodSelectLabel", label: { ru: "Подпись выбора способа", en: "Method select label" }, placeholder: "Способ вывода" },
        { kind: "text", key: "requisitesLabel", label: { ru: "Подпись реквизитов", en: "Requisites label" }, placeholder: "Реквизиты" },
        { kind: "text", key: "saveMethodLabel", label: { ru: "Кнопка сохранения", en: "Save button" }, placeholder: "Сохранить" },
        { kind: "text", key: "savingMethodLabel", label: { ru: "Сохранение в процессе", en: "Saving" }, placeholder: "Сохраняем..." },
        { kind: "text", key: "cancelLabel", label: { ru: "Кнопка «Отмена»", en: "Cancel button" }, placeholder: "Отмена" },
        { kind: "text", key: "changeMethodLabel", label: { ru: "Кнопка «изменить»", en: "Change button" }, placeholder: "изменить" },
        { kind: "text", key: "methodSavedFmt", label: { ru: "Сохранённый способ", en: "Saved method" }, placeholder: "{method} · {masked}" },
        { kind: "select", key: "monoFontFamily", label: { ru: "Шрифт (пусто = из темы)", en: "Font (empty = from theme)" }, source: "fonts", allowEmpty: true, emptyLabel: { ru: "Из темы", en: "From theme" }, allowCustom: true, customPlaceholder: { ru: "из темы / JetBrains Mono", en: "из темы / JetBrains Mono" } },
        { kind: "text", key: "amountLabel", label: { ru: "Подпись суммы", en: "Amount label" } },
      ],
    },
  ],
  defaultPushPermission: [
    {
      title: { ru: "Тексты", en: "Texts" },
      description: { ru: "Заголовок, описание и подписи статусов запроса push-уведомлений.", en: "Header, description and status labels for the push permission request." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "// Уведомления" },
        { kind: "text", key: "hint", label: { ru: "Hint", en: "Hint" }, placeholder: "push" },
        { kind: "text", key: "description", label: { ru: "Описание", en: "Description" }, placeholder: "Разреши push-уведомления…", multiline: true },
        { kind: "text", key: "enableLabel", label: { ru: "Кнопка разрешения", en: "Enable button" }, placeholder: "РАЗРЕШИТЬ" },
        { kind: "text", key: "grantedLabel", label: { ru: "Разрешено", en: "Granted" }, placeholder: "УВЕДОМЛЕНИЯ РАЗРЕШЕНЫ" },
        { kind: "text", key: "deniedLabel", label: { ru: "Заблокировано", en: "Denied" }, placeholder: "Уведомления заблокированы в настройках браузера", multiline: true },
        { kind: "text", key: "unsupportedLabel", label: { ru: "Не поддерживается", en: "Unsupported" }, placeholder: "Уведомления не поддерживаются в этом браузере", multiline: true },
        { kind: "text", key: "subscribeErrorLabel", label: { ru: "Ошибка подписки", en: "Subscribe error" }, placeholder: "Разрешение получено, но подписку не удалось сохранить…", multiline: true },
        { kind: "text", key: "iosInstallFirstLabel", label: { ru: "Подсказка для iOS", en: "iOS hint" }, placeholder: "На iPhone сначала установи приложение…", multiline: true },
        { kind: "text", key: "telegramLabel", label: { ru: "Подсказка в Telegram", en: "Telegram hint" }, placeholder: "Push доступны только в приложении с сайта…", multiline: true },
        { kind: "select", key: "monoFontFamily", label: { ru: "Шрифт (пусто = из темы)", en: "Font (empty = from theme)" }, source: "fonts", allowEmpty: true, emptyLabel: { ru: "Из темы", en: "From theme" }, allowCustom: true, customPlaceholder: { ru: "из темы / JetBrains Mono", en: "из темы / JetBrains Mono" } },
      ],
    },
  ],
  defaultPwaInstall: [
    {
      title: { ru: "Тексты", en: "Texts" },
      description: { ru: "Заголовок, описание и подписи кнопки установки приложения.", en: "Header, description and labels for the app install button." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "// Приложение" },
        { kind: "text", key: "hint", label: { ru: "Hint", en: "Hint" }, placeholder: "pwa" },
        { kind: "text", key: "description", label: { ru: "Описание", en: "Description" }, placeholder: "Установи кабинет как приложение…", multiline: true },
        { kind: "text", key: "installLabel", label: { ru: "Кнопка установки", en: "Install button" }, placeholder: "УСТАНОВИТЬ" },
        { kind: "text", key: "openInBrowserLabel", label: { ru: "Открыть в браузере", en: "Open in browser" }, placeholder: "ОТКРЫТЬ В БРАУЗЕРЕ" },
        { kind: "text", key: "installedLabel", label: { ru: "Уже установлено", en: "Installed" }, placeholder: "ПРИЛОЖЕНИЕ УСТАНОВЛЕНО" },
        { kind: "text", key: "unavailableLabel", label: { ru: "Установка недоступна", en: "Install unavailable" }, placeholder: "Установка недоступна в этом браузере" },
        { kind: "text", key: "iosInstructions", label: { ru: "Инструкция для iOS", en: "iOS instructions" }, placeholder: "Нажми «Поделиться» внизу Safari…", multiline: true },
        { kind: "text", key: "telegramHint", label: { ru: "Подсказка в Telegram", en: "Telegram hint" }, placeholder: "Открой кабинет во внешнем браузере…", multiline: true },
        { kind: "select", key: "monoFontFamily", label: { ru: "Шрифт (пусто = из темы)", en: "Font (empty = from theme)" }, source: "fonts", allowEmpty: true, emptyLabel: { ru: "Из темы", en: "From theme" }, allowCustom: true, customPlaceholder: { ru: "из темы / JetBrains Mono", en: "из темы / JetBrains Mono" } },
      ],
    },
  ],
  defaultQuickActions: [
    {
      title: { ru: "Шапка", en: "Header" },
      tab: "content",
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "Быстрые действия" },
        { kind: "text", key: "panelHeaderHint", label: { ru: "Подсказка справа", en: "Hint" }, placeholder: "Оставь пустым, чтобы не показывать" },
        { kind: "text", key: "emptyText", label: { ru: "Если карточек нет", en: "Empty state" }, placeholder: "Действия не настроены" },
        { kind: "number", key: "maxColumns", label: { ru: "Карточек в ряд", en: "Cards per row" }, min: 1, max: 4, default: 3 },
      ],
    },
    {
      title: { ru: "Карточки", en: "Cards" },
      description: { ru: "Карточка либо переключает вкладку кабинета, либо открывает ссылку — тип действия выбирается явно.", en: "A card either switches a cabinet tab or opens a link — the action type is explicit." },
      tab: "content",
      fields: [
        {
          kind: "list",
          key: "items",
          label: { ru: "Карточки", en: "Cards" },
          addLabel: { ru: "Карточка", en: "Card" },
          newItem: { icon: "✨", title: "Новое действие", subtitle: "", linkType: "tab", cabinetTabId: "keys" },
          item: [
            { kind: "text", key: "icon", label: { ru: "Иконка (emoji)", en: "Icon (emoji)" }, placeholder: "🔑" },
            { kind: "text", key: "title", label: { ru: "Название", en: "Title" }, placeholder: "Моя подписка" },
            { kind: "text", key: "subtitle", label: { ru: "Описание", en: "Subtitle" }, placeholder: "Тариф, продление, ключ" },
            { kind: "select", key: "linkType", label: { ru: "Действие при клике", en: "Click action" }, options: [{ value: "tab", label: { ru: "Переключить вкладку кабинета", en: "Switch cabinet tab" } }, { value: "url", label: { ru: "Открыть ссылку", en: "Open link" } }], default: "tab" },
            { kind: "text", key: "href", label: { ru: "Ссылка (URL / deep-link)", en: "Link (URL / deep-link)" }, placeholder: "/tariffs, https://..., tg://...", when: { key: "linkType", equals: "url" } },
            { kind: "widget", widget: "cabinetTarget", when: { key: "linkType", equals: "tab" } },
          ],
        },
      ],
    },
  ],
  defaultSteps: [
    {
      title: { ru: "Раскладка", en: "Layout" },
      fields: [
        { kind: "number", key: "padding", label: { ru: "Внутренний отступ (px)", en: "Padding (px)" }, min: 0, max: 80, step: 1 },
        { kind: "number", key: "gap", label: { ru: "Отступ между шагами (px)", en: "Gap (px)" }, min: 0, max: 60, step: 1 },
      ],
    },
    {
      title: { ru: "Шаги", en: "Steps" },
      description: { ru: "Карточки «как подключиться». Колонки делятся равномерно.", en: "How-to-connect cards. Columns split evenly." },
      tab: "content",
      fields: [
        { kind: "boolean", key: "sideAnimation", label: { ru: "Анимация сбоку", en: "Side animation" } },
        {
          kind: "list",
          key: "items",
          label: { ru: "Шаги", en: "Steps" },
          addLabel: { ru: "Шаг", en: "Step" },
          newItem: { num: "", title: "", desc: "" },
          item: [
            { kind: "text", key: "num", label: { ru: "Номер", en: "Num" }, placeholder: "01" },
            { kind: "text", key: "title", label: { ru: "Заголовок", en: "Title" }, placeholder: "Выберите тариф" },
            { kind: "text", key: "desc", label: { ru: "Описание", en: "Description" }, placeholder: "Пробный период бесплатно…" },
          ],
        },
      ],
    },
  ],
  defaultThemeMode: [
    {
      title: { ru: "Тексты", en: "Texts" },
      description: { ru: "Заголовок и подписи режимов темы. Клиент сам выбирает режим — выбор хранится у него.", en: "Header and labels for theme mode options. The client picks the mode — saved on their device." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "Тема" },
        { kind: "text", key: "hint", label: { ru: "Hint", en: "Hint" }, placeholder: "вид" },
        { kind: "text", key: "lightLabel", label: { ru: "Светлая", en: "Light" }, placeholder: "Светлая" },
        { kind: "text", key: "darkLabel", label: { ru: "Тёмная", en: "Dark" }, placeholder: "Тёмная" },
        { kind: "text", key: "autoLabel", label: { ru: "Системная", en: "System" }, placeholder: "Системная" },
        { kind: "select", key: "monoFontFamily", label: { ru: "Шрифт (пусто = из темы)", en: "Font (empty = from theme)" }, source: "fonts", allowEmpty: true, emptyLabel: { ru: "Из темы", en: "From theme" }, allowCustom: true, customPlaceholder: { ru: "из темы / JetBrains Mono", en: "из темы / JetBrains Mono" } },
      ],
    },
  ],
  defaultTrafficTrend: [
    {
      title: { ru: "Тексты", en: "Texts" },
      description: { ru: "Заголовок графика, подписи осей и состояния.", en: "Chart header, axis labels and states." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "Использование трафика" },
        { kind: "text", key: "hint", label: { ru: "Hint", en: "Hint" }, placeholder: "traffic" },
        { kind: "text", key: "unitLabel", label: { ru: "Единица измерения", en: "Unit" }, placeholder: "ГБ" },
        { kind: "text", key: "peakLabel", label: { ru: "Пик", en: "Peak" }, placeholder: "Пик" },
        { kind: "text", key: "lastLabel", label: { ru: "Текущее значение", en: "Current value" }, placeholder: "Сейчас" },
        { kind: "text", key: "emptyText", label: { ru: "Нет данных", en: "No data" }, placeholder: "Данные появятся в течение нескольких дней." },
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" }, placeholder: "Загрузка..." },
        { kind: "select", key: "monoFontFamily", label: { ru: "Шрифт (пусто = из темы)", en: "Font (empty = from theme)" }, source: "fonts", allowEmpty: true, emptyLabel: { ru: "Из темы", en: "From theme" }, allowCustom: true, customPlaceholder: { ru: "из темы / JetBrains Mono", en: "из темы / JetBrains Mono" } },
      ],
    },
  ],
  defaultTrafficUsage: [
    {
      title: { ru: "Докупка трафика", en: "Traffic add-ons" },
      fields: [
        { kind: "text", key: "addonsTrafficLabel", label: { ru: "Подпись объёма", en: "Volume label" }, placeholder: "Трафик" },
        { kind: "text", key: "addonsTrafficUnitFormat", label: { ru: "Формат единиц", en: "Unit format" }, placeholder: "{value} ГБ" },
        { kind: "text", key: "addonsTrafficUnlimitedText", label: { ru: "Безлимит", en: "Unlimited" }, placeholder: "Безлимит" },
        { kind: "text", key: "addonsTotalLabel", label: { ru: "Подпись итога", en: "Total label" }, placeholder: "Итого" },
        { kind: "text", key: "addonsSubmitLabel", label: { ru: "Кнопка покупки", en: "Submit button" }, placeholder: "Докупить" },
        { kind: "text", key: "addonsCancelLabel", label: { ru: "Кнопка отмены", en: "Cancel button" }, placeholder: "Отмена" },
        { kind: "text", key: "addonsHint", label: { ru: "Подсказка", en: "Hint" }, multiline: true, placeholder: "Трафик добавится к текущей подписке" },
        { kind: "text", key: "addonsUnavailableText", label: { ru: "Докупка недоступна", en: "Add-ons unavailable" }, placeholder: "Докупка недоступна" },
      ],
    },
    {
      title: { ru: "Тексты", en: "Texts" },
      description: { ru: "Заголовок и форматы расхода трафика.", en: "Header and traffic usage formats." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "Трафик" },
        { kind: "text", key: "hint", label: { ru: "Hint", en: "Hint" }, placeholder: "usage" },
        { kind: "text", key: "usedFormat", label: { ru: "Израсходовано", en: "Used" }, placeholder: "{used} / {limit} ГБ" },
        { kind: "text", key: "remainingFormat", label: { ru: "Остаток", en: "Remaining" }, placeholder: "Осталось {left} ГБ" },
        { kind: "text", key: "unlimitedText", label: { ru: "Безлимит", en: "Unlimited" }, placeholder: "Безлимитный трафик" },
        { kind: "text", key: "unlimitedHint", label: { ru: "Знак безлимита", en: "Unlimited mark" }, placeholder: "∞" },
        { kind: "text", key: "noKeysText", label: { ru: "Нет подписки", en: "No subscription" }, placeholder: "Нет активной подписки" },
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" }, placeholder: "Загрузка..." },
        { kind: "text", key: "addonLabel", label: { ru: "Кнопка «Докупить трафик»", en: "«Buy more traffic» button" }, placeholder: "Докупить трафик" },
      ],
    },
    {
      title: { ru: "Предупреждение о расходе", en: "Usage warning" },
      description: { ru: "С какого процента расхода блок подсвечивает остаток.", en: "Usage percentage at which the block highlights the remainder." },
      fields: [
        { kind: "number", key: "lowWarnPercent", label: { ru: "Процент предупреждения", en: "Warning percent" }, min: 0, max: 100, default: 90 },
        { kind: "select", key: "monoFontFamily", label: { ru: "Шрифт (пусто = из темы)", en: "Font (empty = from theme)" }, source: "fonts", allowEmpty: true, emptyLabel: { ru: "Из темы", en: "From theme" }, allowCustom: true, customPlaceholder: { ru: "из темы / JetBrains Mono", en: "из темы / JetBrains Mono" } },
      ],
    },
  ],
  defaultUsageRings: [
    {
      title: { ru: "Кольца", en: "Rings" },
      description: { ru: "Подписи вокруг колец трафика и устройств.", en: "Labels around the traffic and device rings." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "Трафик и устройства" },
        { kind: "text", key: "trafficLabel", label: { ru: "Кольцо трафика", en: "Traffic ring" }, placeholder: "Трафик" },
        { kind: "text", key: "trafficUnitFormat", label: { ru: "Подпись лимита", en: "Limit caption" }, placeholder: "из {limit} ГБ" },
        { kind: "text", key: "trafficUnlimitedText", label: { ru: "Безлимит трафика", en: "Unlimited traffic" }, placeholder: "Безлимит" },
        { kind: "text", key: "unitGb", label: { ru: "Единица измерения", en: "Unit" }, placeholder: "ГБ" },
        { kind: "text", key: "devicesLabel", label: { ru: "Кольцо устройств", en: "Devices ring" }, placeholder: "Устройства" },
        { kind: "text", key: "devicesSubLabel", label: { ru: "Подпись устройств", en: "Devices caption" }, placeholder: "подключено" },
        { kind: "text", key: "devicesUnlimitedText", label: { ru: "Безлимит устройств", en: "Unlimited devices" }, placeholder: "∞" },
      ],
    },
    {
      title: { ru: "Список устройств", en: "Devices list" },
      description: { ru: "Список под кольцами и отвязка устройства.", en: "The list under the rings and device unbinding." },
      fields: [
        { kind: "text", key: "devicesListTitle", label: { ru: "Заголовок списка", en: "List header" }, placeholder: "Подключённые устройства" },
        { kind: "text", key: "emptyDevicesText", label: { ru: "Список пуст", en: "Empty list" }, placeholder: "Нет подключённых устройств" },
        { kind: "text", key: "deleteLabel", label: { ru: "Кнопка отвязки", en: "Unbind button" }, placeholder: "Отвязать" },
        { kind: "text", key: "deleteBusyLabel", label: { ru: "В процессе", en: "In progress" }, placeholder: "…" },
        { kind: "text", key: "deleteSuccessText", label: { ru: "Успех", en: "Success" }, placeholder: "Устройство отвязано" },
        { kind: "text", key: "deleteErrorText", label: { ru: "Ошибка", en: "Error" }, placeholder: "Не удалось отвязать устройство" },
        { kind: "number", key: "pageSize", label: { ru: "Устройств на странице", en: "Devices per page" }, min: 1, max: 50, default: 3 },
      ],
    },
    {
      title: { ru: "Состояния и докупка", en: "States & add-ons" },
      fields: [
        { kind: "text", key: "noKeysText", label: { ru: "Нет подписки", en: "No subscription" }, placeholder: "Нет активной подписки" },
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" }, placeholder: "Загрузка..." },
        { kind: "text", key: "addonTrafficLabel", label: { ru: "Докупить трафик", en: "Buy more traffic" }, placeholder: "Докупить трафик" },
        { kind: "text", key: "addonDevicesLabel", label: { ru: "Докупить устройства", en: "Buy more devices" }, placeholder: "Докупить устройства" },
        { kind: "number", key: "lowWarnPercent", label: { ru: "Процент предупреждения", en: "Warning percent" }, min: 0, max: 100, default: 90 },
        { kind: "select", key: "monoFontFamily", label: { ru: "Шрифт (пусто = из темы)", en: "Font (empty = from theme)" }, source: "fonts", allowEmpty: true, emptyLabel: { ru: "Из темы", en: "From theme" }, allowCustom: true, customPlaceholder: { ru: "из темы / JetBrains Mono", en: "из темы / JetBrains Mono" } },
      ],
    },
  ],
};
