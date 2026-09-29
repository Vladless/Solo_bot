"use client";

import { useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import { usePagedItems, DefaultPagination } from "./DefaultPagination";
import { DefaultRows, ROW_H } from "./layout";
import {
  useDefaultTheme,
  btnSolid,
  btnSecondary,
  pillStyle,
  linkBtn,
  DefaultPanel,
  f,
  parseBlockData,
  useBlockApi,
  useLoadedFade,
  useIsMobile,
  actionsRow,
  actionsItem,
  actionsSolo,
  CONTROL_H,
  CONTROL_R,
} from ".";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import { isApiError } from "@/lib/api";
import {
  usePayoutHistory,
  usePayoutMethod,
  savePayoutMethod,
  payoutStatusKind,
  type PayoutEntry,
} from "@/components/constructor/blockContent/payouts/payoutData";
import { formatDateShort } from "@/lib/format-date";

const SCHEMA = {
  panelHeader: f.str("Заявки на вывод"),
  panelHeaderHintFmt: f.str("баланс {balance} ₽"),
  amountLabel: f.str("Сумма к выводу"),
  amountPlaceholder: f.str("0"),
  submitLabel: f.str("Запросить"),
  submittingLabel: f.str("Отправляем..."),
  successText: f.str("✓ Заявка создана"),
  errorText: f.str("✕ Не удалось создать заявку"),
  emptyText: f.str("Заявок пока нет"),
  loadingText: f.str("Загрузка..."),
  historyHeader: f.str("История"),
  statusPendingLabel: f.str("В обработке"),
  statusApprovedLabel: f.str("Одобрено"),
  statusRejectedLabel: f.str("Отклонено"),
  statusPaidLabel: f.str("Выплачено"),
  maxItems: f.num(20),
  pageSize: f.num(4),
  minAmount: f.num(100),
  notEnoughBalanceText: f.str("На балансе недостаточно средств"),
  belowMinText: f.str("Минимальная сумма {min} ₽"),
  setupHint: f.str("Чтобы выводить средства, укажите способ вывода и реквизиты."),
  setupCtaLabel: f.str("Установить способ вывода"),
  noMethodsText: f.str("Способы вывода не настроены администратором."),
  methodSelectLabel: f.str("Способ вывода"),
  requisitesLabel: f.str("Реквизиты"),
  saveMethodLabel: f.str("Сохранить"),
  savingMethodLabel: f.str("Сохраняем..."),
  cancelLabel: f.str("Отмена"),
  changeMethodLabel: f.str("изменить"),
  methodSavedFmt: f.str("{method} · {masked}"),
};


export function DefaultPartnerPayoutsBlockView({ block, context }: TypedBlockViewProps<"defaultPartnerPayouts">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const isMobile = useIsMobile();
  const cfg = parseBlockData(d, SCHEMA);

  const isPreview = previewMode === true;
  const api = useBlockApi({ needs: ["summary"], disabled: isPreview, mock: isPreview });
  const balance = api.summary.data?.partner_balance ?? 0;
  const headerHint = cfg.panelHeaderHintFmt.replace("{balance}", balance.toLocaleString("ru-RU"));

  const history = usePayoutHistory(isPreview, cfg.maxItems);
  const loadedFade = useLoadedFade(history.isLoading);
  const items: PayoutEntry[] = history.items;
  const paged = usePagedItems(items, cfg.pageSize);

  const [amount, setAmount] = useState("");
  const [submitBusy, setSubmitBusy] = useState(false);
  const [statusMsg, setStatusMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const mut = useAccountMutations();

  const method = usePayoutMethod(isPreview);
  const methodState = method.state;
  const methodLoaded = method.loaded;
  const configured = methodState.configured;
  const availableMethods = methodState.methods;

  const [setupOpen, setSetupOpen] = useState(false);
  const [setupMethod, setSetupMethod] = useState("");
  const [requisites, setRequisites] = useState("");
  const [setupBusy, setSetupBusy] = useState(false);
  const [setupError, setSetupError] = useState("");

  const openSetup = () => {
    setSetupError("");
    setRequisites("");
    setSetupMethod(methodState.method || availableMethods[0]?.key || "");
    setSetupOpen(true);
  };

  const saveMethod = async () => {
    if (isPreview || setupBusy) return;
    if (!setupMethod || !requisites.trim()) {
      setSetupError(cfg.requisitesLabel);
      return;
    }
    setSetupBusy(true);
    setSetupError("");
    try {
      await savePayoutMethod(setupMethod, requisites.trim());
      await method.refetch();
      setSetupOpen(false);
      setRequisites("");
    } catch (err) {
      setSetupError(isApiError(err) && err.message ? err.message : cfg.errorText);
    } finally {
      setSetupBusy(false);
    }
  };

  const selectedHint = availableMethods.find((m) => m.key === setupMethod)?.hint ?? "";

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isPreview || submitBusy) return;
    const num = Number(amount.replace(/\s+/g, "").replace(",", "."));
    if (!Number.isFinite(num) || num <= 0) {
      setStatusMsg({ kind: "err", text: cfg.belowMinText.replace("{min}", String(cfg.minAmount)) });
      return;
    }
    if (num < cfg.minAmount) {
      setStatusMsg({ kind: "err", text: cfg.belowMinText.replace("{min}", String(cfg.minAmount)) });
      return;
    }
    if (num > balance) {
      setStatusMsg({ kind: "err", text: cfg.notEnoughBalanceText });
      return;
    }
    setSubmitBusy(true);
    setStatusMsg(null);
    try {
      const res = await mut.requestPartnerPayout({ amount_rub: num });
      const text = res?.message?.trim() || cfg.successText;
      setStatusMsg({ kind: "ok", text });
      setAmount("");
      void history.refetch();
    } catch (err) {
      const msg = isApiError(err) && err.message ? err.message : cfg.errorText;
      setStatusMsg({ kind: "err", text: msg });
    } finally {
      setSubmitBusy(false);
    }
  };

  const statusColor = statusMsg?.kind === "ok" ? t.success : statusMsg?.kind === "err" ? t.error : t.inkDim;

  const renderStatusPill = (status: string) => {
    const kind = payoutStatusKind(status);
    const label =
      kind === "pending" ? cfg.statusPendingLabel :
      kind === "approved" ? cfg.statusApprovedLabel :
      kind === "rejected" ? cfg.statusRejectedLabel :
      kind === "paid" ? cfg.statusPaidLabel :
      status;
    const color =
      kind === "paid" ? t.success :
      kind === "approved" ? t.success :
      kind === "rejected" ? t.error :
      kind === "pending" ? t.warn :
      t.inkDim;
    return <span style={pillStyle(t, color)}>{label}</span>;
  };

  return wrap(
    <DefaultPanel
      t={t}
      header={cfg.panelHeader}
      hint={headerHint}
      noBodyPadding
    >
      {!methodLoaded ? (
        <div style={{ padding: "16px 26px", color: t.inkDim, fontSize: t.font.sm }}>{cfg.loadingText}</div>
      ) : setupOpen ? (
        <div style={{ padding: "8px 26px 18px", display: "flex", flexDirection: "column", gap: t.space.smPlus }}>
          {availableMethods.length === 0 ? (
            <div style={{ fontSize: t.font.sm, color: t.inkDim }}>{cfg.noMethodsText}</div>
          ) : (
            <>
              <div style={{ fontSize: t.font.sm, color: t.inkDim }}>{cfg.methodSelectLabel}</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: t.space.sm }}>
                {availableMethods.map((m) => {
                  const active = m.key === setupMethod;
                  return (
                    <button
                      key={m.key}
                      type="button"
                      onClick={() => { setSetupMethod(m.key); setSetupError(""); }}
                      style={{ ...(active ? btnSolid(t) : btnSecondary(t)), minHeight: 40, cursor: "pointer" }}
                    >
                      {m.label}
                    </button>
                  );
                })}
              </div>
              <div style={{ fontSize: t.font.sm, color: t.inkDim, marginTop: 4 }}>{cfg.requisitesLabel}</div>
              <input
                value={requisites}
                onChange={(e) => { setRequisites(e.target.value); if (setupError) setSetupError(""); }}
                placeholder={selectedHint}
                disabled={setupBusy}
                style={{
                  minHeight: t.px(CONTROL_H),
                  boxSizing: "border-box",
                  background: t.innerBg,
                  border: "none",
                  borderRadius: CONTROL_R,
                  color: t.ink,
                  fontFamily: t.monoFont,
                  padding: "0 16px",
                  outline: "none",
                  fontSize: t.font.smPlus,
                }}
              />
              {selectedHint ? <div style={{ fontSize: t.font.xs, color: t.inkMute }}>{selectedHint}</div> : null}
              <div style={{ display: "flex", gap: t.space.sm, marginTop: 4, ...actionsRow(isMobile) }}>
                <button type="button" onClick={() => void saveMethod()} disabled={setupBusy} style={{ ...btnSolid(t), minHeight: 44, flexShrink: 0, cursor: "pointer", ...actionsItem(isMobile) }}>
                  {setupBusy ? cfg.savingMethodLabel : cfg.saveMethodLabel}
                </button>
                {configured ? (
                  <button type="button" onClick={() => { setSetupOpen(false); setSetupError(""); }} disabled={setupBusy} style={{ ...btnSecondary(t), minHeight: 44, flexShrink: 0, cursor: "pointer", ...actionsItem(isMobile) }}>
                    {cfg.cancelLabel}
                  </button>
                ) : null}
              </div>
              {setupError ? <div style={{ fontSize: t.font.xs, color: t.error }}>{setupError}</div> : null}
            </>
          )}
        </div>
      ) : !configured ? (
        <div style={{ padding: "8px 26px 18px", display: "flex", flexDirection: "column", gap: t.space.smPlus, alignItems: "flex-start" }}>
          <div style={{ fontSize: t.font.sm, color: t.inkDim }}>
            {availableMethods.length === 0 ? cfg.noMethodsText : cfg.setupHint}
          </div>
          {availableMethods.length > 0 ? (
            <button type="button" onClick={openSetup} style={{ ...btnSolid(t), flexShrink: 0, cursor: "pointer", ...actionsSolo(isMobile) }}>
              {cfg.setupCtaLabel}
            </button>
          ) : null}
        </div>
      ) : (
        <>
          <form
            onSubmit={onSubmit}
            style={{
              padding: "12px 26px 6px",
              display: "flex",
              flexDirection: "column",
              gap: t.space.smPlus,
            }}
          >
            <div style={{ fontSize: t.font.sm, color: t.inkDim }}>
              {cfg.amountLabel}
            </div>
            <div style={{ display: "flex", gap: t.space.md, alignItems: "stretch" }}>
              <input
                value={amount}
                inputMode="numeric"
                onChange={(e) => {
                  const v = e.target.value.replace(/[^\d.,]/g, "");
                  setAmount(v);
                  if (statusMsg) setStatusMsg(null);
                }}
                placeholder={cfg.amountPlaceholder}
                disabled={submitBusy}
                style={{
                  flex: 1,
                  minWidth: 0,
                  minHeight: t.px(CONTROL_H),
                  boxSizing: "border-box",
                  background: t.innerBg,
                  border: "none",
                  borderRadius: CONTROL_R,
                  color: t.ink,
                  fontFamily: t.monoFont,
                  padding: "0 16px",
                  outline: "none",
                  fontSize: t.font.smPlus,
                }}
              />
              <button type="submit" disabled={submitBusy} style={{ ...btnSolid(t), flexShrink: 0 }}>
                {submitBusy ? cfg.submittingLabel : cfg.submitLabel}
              </button>
            </div>
            <div style={{ minHeight: t.space.lg, fontSize: t.font.xs, color: statusColor }}>
              {statusMsg?.text ?? " "}
            </div>
          </form>
          <div style={{ padding: "0 26px 16px", display: "flex", alignItems: "center", gap: t.space.sm, fontSize: t.font.xs, color: t.inkDim, flexWrap: "wrap" }}>
            <span>{cfg.methodSavedFmt.replace("{method}", methodState.method_label || "").replace("{masked}", methodState.masked || "")}</span>
            <button type="button" onClick={openSetup} style={{ ...linkBtn(t), cursor: "pointer" }}>{cfg.changeMethodLabel}</button>
          </div>
        </>
      )}

      <div style={{ padding: "6px 26px 4px", fontSize: t.font.smPlus, fontWeight: t.weight.bold, color: t.ink }}>
        {cfg.historyHeader}
      </div>
      {history.isLoading ? (
        <div style={{ padding: "8px 26px 18px", color: t.inkDim, fontSize: t.font.sm }}>
          {cfg.loadingText}
        </div>
      ) : items.length === 0 ? (
        <div className={loadedFade} style={{ padding: "8px 26px 18px", color: t.inkDim, fontSize: t.font.sm }}>
          {cfg.emptyText}
        </div>
      ) : (
        <div className={loadedFade} style={{ padding: "4px 26px 18px" }}>
          <DefaultRows t={t} slots={paged.pageSize} flipRef={paged.flipRef}>
          {paged.pageItems.map((it) => (
            <div
              key={it.id}
              style={{
                display: "grid",
                gridTemplateColumns: "auto 1fr auto auto",
                gap: t.space.md,
                alignItems: "center",
                minHeight: t.px(ROW_H),
                fontSize: t.font.sm,
                color: t.ink,
              }}
            >
              <span style={{ fontSize: t.font.xs, color: t.inkDim }}>
                {formatDateShort(it.created_at)}
              </span>
              <span style={{ color: t.inkDim, fontSize: t.font.xs, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {it.method || it.destination || ""}
              </span>
              <span style={{ fontWeight: t.weight.bold, fontVariantNumeric: "tabular-nums" }}>
                {it.amount_rub.toLocaleString("ru-RU")} ₽
              </span>
              {renderStatusPill(it.status)}
            </div>
          ))}
          </DefaultRows>
          <DefaultPagination t={t} page={paged.page} totalPages={paged.totalPages} onPrev={paged.prev} onNext={paged.next} />
        </div>
      )}
    </DefaultPanel>,
    false,
    true,
  );
}
