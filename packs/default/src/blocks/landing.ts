import type { ElementDefinition, ElementVisualDefinition } from "@/components/constructor/elementDefinitions";
import { PANEL_PRESENTATION_CAPABILITY } from "@/components/constructor/presentation/elementCapabilityCatalog";
import { createLazyView } from "@/components/constructor/blockContent/lazyViews";

const DefaultFeaturesGridBlockView = createLazyView(() => import("../blockContent/DefaultFeaturesGridBlockView").then(m => ({ default: m.DefaultFeaturesGridBlockView })));
const DefaultTariffsGridBlockView = createLazyView(() => import("../blockContent/DefaultTariffsGridBlockView").then(m => ({ default: m.DefaultTariffsGridBlockView })));
const DefaultHeroBlockView = createLazyView(() => import("../blockContent/DefaultHeroBlockView").then(m => ({ default: m.DefaultHeroBlockView })));
const DefaultStepsBlockView = createLazyView(() => import("../blockContent/DefaultStepsBlockView").then(m => ({ default: m.DefaultStepsBlockView })));
const DefaultFaqBlockView = createLazyView(() => import("../blockContent/DefaultFaqBlockView").then(m => ({ default: m.DefaultFaqBlockView })));
const DefaultCtaBannerBlockView = createLazyView(() => import("../blockContent/DefaultCtaBannerBlockView").then(m => ({ default: m.DefaultCtaBannerBlockView })));
const DefaultOrbitBlockView = createLazyView(() => import("../blockContent/DefaultOrbitBlockView").then(m => ({ default: m.DefaultOrbitBlockView })));
const DefaultCheckoutTrustBlockView = createLazyView(() => import("../blockContent/DefaultCheckoutTrustBlockView").then(m => ({ default: m.DefaultCheckoutTrustBlockView })));
const DefaultCheckoutProviderBlockView = createLazyView(() => import("../blockContent/DefaultCheckoutProviderBlockView").then(m => ({ default: m.DefaultCheckoutProviderBlockView })));
const DefaultLoginBannerBlockView = createLazyView(() => import("../blockContent/DefaultLoginBannerBlockView").then(m => ({ default: m.DefaultLoginBannerBlockView })));

import { DEFAULT_DEMO_TARIFFS } from "./demoTariffs";

const BASE_GRID = { col: 0, row: 0, zIndex: 0, fontScale: 1 };
const PACK = "default";
const MONO_FONT = "";

/** Сетки карточек: обводка, свечение и тень ложатся на сами карточки, а не на полотно блока. */
const CARD_COLLECTION_VISUAL: ElementVisualDefinition = {
  textLike: false,
  cardCollection: true,
  autoTextScale: "none",
  hoverTarget: "control",
  outlineTarget: "card",
  glowTarget: "card",
  shadowTarget: "card",
  disableAutoHoverWhenUnconfigured: true,
  overflowVisibleInGrid: true,
};

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

