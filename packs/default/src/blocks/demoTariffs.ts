import type { TariffItem } from "../blockContent/tariffGridTypes";

/** Витринные тарифы «Стандарта»: их же показывает блок, пока тарифы не заведены. */
export const DEFAULT_DEMO_TARIFFS: TariffItem[] = [
  {
    code: "Старт", name: "PROBE",
    desc: "Для разовых сессий и теста скорости.",
    price: "99", currency: "₽", period: "/ 1 месяц",
    features: [
      { label: "Серверы", value: "40+" },
      { label: "Устройств", value: "2" },
      { label: "Скорость", value: "до 1 Гбит/с" },
      { label: "Поддержка", value: "Email · 48ч" },
    ],
    ctaLabel: "Начать",
  },
  {
    code: "База", name: "CORE", badge: "Базовый",
    desc: "Базовая защита для нескольких устройств.",
    price: "249", currency: "₽", period: "/ 6 месяцев",
    features: [
      { label: "Серверы", value: "180+" },
      { label: "Устройств", value: "5" },
      { label: "Скорость", value: "до 5 Гбит/с" },
      { label: "Killswitch", value: "Да" },
      { label: "Поддержка", value: "Email · 24ч" },
    ],
    ctaLabel: "Подписаться",
  },
  {
    code: "Хит", name: "PRIME", badge: "Популярный", highlighted: true,
    desc: "Самый ходовой. Multi-hop и приоритет в очереди.",
    price: "399", currency: "₽", period: "/ 12 месяцев",
    features: [
      { label: "Серверы", value: "412" },
      { label: "Устройств", value: "10" },
      { label: "Скорость", value: "до 10 Гбит/с" },
      { label: "Multi-hop", value: "2 узла" },
      { label: "Killswitch", value: "Да" },
      { label: "Поддержка", value: "24/7" },
    ],
    ctaLabel: "Выбрать PRIME",
  },
  {
    code: "Макс", name: "SHADOW",
    desc: "Triple-hop, выделенный IP, обфускация трафика.",
    price: "899", currency: "₽", period: "/ 12 месяцев",
    features: [
      { label: "Серверы", value: "412 + выделенный" },
      { label: "Устройств", value: "∞" },
      { label: "Скорость", value: "до 10 Гбит/с" },
      { label: "Triple-hop", value: "Да" },
      { label: "Обфускация", value: "XOR + TLS" },
      { label: "Выделенный IP", value: "Да" },
      { label: "Поддержка", value: "24/7" },
    ],
    ctaLabel: "Получить SHADOW",
  },
];
