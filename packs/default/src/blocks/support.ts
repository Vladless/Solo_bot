import type { ElementDefinition, ElementVisualDefinition } from "@/components/constructor/elementDefinitions";
import { PANEL_PRESENTATION_CAPABILITY } from "@/components/constructor/presentation/elementCapabilityCatalog";
import { createLazyView } from "@/components/constructor/blockContent/lazyViews";

const DefaultNotifFeedBlockView = createLazyView(() => import("../blockContent/DefaultNotifFeedBlockView").then(m => ({ default: m.DefaultNotifFeedBlockView })));
const DefaultSupportBlockView = createLazyView(() => import("../blockContent/DefaultSupportBlockView").then(m => ({ default: m.DefaultSupportBlockView })));
const DefaultNotifChannelsBlockView = createLazyView(() => import("../blockContent/DefaultNotifChannelsBlockView").then(m => ({ default: m.DefaultNotifChannelsBlockView })));

const BASE_GRID = { col: 0, row: 0, zIndex: 0, fontScale: 1 };
const PACK = "default";
const MONO_FONT = "";

const PANEL_VISUAL: ElementVisualDefinition = {
  textLike: false,
  cardCollection: false,
  autoTextScale: "none",
  hoverTarget: "control",
  outlineTarget: "surface",
  glowTarget: "surface",
  shadowTarget: "surface",
  disableAutoHoverWhenUnconfigured: true,
  overflowVisibleInGrid: true,
};

/** Уведомления и поддержка. */
export const DEFAULT_SUPPORT_BLOCKS: ElementDefinition[] = [
  {
    type: "defaultNotifFeed",
    roles: ["selfScrolling"],
    label: "Стандарт: лента уведомлений",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 24,
      rowSpan: 8,
      monoFontFamily: MONO_FONT,
      panelHeader: "",
      filterAllFormat: "Все · {count}",
      filterUnreadFormat: "Новые · {count}",
      filterImportantFormat: "Важные · {count}",
      markAllLabel: "Пометить как прочитанные",
      markAllBusyLabel: "...",
      markAllSuccessText: "Прочитано",
      markAllErrorText: "Не удалось",
      emptyText: "Уведомлений нет",
      unauthenticatedText: "Войдите в аккаунт",
      loadingText: "Загрузка...",
      maxItems: 30,
      deleteLabel: "Удалить",
      expandLabel: "Подробнее",
      collapseLabel: "Свернуть",
    },
    view: DefaultNotifFeedBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: PANEL_VISUAL,
    preview: { colSpan: 20, rowSpan: 7, data: {} },
    insert: [
      {
        key: "defaultNotifFeed",
        category: "cabinet",
        label: "Стандарт: лента уведомлений",
        description: "Лента событий из /api/notifications с фильтром по статусу.",
      },
    ],
  },
  {
    type: "defaultSupport",
    roles: ["support"],
    label: "Стандарт: поддержка",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 24,
      rowSpan: 9,
      monoFontFamily: MONO_FONT,
      panelHeader: "Поддержка",
      panelSubheader: "Задайте вопрос — ответим как можно скорее",
      newButtonLabel: "Новое обращение",
      subjectLabel: "Тема",
      subjectPlaceholder: "Коротко о проблеме",
      categoryLabel: "Категория",
      messageLabel: "Сообщение",
      messagePlaceholder: "Опишите, что случилось и что вы уже пробовали",
      sendLabel: "Отправить",
      replyPlaceholder: "Ваш ответ…",
      backLabel: "← К списку",
      emptyText: "Обращений пока нет",
      loadingText: "Загрузка…",
      unauthenticatedText: "Войдите в аккаунт, чтобы обратиться в поддержку",
      disabledText: "Поддержка временно недоступна",
      createTitle: "Новое обращение",
      closedText: "Обращение закрыто",
      maxItems: 20,
    },
    view: DefaultSupportBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: PANEL_VISUAL,
    preview: { colSpan: 20, rowSpan: 8, data: {} },
    insert: [
      {
        key: "defaultSupport",
        category: "cabinet",
        label: "Стандарт: поддержка",
        description: "Обращения в поддержку: список, переписка, новое обращение.",
        cabinetTabHints: ["instructions"],
      },
    ],
  },
  {
    type: "defaultNotifChannels",
    label: "Стандарт: каналы уведомлений",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 14,
      rowSpan: 6,
      monoFontFamily: MONO_FONT,
      panelHeader: "Каналы доставки",
      panelHeaderHint: "куда присылать",
      channels: [
        { kind: "email", name: "E-mail", defaultOn: true },
        { kind: "telegram", name: "Telegram", defaultOn: true },
        { kind: "push", name: "Push в приложении", staticValue: "на устройства", defaultOn: true },
        { kind: "sms", name: "SMS", staticValue: "не подключено", defaultOn: false },
      ],
    },
    view: DefaultNotifChannelsBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: PANEL_VISUAL,
    preview: { colSpan: 12, rowSpan: 6, data: {} },
    insert: [
      {
        key: "defaultNotifChannels",
        category: "cabinet",
        label: "Стандарт: каналы уведомлений",
        description: "Тогглы каналов доставки уведомлений: email, Telegram, push, SMS.",
      },
    ],
  },
];
