import type { ElementDefinition, ElementVisualDefinition } from "@/components/constructor/elementDefinitions";
import { PANEL_PRESENTATION_CAPABILITY } from "@/components/constructor/presentation/elementCapabilityCatalog";
import { createLazyView } from "@/components/constructor/blockContent/lazyViews";

const DefaultSidebarBlockView = createLazyView(() => import("../blockContent/DefaultSidebarBlockView").then(m => ({ default: m.DefaultSidebarBlockView })));
const DefaultTopbarBlockView = createLazyView(() => import("../blockContent/DefaultTopbarBlockView").then(m => ({ default: m.DefaultTopbarBlockView })));
const DefaultPageHeaderBlockView = createLazyView(() => import("../blockContent/DefaultPageHeaderBlockView").then(m => ({ default: m.DefaultPageHeaderBlockView })));
const DefaultBackdropBlockView = createLazyView(() => import("../blockContent/DefaultBackdropBlockView").then(m => ({ default: m.DefaultBackdropBlockView })));
const DefaultStatRowBlockView = createLazyView(() => import("../blockContent/DefaultStatRowBlockView").then(m => ({ default: m.DefaultStatRowBlockView })));

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

/** Каркас страницы кабинета. */
export const DEFAULT_CHROME_BLOCKS: ElementDefinition[] = [
  {
    type: "defaultSidebar",
    roles: ["cabinetNav", "cabinetSidebar", "noMobileStream"],
    label: "Стандарт: сайдбар",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      vs_radius: "none",
      colSpan: 6,
      rowSpan: 24,
      monoFontFamily: MONO_FONT,
      animationType: "slideEdgeLeft",
      animationTrigger: "load",
      animationDuration: 420,
      brandName: "",
      brandVersion: "",
      showConnCard: true,
      connTunnelLabel: "",
      connTunnelValue: "WG-01",
      connStatusConnectedLabel: "Подключено",
      connStatusDisconnectedLabel: "Отключено",
      connLocLabel: "AMS-03 · Амстердам · 14 ms",
      tabGroupId: "cabinet",
      items: [
        { id: "profile", num: "01", label: "Профиль", icon: "prof" },
        { id: "keys", num: "02", label: "Подписки", icon: "bill" },
        { id: "partners", num: "03", label: "Партнёрка", icon: "ref" },
        { id: "gifts", num: "04", label: "Подарки", icon: "gift", badgeKey: "giftsClaimed" },
        { id: "notifications", num: "05", label: "Уведомления", icon: "bell", badgeKey: "unreadNotifications" },
      ],
      showUserFooter: true,
      userPlanFormat: "PRIME · 247 дн",
      logoutLabel: "↗",
    },
    view: DefaultSidebarBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: PANEL_VISUAL,
    preview: { colSpan: 5, rowSpan: 12, data: {} },
    insert: [
      {
        key: "defaultSidebar",
        category: "cabinet",
        label: "Стандарт: сайдбар",
        description: "Левая панель: бренд + статус соединения + табы разделов + блок пользователя.",
      },
    ],
  },
  {
    type: "defaultTopbar",
    roles: ["selfScrolling"],
    label: "Стандарт: шапка",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 18,
      rowSpan: 2,
      monoFontFamily: MONO_FONT,
      searchPlaceholder: "Поиск по кабинету…",
      showSearch: true,
      actions: [
        { label: "Помощь", variant: "ghost", href: "/dashboard/instructions" },
        { label: "Купить трафик", variant: "red", href: "/tariffs" },
      ],
    },
    view: DefaultTopbarBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: PANEL_VISUAL,
    preview: { colSpan: 18, rowSpan: 2, data: {} },
    insert: [
      {
        key: "defaultTopbar",
        category: "cabinet",
        label: "Стандарт: шапка",
        description: "Crumbs + поиск + кнопки помощи и покупки трафика. Подхватывает активный таб.",
      },
    ],
  },
  {
    type: "defaultPageHeader",
    label: "Стандарт: заголовок раздела",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 18,
      rowSpan: 3,
      monoFontFamily: MONO_FONT,
      source: "auto",
      title: "Заголовок",
      subtitle: "",
      meta: "",
    },
    view: DefaultPageHeaderBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: PANEL_VISUAL,
    preview: { colSpan: 12, rowSpan: 3, data: {} },
    insert: [
      {
        key: "defaultPageHeader",
        category: "cabinet",
        label: "Стандарт: заголовок раздела",
        description: "Eyebrow + большой uppercase-заголовок раздела (автоматически из URL) + meta справа.",
        cabinetTabHints: ["profile", "keys", "instructions", "referrals", "partners", "gifts", "notifications"],
      },
    ],
  },
  {
    type: "defaultBackdrop",
    label: "Стандарт: подложка лендинга",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 24,
      rowSpan: 14,
      monoFontFamily: MONO_FONT,
      statusLeft: "Онлайн",
      statusCenter: "Защищённое соединение",
      statusRight: "v4.2",
      showStatusBar: true,
      showCanvas: true,
      showLiveTime: false,
      cols: 18,
      rows: 9,
      hotRatio: 0.04,
    },
    view: DefaultBackdropBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: PANEL_VISUAL,
    preview: { colSpan: 24, rowSpan: 8, data: {} },
    insert: [
      {
        key: "defaultBackdrop",
        category: "landing",
        label: "Стандарт: подложка лендинга",
        description: "Мягкая анимированная подложка из узлов и линий в цветах темы, со строкой статуса сверху. Цвета наследуются от темы.",
      },
    ],
  },
  {
    type: "defaultStatRow",
    label: "Стандарт: полоса метрик",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 18,
      rowSpan: 4,
      monoFontFamily: MONO_FONT,
      borderWidth: 1,
      padding: 20,
      gap: 24,
      items: [
        { label: "Скорость", value: "10", suffix: "Гбит/с" },
        { label: "Серверов", value: "412" },
        { label: "Логов", value: "0" },
      ],
    },
    view: DefaultStatRowBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: PANEL_VISUAL,
    preview: { colSpan: 18, rowSpan: 4, data: {} },
    insert: [
      {
        key: "defaultStatRow",
        category: "landing",
        label: "Стандарт: полоса метрик",
        description: "Скруглённый контейнер с N тайлами метрик. По умолчанию 3: скорость / серверов / логов. Добавляй тайлы через массив items.",
      },
    ],
  },
];
