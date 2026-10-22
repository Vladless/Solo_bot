const host = (globalThis as {
  __SOLO_PACK_HOST__?: { react: typeof import("react"); jsxRuntime?: typeof import("react/jsx-runtime") };
}).__SOLO_PACK_HOST__;

/** Заглушка типа фрагмента: настоящий React приходит из хоста только в рантайме. */
export const Fragment = Symbol.for("solo.pack.fragment") as unknown as import("react").ExoticComponent<{ children?: unknown }>;

/**
 * Фабрика элементов набора. Идёт через jsx-runtime хоста — тот же путь, которым собрано само
 * приложение: статические дети помечаются проверенными, и React не требует у них ключей.
 */
export function h(type: unknown, props?: Record<string, unknown> | null, ...children: unknown[]): unknown {
  const react = host!.react;
  const resolved = type === (Fragment as unknown) ? react.Fragment : type;
  const runtime = host!.jsxRuntime;
  const { key, ...rest } = (props ?? {}) as Record<string, unknown>;
  if (!runtime) return react.createElement(resolved as never, props as never, ...(children as never[]));
  const merged =
    children.length === 0 ? rest : { ...rest, children: children.length === 1 ? children[0] : children };
  return children.length > 1
    ? runtime.jsxs(resolved as never, merged as never, key as never)
    : runtime.jsx(resolved as never, merged as never, key as never);
}

/** Набор кладёт свои блоки в общий реестр: одна и та же обвязка у всех наборов. */
export function registerPack(packId: string, factory: () => unknown[]): void {
  const registry = ((globalThis as { __SOLO_PACK_BUNDLES__?: Record<string, unknown> }).__SOLO_PACK_BUNDLES__ ??= {});
  registry[packId] = () => {
    if (!host) throw new Error(`[${packId}] хост приложения недоступен`);
    return factory();
  };
}