/** Лендинг и оформление заказа. */
export const DEFAULT_LANDING_BLOCKS: ElementDefinition[] = [
  {
    type: "defaultFeaturesGrid",
    label: "Стандарт: фичи N×",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 24,
      rowSpan: 10,
      monoFontFamily: MONO_FONT,
      hoverEnabled: true,
      hoverTransitionMs: 200,
      padding: 28,
      iconSize: 56,
      iconStroke: 1.6,
      items: [
        {
          code: "Защита", title: "Шифрование",
          desc: "AES-256-GCM поверх WireGuard. Новые ключи при каждом подключении — приватность по умолчанию.",
          metricValue: "AES-256", metricLabel: "GCM / X25519", iconKey: "lock",
        },
        {
          code: "Без утечек", title: "Zero Leak",
          desc: "Killswitch на ядре. Утечки DNS, IPv6 и WebRTC заблокированы — упал туннель, встал и трафик.",
          metricValue: "0%", metricLabel: "утечек за 90 дней", iconKey: "shield",
        },
        {
          code: "Скорость", title: "Быстро",
          desc: "Магистрали 10G в каждом регионе. Multi-hop без потери пакетов, реальные 940+ Мбит/с.",
          metricValue: "940", metricLabel: "Мбит/с p50", iconKey: "chart",
        },
      ],
    },
    view: DefaultFeaturesGridBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: CARD_COLLECTION_VISUAL,
    preview: { colSpan: 24, rowSpan: 10, data: {} },
    insert: [
      {
        key: "defaultFeaturesGrid",
        category: "landing",
        label: "Стандарт: фичи N×",
        description: "Скруглённые карточки-фичи: метка, иконка (lock/shield/chart/...), заголовок, описание и метрика. Добавляй сколько нужно через массив items.",
      },
    ],
  },
  {
    type: "defaultTariffsGrid",
    label: "Стандарт: тарифы 2-5",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 24,
      rowSpan: 16,
      monoFontFamily: MONO_FONT,
      padding: 28,
      hoverEnabled: true,
      hoverTransitionMs: 200,
      checkoutSlug: "checkout",
      items: DEFAULT_DEMO_TARIFFS,
    },
    view: DefaultTariffsGridBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: CARD_COLLECTION_VISUAL,
    preview: { colSpan: 24, rowSpan: 16, data: {} },
    insert: [
      {
        key: "defaultTariffsGrid",
        category: "landing",
        label: "Стандарт: тарифы 2-5",
        description: "Скруглённые карточки тарифов (PROBE / CORE / PRIME / SHADOW). От 2 до 5 карточек. Поддерживает badge, выделенную карточку с акцентной рамкой, hover, привязку к tariff_id с живой ценой.",
      },
    ],
  },
  {
    type: "defaultHero",
    label: "Стандарт: hero лендинга",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 14,
      rowSpan: 10,
      monoFontFamily: MONO_FONT,
      pillText: "Без логов · Без ограничений",
      title: "Быстрый и безопасный интернет",
      titleAccent: "без границ",
      subtitle: "Подключение за пару минут на любом устройстве. Современные протоколы, стабильная скорость и поддержка, которая отвечает.",
      trustText: "Пробный период — бесплатно. Отмена в один клик.",
      primaryLabel: "Попробовать бесплатно",
      primaryActionType: "flow",
      primaryFlowId: "trial",
      secondaryLabel: "Смотреть тарифы",
      secondaryLink: "#Тарифы",
    },
    view: DefaultHeroBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: PANEL_VISUAL,
    preview: { colSpan: 14, rowSpan: 10, data: {} },
    insert: [
      {
        key: "defaultHero",
        category: "landing",
        label: "Стандарт: hero лендинга",
        description: "Главный экран: бейдж, крупный заголовок с акцентным словом, подзаголовок, основная и вторичная кнопки, строка доверия.",
      },
    ],
  },
  {
    type: "defaultSteps",
    label: "Стандарт: шаги подключения",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 20,
      rowSpan: 6,
      monoFontFamily: MONO_FONT,
      padding: 26,
      gap: 18,
      items: [
        { num: "01", title: "Выберите тариф", desc: "Пробный период бесплатно — карта не нужна." },
        { num: "02", title: "Получите ключ", desc: "Ключ доступа появится в личном кабинете сразу после оплаты." },
        { num: "03", title: "Подключитесь", desc: "Отсканируйте QR или вставьте ключ в приложение — готово." },
      ],
    },
    view: DefaultStepsBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: CARD_COLLECTION_VISUAL,
    preview: { colSpan: 20, rowSpan: 6, data: {} },
    insert: [
      {
        key: "defaultSteps",
        category: "landing",
        label: "Стандарт: шаги подключения",
        description: "Карточки-шаги «как подключиться»: номер в акцентном квадрате, заголовок и описание. Количество шагов настраивается через items.",
      },
    ],
  },
  {
    type: "defaultFaq",
    label: "Стандарт: FAQ-аккордеон",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 20,
      rowSpan: 8,
      monoFontFamily: MONO_FONT,
      gap: 12,
      items: [
        { title: "Что будет после пробного периода?", content: "Ничего. Подписка не продлевается автоматически — вы сами решаете, продолжать или нет." },
        { title: "На скольких устройствах работает?", content: "Зависит от тарифа. Дополнительные устройства можно докупить в любой момент." },
        { title: "Как быстро происходит подключение?", content: "Сразу после оплаты: ключ появляется в кабинете, подключение занимает пару минут." },
      ],
    },
    view: DefaultFaqBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: CARD_COLLECTION_VISUAL,
    preview: { colSpan: 20, rowSpan: 8, data: {} },
    insert: [
      {
        key: "defaultFaq",
        category: "landing",
        label: "Стандарт: FAQ-аккордеон",
        description: "Вопрос-ответ в мягких карточках: плавное раскрытие, плюс-индикатор, один открытый пункт за раз.",
      },
    ],
  },
  {
    type: "defaultCtaBanner",
    label: "Стандарт: финальный CTA",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 20,
      rowSpan: 5,
      monoFontFamily: MONO_FONT,
      title: "Готовы попробовать?",
      subtitle: "Пробный период бесплатно. Подключение за пару минут, отмена в один клик.",
      ctaLabel: "Начать бесплатно",
      noteText: "Карта не требуется",
      ctaActionType: "flow",
      ctaFlowId: "trial",
    },
    view: DefaultCtaBannerBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: PANEL_VISUAL,
    preview: { colSpan: 20, rowSpan: 5, data: {} },
    insert: [
      {
        key: "defaultCtaBanner",
        category: "landing",
        label: "Стандарт: финальный CTA",
        description: "Широкий акцентный баннер в конце лендинга: заголовок, подзаголовок и крупная кнопка действия.",
      },
    ],
  },
  {
    type: "defaultOrbit",
    roles: ["selfScrolling"],
    label: "Стандарт: орбита серверов",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 8,
      rowSpan: 8,
      monoFontFamily: MONO_FONT,
      centerLabel: "",
      nodeLabels: "AMS, FRA, NYC, LON, TYO, SGP",
      spinSeconds: 46,
      showPulse: true,
    },
    view: DefaultOrbitBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: PANEL_VISUAL,
    preview: { colSpan: 8, rowSpan: 8, data: {} },
    insert: [
      {
        key: "defaultOrbit",
        category: "landing",
        label: "Стандарт: орбита серверов",
        description: "Анимированная орбита: центральный узел с пульсом и вращающиеся кольца с локациями серверов. Визуальный акцент для hero-зоны.",
      },
    ],
  },
  {
    type: "defaultLoginBanner",
    label: "Стандарт: баннер входа",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 14,
      rowSpan: 14,
      monoFontFamily: MONO_FONT,
      logoText: "",
      title: "Рады видеть вас",
      subtitle: "Войдите, чтобы управлять подписками, ключами и устройствами.",
      footnote: "Подключение занимает пару минут",
      bullets: [
        { title: "Без логов", desc: "Мы не храним историю ваших подключений." },
        { title: "Любые устройства", desc: "Телефон, компьютер, роутер и даже телевизор." },
        { title: "Поддержка 24/7", desc: "Отвечаем быстро и по делу." },
      ],
    },
    view: DefaultLoginBannerBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: PANEL_VISUAL,
    preview: { colSpan: 14, rowSpan: 14, data: {} },
    insert: [
      {
        key: "defaultLoginBanner",
        category: "login",
        label: "Стандарт: баннер входа",
        description: "Левая панель страницы входа: логотип, приветствие, преимущества с галочками. Мягкая карточка с лёгким акцентным свечением.",
      },
    ],
  },
  {
    type: "defaultCheckoutTrust",
    label: "Стандарт: гарантии оплаты",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 20,
      rowSpan: 4,
      monoFontFamily: MONO_FONT,
      items: [
        { title: "Безопасная оплата", desc: "Платёж проходит по защищённому каналу, данные карты мы не видим и не храним." },
        { title: "Мгновенная активация", desc: "Подписка включается автоматически в течение минуты после оплаты." },
        { title: "Поддержка рядом", desc: "Если что-то пойдёт не так — напишите нам, разберёмся быстро." },
      ],
      methodsLabel: "Принимаем",
      methodsText: "Visa · Mastercard · МИР · СБП · USDT",
    },
    view: DefaultCheckoutTrustBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: PANEL_VISUAL,
    preview: { colSpan: 20, rowSpan: 4, data: {} },
    insert: [
      {
        key: "defaultCheckoutTrust",
        category: "checkout",
        label: "Стандарт: гарантии оплаты",
        description: "Полоса доверия для страницы оплаты: карточки гарантий и список принимаемых способов оплаты.",
      },
    ],
  },
  {
    type: "defaultCheckoutProvider",
    label: "Стандарт: способ оплаты",
    pack: PACK,
    defaultData: {
      ...BASE_GRID,
      colSpan: 14,
      rowSpan: 4,
      monoFontFamily: MONO_FONT,
      title: "Способ оплаты",
      subtitle: "",
      noProvidersText: "Нет доступных способов оплаты",
      defaultProvider: "",
      showSingleProvider: true,
      providerLabels: {},
    },
    view: DefaultCheckoutProviderBlockView,
    presentationCapability: PANEL_PRESENTATION_CAPABILITY,
    allowInModal: false,
    inspector: { showGenericHoverControls: false },
    visual: PANEL_VISUAL,
    preview: { colSpan: 14, rowSpan: 4, data: {} },
    insert: [
      {
        key: "defaultCheckoutProvider",
        category: "checkout",
        label: "Стандарт: способ оплаты",
        description: "Выбор платёжной кассы на странице оплаты. Список способов берётся из подключённых в админке касс автоматически.",
      },
    ],
  },
];
