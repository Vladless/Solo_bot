import { registerPack } from "../../_shared/packRuntime";
import { CABINET_DEFAULT_BLOCKS } from "./blocks";
import { DEFAULT_EXTRA_BLOCKS } from "./blocks/extras";
import { DEFAULT_PACK_FORMS } from "./blocks/forms";

const PACK_ID = "default";

registerPack(PACK_ID, () =>
  [...CABINET_DEFAULT_BLOCKS, ...DEFAULT_EXTRA_BLOCKS].map((def) => {
    const form = DEFAULT_PACK_FORMS[def.type];
    return form ? { ...def, form } : def;
  }),
);
