"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { useDefaultTheme, DefaultPanel, btnSolid, btnSecondary, f, parseBlockData, useIsMobile } from ".";
import { useIdentityMe } from "@/lib/useIdentityMe";
import { AnalyticsPanel } from "@/app/(marketing)/landing/AnalyticsPanel";
import { LogsPanel } from "@/app/(marketing)/landing/LogsPanel";
import { AuditPanel } from "@/app/(marketing)/landing/AuditPanel";

const SCHEMA = {
  panelHeader: f.str("Администратор"),
  hint: f.str("Видно только администраторам сайта"),
  adminPanelLabel: f.str("Веб-админка"),
  adminPanelHref: f.href("/admin"),
  analyticsLabel: f.str("Аналитика"),
  logsLabel: f.str("Логи и здоровье"),
  auditLabel: f.str("Журнал действий"),
};

export function DefaultAdminToolsBlockView({ block, context }: TypedBlockViewProps<"defaultAdminTools">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, SCHEMA);
  const isPreview = previewMode === true;

  const { data: me } = useIdentityMe();
  const admin = me?.is_admin === true;
  const [open, setOpen] = useState<"analytics" | "logs" | "audit" | null>(null);

  const isMobile = useIsMobile();
  const [toolbarless, setToolbarless] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const update = () => setToolbarless(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);

  if (!isPreview && (!admin || !toolbarless)) return null;

  return wrap(
    <DefaultPanel t={t} header={cfg.panelHeader}>
      <div style={{ display: "flex", flexDirection: "column", gap: t.space.md }}>
        <span style={{ fontSize: t.font.xs, color: t.inkMute }}>{cfg.hint}</span>
        <div style={{ display: "flex", flexDirection: isMobile ? "column" : "row", gap: t.space.sm, flexWrap: "wrap" }}>
          {cfg.adminPanelLabel ? (
            <a
              href={isPreview ? undefined : cfg.adminPanelHref}
              style={{ ...btnSolid(t), flex: "1 1 0", minWidth: isMobile ? 0 : 140, justifyContent: "center", textDecoration: "none", cursor: "pointer" }}
            >
              {cfg.adminPanelLabel}
            </a>
          ) : null}
          <button
            type="button"
            onClick={() => !isPreview && setOpen("analytics")}
            style={{ ...btnSecondary(t), flex: "1 1 0", minWidth: isMobile ? 0 : 140, justifyContent: "center", cursor: "pointer" }}
          >
            {cfg.analyticsLabel}
          </button>
          <button
            type="button"
            onClick={() => !isPreview && setOpen("logs")}
            style={{ ...btnSecondary(t), flex: "1 1 0", minWidth: isMobile ? 0 : 140, justifyContent: "center", cursor: "pointer" }}
          >
            {cfg.logsLabel}
          </button>
          <button
            type="button"
            onClick={() => !isPreview && setOpen("audit")}
            style={{ ...btnSecondary(t), flex: "1 1 0", minWidth: isMobile ? 0 : 140, justifyContent: "center", cursor: "pointer" }}
          >
            {cfg.auditLabel}
          </button>
        </div>
      </div>
      {!isPreview && open === "analytics" ? createPortal(<AnalyticsPanel onClose={() => setOpen(null)} />, document.body) : null}
      {!isPreview && open === "logs" ? createPortal(<LogsPanel onClose={() => setOpen(null)} />, document.body) : null}
      {!isPreview && open === "audit" ? createPortal(<AuditPanel onClose={() => setOpen(null)} />, document.body) : null}
    </DefaultPanel>,
    false,
    true,
  );
}
