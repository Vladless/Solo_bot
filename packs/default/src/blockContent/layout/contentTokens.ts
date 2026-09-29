/** Боковые поля тела блока — совпадают с полями шапки панели. */
export const PAD_X = 26;
/** Отступ тела от шапки и от низа блока. */
export const PAD_TOP = 8;
export const PAD_BOTTOM = 18;

/** Шаг между смысловыми группами внутри тела. */
export const GROUP_GAP = 12;

/** Ряд «поле → значение»: одинаковая высота у всех блоков пака. */
export const ROW_H = 44;
/** Ряд списка: иконка, две строки текста, значение справа. */
export const LIST_ROW_H = 56;
/** Кнопка внутри ряда — только такого размера, ряд от неё не растёт. */
export const CHIP_H = 32;

/** Значение длиннее этого числа символов не влезает в одну строку — ряд становится двухстрочным. */
export const LONG_VALUE_CHARS = 28;

/** Ниже этой ширины колонки действия и поля встают друг под друга. */
export const STACK_WIDTH = 320;

/** Карточка-опция: минимальная ширина колонки и минимальная высота ряда карточек. */
export const OPTION_CARD_MIN_WIDTH = 180;
export const OPTION_CARD_HEIGHT = 132;

/** Ниже этой ширины шапка кабинета переносит контент по строкам: поиск отдельно, кнопки отдельно. */
export const TOPBAR_SEARCH_MIN_WIDTH = 460;

/** Ниже этой ширины поиск в шапке убирается совсем: в строку влезают только иконки. */
export const TOPBAR_SEARCH_HIDE_WIDTH = 220;

/** Размер шапки кабинета, на котором её контролы имеют базовый размер. */
export const TOPBAR_DESIGN_WIDTH = 680;
export const TOPBAR_DESIGN_HEIGHT = 56;
/** Границы автомасштаба шапки: контент едет за нарисованной ячейкой, но не превращается в кашу. */
export const TOPBAR_SCALE_MIN = 0.7;
export const TOPBAR_SCALE_MAX = 1.8;

/**
 * Масштаб контента шапки по нарисованной ячейке: берём меньшее из «во сколько раз ячейка выше»
 * и «во сколько раз шире» базового размера. Нарисовал крупнее — контролы крупнее, уже — мельче.
 */
export function topbarAutoScale(width: number, height: number, fontScale = 1): number {
  if (height <= 0 && width <= 0) return 1;
  const font = fontScale > 0 ? fontScale : 1;
  const byHeight = height > 0 ? height / TOPBAR_DESIGN_HEIGHT : TOPBAR_SCALE_MAX;
  const byWidth = width > 0 ? width / TOPBAR_DESIGN_WIDTH : TOPBAR_SCALE_MAX;
  const raw = Math.min(byHeight, byWidth) / font;
  return Math.min(TOPBAR_SCALE_MAX, Math.max(TOPBAR_SCALE_MIN, Math.round(raw * 100) / 100));
}
