import type { PackForm } from "@/components/constructor/packForms";

/** Формы блоков «Стандарта»: поддержка. */
export const DEFAULT_SUPPORT_FORMS: Record<string, PackForm> = {
  defaultSupport: [
    {
      title: { ru: "Шапка", en: "Header" },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "Поддержка" },
        { kind: "text", key: "panelSubheader", label: { ru: "Подпись", en: "Subheader" }, placeholder: "Задайте вопрос — ответим как можно скорее" },
        { kind: "text", key: "newButtonLabel", label: { ru: "Кнопка нового обращения", en: "New ticket button" }, placeholder: "Новое обращение" },
        { kind: "text", key: "backLabel", label: { ru: "Возврат к списку", en: "Back to list" }, placeholder: "← К списку" },
      ],
    },
    {
      title: { ru: "Форма обращения", en: "Ticket form" },
      fields: [
        { kind: "text", key: "createTitle", label: { ru: "Заголовок формы", en: "Form title" }, placeholder: "Новое обращение" },
        { kind: "text", key: "subjectLabel", label: { ru: "Подпись темы", en: "Subject label" }, placeholder: "Тема" },
        { kind: "text", key: "subjectPlaceholder", label: { ru: "Подсказка темы", en: "Subject placeholder" }, placeholder: "Коротко о проблеме" },
        { kind: "text", key: "categoryLabel", label: { ru: "Подпись категории", en: "Category label" }, placeholder: "Категория" },
        { kind: "text", key: "messageLabel", label: { ru: "Подпись сообщения", en: "Message label" }, placeholder: "Сообщение" },
        { kind: "text", key: "messagePlaceholder", label: { ru: "Подсказка сообщения", en: "Message placeholder" }, multiline: true, placeholder: "Опишите, что случилось" },
        { kind: "text", key: "sendLabel", label: { ru: "Кнопка отправки", en: "Send button" }, placeholder: "Отправить" },
        { kind: "text", key: "replyPlaceholder", label: { ru: "Подсказка ответа", en: "Reply placeholder" }, placeholder: "Ваш ответ…" },
      ],
    },
    {
      title: { ru: "Состояния", en: "States" },
      fields: [
        { kind: "text", key: "emptyText", label: { ru: "Обращений нет", en: "No tickets" }, placeholder: "Обращений пока нет" },
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" }, placeholder: "Загрузка…" },
        { kind: "text", key: "unauthenticatedText", label: { ru: "Гость", en: "Guest" }, placeholder: "Войдите в аккаунт" },
        { kind: "text", key: "disabledText", label: { ru: "Поддержка выключена", en: "Support disabled" }, placeholder: "Поддержка временно недоступна" },
        { kind: "text", key: "closedText", label: { ru: "Обращение закрыто", en: "Ticket closed" }, placeholder: "Обращение закрыто" },
        { kind: "number", key: "maxItems", label: { ru: "Сколько обращений показывать", en: "Tickets shown" }, min: 1, max: 100, step: 1 },
      ],
    },
  ],
  defaultNotifChannels: [
    {
      title: { ru: "Шапка", en: "Header" },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "// Каналы доставки" },
        { kind: "text", key: "panelHeaderHint", label: { ru: "Хинт", en: "Hint" }, placeholder: "куда присылать" },
      ],
    },
    {
      title: { ru: "Каналы", en: "Channels" },
      description: { ru: "Email/Telegram значения берутся из /api/auth/summary.", en: "Email/Telegram values come from API." },
      fields: [
        { kind: "list", key: "channels", label: { ru: "Каналы", en: "Channels" }, addLabel: { ru: "Канал", en: "Channel" }, newItem: { kind: "static", name: "Канал", staticValue: "", defaultOn: false }, item: [{ kind: "select", key: "kind", label: { ru: "Тип", en: "Kind" }, default: "static", options: [{ value: "email", label: { ru: "E-mail (api)", en: "Email (api)" } }, { value: "telegram", label: { ru: "Telegram (api)", en: "Telegram (api)" } }, { value: "push", label: { ru: "Push (статика)", en: "Push (static)" } }, { value: "sms", label: { ru: "SMS (статика)", en: "SMS (static)" } }, { value: "static", label: { ru: "Свой канал", en: "Custom" } }] }, { kind: "text", key: "name", label: { ru: "Название", en: "Name" } }, { kind: "text", key: "staticValue", label: { ru: "Подпись (значение)", en: "Static value" } }, { kind: "boolean", key: "defaultOn", label: { ru: "Включён по умолчанию", en: "On by default" } }] },
      ],
    },
    {
      title: "Значение «не привязан»",
      fields: [
        { kind: "text", key: "notLinkedText", label: { ru: "Значение «не привязан»", en: "Not linked value" }, placeholder: "не привязан" },
        { kind: "text", key: "saveErrorText", label: { ru: "Ошибка сохранения", en: "Save error" }, placeholder: "Не удалось сохранить" },
      ],
    },
  ],
  defaultNotifFeed: [
    {
      title: { ru: "Шапка и фильтры", en: "Header and filters" },
      description: { ru: "Тянет уведомления из /api/notifications.", en: "Reads /api/notifications." },
      fields: [
        { kind: "text", key: "panelHeader", label: { ru: "Заголовок", en: "Header" }, placeholder: "// Лента событий" },
        { kind: "text", key: "filterAllFormat", label: { ru: "Фильтр «Все» ({count})", en: "All filter ({count})" }, placeholder: "Все · {count}" },
        { kind: "text", key: "filterUnreadFormat", label: { ru: "Фильтр «Новые» ({count})", en: "Unread filter" }, placeholder: "Новые · {count}" },
        { kind: "text", key: "filterImportantFormat", label: { ru: "Фильтр «Важные» ({count})", en: "Important filter" }, placeholder: "Важные · {count}" },
        { kind: "number", key: "maxItems", label: { ru: "Максимум", en: "Max items" }, min: 1, max: 200, default: 30 },
      ],
    },
    {
      title: { ru: "Кнопка «прочитать всё»", en: "«Mark all read» button" },
      fields: [
        { kind: "text", key: "markAllLabel", label: { ru: "Текст кнопки", en: "Label" }, placeholder: "Пометить как прочитанные" },
        { kind: "text", key: "markAllBusyLabel", label: { ru: "Текст во время отправки", en: "Busy label" }, placeholder: "..." },
        { kind: "text", key: "markAllSuccessText", label: { ru: "Сообщение успеха", en: "Success" }, placeholder: "Прочитано" },
        { kind: "text", key: "markAllErrorText", label: { ru: "Сообщение ошибки", en: "Error" }, placeholder: "Не удалось" },
      ],
    },
    {
      title: { ru: "Действия с уведомлением", en: "Notification actions" },
      description: { ru: "Клик по строке = развернуть и прочитать. Кнопка × или свайп влево = удалить.", en: "Click row = expand and mark read. × or swipe left = delete." },
      fields: [
        { kind: "text", key: "deleteLabel", label: { ru: "Текст «удалить»", en: "Delete label" }, placeholder: "Удалить" },
        { kind: "text", key: "openLabel", label: { ru: "Подсказка кнопки перехода", en: "Open button hint" }, placeholder: "Перейти" },
        { kind: "text", key: "expandLabel", label: { ru: "Текст «подробнее»", en: "Expand label" }, placeholder: "Подробнее" },
        { kind: "text", key: "collapseLabel", label: { ru: "Текст «свернуть»", en: "Collapse label" }, placeholder: "Свернуть" },
      ],
    },
    {
      title: { ru: "Состояния", en: "States" },
      fields: [
        { kind: "text", key: "emptyText", label: { ru: "Пусто", en: "Empty" } },
        { kind: "text", key: "loadingText", label: { ru: "Загрузка", en: "Loading" } },
        { kind: "text", key: "unauthenticatedText", label: { ru: "Не авторизован", en: "Not authed" } },
      ],
    },
    {
      title: "Очистка уведомлений",
      fields: [
        { kind: "text", key: "clearAllLabel", label: { ru: "Кнопка «Удалить все»", en: "«Clear all» button" }, placeholder: "Удалить все" },
        { kind: "text", key: "clearAllConfirmLabel", label: { ru: "Подтверждение", en: "Confirmation" }, placeholder: "Точно?" },
      ],
    },
  ],
};
