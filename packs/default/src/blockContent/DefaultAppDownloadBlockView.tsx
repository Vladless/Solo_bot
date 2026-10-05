"use client";

import { useMemo } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, DefaultPanel, btnSolid, btnGhost, f, parseBlockData, useIsMobile, CONTROL_H, CONTROL_R } from ".";
import { DefaultAppList, DefaultChipPicker, DefaultStageBar, DefaultStepList } from "./DefaultAppInstall";
import { DEFAULT_APP_CATALOG, DEFAULT_APP_STEPS, parseApps, parseSteps, platformLabels, type AppItem, type AppStep } from "@/components/constructor/blockContent/appCatalog";
import { useAppInstallFlow } from "@/components/constructor/blockContent/useAppInstallFlow";

const SCHEMA = {
  viewMode: f.enum(["steps", "all"] as const, "steps"),
  panelHeader: f.str("Установка приложения"),
  hint: f.str(""),
  platformTitle: f.str("Выберите устройство"),
  clientTitle: f.str("Выберите приложение"),
  stepsTitle: f.str("Что делать"),
  downloadLabel: f.str("Скачать"),
  installLabel: f.str("Установить {app}"),
  backLabel: f.str("Назад"),
  restartLabel: f.str("Выбрать другое приложение"),
  counterFormat: f.str("Шаг {n} из {total}"),
  noClientsText: f.str("Для этой платформы приложений пока нет"),
  noStepsText: f.str("Инструкция появится после выбора приложения"),
  labelIos: f.str("iPhone / iPad"),
  labelAndroid: f.str("Android"),
  labelWindows: f.str("Windows"),
  labelMacos: f.str("macOS"),
  labelLinux: f.str("Linux"),
  showSteps: f.bool(true),
  showDownload: f.bool(true),
  apps: f.array<AppItem>(DEFAULT_APP_CATALOG),
  steps: f.array<AppStep>(DEFAULT_APP_STEPS),
};

export function DefaultAppDownloadBlockView({ block, context }: TypedBlockViewProps<"defaultAppDownload">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const isMobile = useIsMobile();
  const cfg = parseBlockData(d, SCHEMA);
  const isPreview = previewMode === true;
  const stepper = cfg.viewMode === "steps";

  const apps = useMemo(() => parseApps(cfg.apps), [cfg.apps]);
  const steps = useMemo(() => parseSteps(cfg.steps), [cfg.steps]);
  const labels = useMemo(() => platformLabels(d), [d]);

  const flow = useAppInstallFlow({
    apps,
    steps,
    labels,
    stepper,
    previewMode: isPreview,
    autoSelectFirst: true,
    counterFormat: cfg.counterFormat,
  });

  const installButton = () => {
    if (!cfg.showDownload || !flow.downloadUrl || !flow.selected) return null;
    return (
      <a
        href={isPreview ? undefined : flow.downloadUrl}
        target="_blank"
        rel="noreferrer"
        style={{
          ...btnSolid(t),
          minHeight: t.px(CONTROL_H),
          borderRadius: CONTROL_R,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          padding: `0 ${t.space.xl}px`,
          textDecoration: "none",
          width: isMobile ? "100%" : undefined,
        }}
      >
        {cfg.installLabel.replace("{app}", flow.selected.name)}
      </a>
    );
  };

  const stepsSection = () => {
    if (!cfg.showSteps) return null;
    if (flow.steps.length === 0) {
      return cfg.noStepsText ? (
        <div style={{ fontSize: t.font.smPlus, color: t.inkMute }}>{cfg.noStepsText}</div>
      ) : null;
    }
    return <DefaultStepList t={t} steps={flow.steps} />;
  };

  const sectionTitle = (title: string) =>
    title ? <div style={{ fontSize: t.font.xs, color: t.inkDim }}>{title}</div> : null;

  const appList = () => (
    <DefaultAppList
      t={t}
      apps={flow.available}
      platform={flow.platform}
      selectedId={flow.selected?.id ?? ""}
      onSelect={(app) => flow.pickApp(app.id)}
      downloadLabel={cfg.downloadLabel}
      emptyText={cfg.noClientsText}
      linksDisabled={isPreview}
    />
  );

  const body = () => {
    if (!stepper) {
      return (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: t.space.sm }}>
            {sectionTitle(cfg.platformTitle)}
            <DefaultChipPicker t={t} options={flow.platformOptions} value={flow.platform} onChange={flow.pickPlatform} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: t.space.sm }}>
            {sectionTitle(cfg.clientTitle)}
            {appList()}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: t.space.sm }}>
            {sectionTitle(cfg.stepsTitle)}
            {stepsSection()}
          </div>
        </>
      );
    }

    if (flow.stage === "platform") {
      return (
        <>
          <DefaultStageBar t={t} title={cfg.platformTitle} counter={flow.counter("platform")} backLabel="" onBack={null} />
          <DefaultChipPicker t={t} options={flow.platformOptions} value={flow.platform} onChange={flow.pickPlatform} />
        </>
      );
    }

    if (flow.stage === "app") {
      return (
        <>
          <DefaultStageBar
            t={t}
            title={cfg.clientTitle}
            counter={flow.counter("app")}
            backLabel={cfg.backLabel}
            onBack={() => flow.goTo("platform")}
          />
          {appList()}
        </>
      );
    }

    return (
      <>
        <DefaultStageBar
          t={t}
          title={cfg.stepsTitle}
          counter={flow.counter("final")}
          backLabel={cfg.backLabel}
          onBack={() => flow.goTo("app")}
        />
        {installButton()}
        {stepsSection()}
        {cfg.restartLabel ? (
          <button
            type="button"
            onClick={flow.resetApp}
            style={{ ...btnGhost(t), borderRadius: CONTROL_R, color: t.inkDim, alignSelf: "flex-start" }}
          >
            {cfg.restartLabel}
          </button>
        ) : null}
      </>
    );
  };

  return wrap(
    <DefaultPanel t={t} header={cfg.panelHeader} hint={cfg.hint || undefined} bodyGap={t.space.lg}>
      {body()}
    </DefaultPanel>,
    false,
    true,
  );
}
