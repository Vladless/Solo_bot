import defaults from "./defaults.json" with { type: "json" };

const APPS = defaults.defaultAppDownload.apps;

/** Сохраняет эталонную раскладку и обновляет приложения из дефолтов набора. */
export default {
  data: {
    "dashboard dashboard-keys dashboard-instructions": {
      defaultAppDownload: { apps: APPS },
      defaultConnectCta: { apps: APPS },
    },
  },
  pages: {},
};
