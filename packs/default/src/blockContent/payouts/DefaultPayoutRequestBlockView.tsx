"use client";

import { useState } from "react";

import { useScreenFlip } from "@/components/constructor/blockContent/pageFlip";

const PAYOUT_SCREENS = ["request", "setup"] as const;
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import {
  useDefaultTheme,
  btnSolid,
  linkBtn,
  DefaultPanel,
  f,
  parseBlockData,
  useBlockApi,
  CONTROL_H,
  CONTROL_R,
} from "..";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import { isApiError } from "@/lib/api";
import { usePayoutHistory, usePayoutMethod } from "@/components/constructor/blockContent/payouts/payoutData";
import { PayoutMethodEditor } from "@/components/constructor/blockContent/payouts/PayoutMethodEditor";
import { DefaultBody, DefaultEmptyState, DefaultFooter } from "../layout";

const SCHEMA = {
  panelHeader: f.str("Запрос на вывод"),
  panelHeaderHintFmt: f.str("баланс {balance} ₽"),
  amountPlaceholder: f.str("Сумма к выводу"),
  submitLabel: f.str("Запросить"),
  submittingLabel: f.str("Отправляем..."),
  successText: f.str("✓ Заявка создана"),
  errorText: f.str("✕ Не удалось создать заявку"),
  loadingText: f.str("Загрузка..."),
  minAmount: f.num(100),
  notEnoughBalanceText: f.str("На балансе недостаточно средств"),
  belowMinText: f.str("Минимальная сумма {min} ₽"),
  setupHint: f.str("Чтобы выводить средства, укажите способ вывода и реквизиты."),
  setupCtaLabel: f.str("Установить способ вывода"),
  noMethodsText: f.str("Способы вывода не настроены администратором."),
  methodSelectLabel: f.str("Способ вывода"),
  requisitesLabel: f.str("Реквизиты"),
  requisitesRequiredText: f.str("Выберите способ и заполните реквизиты"),
  saveMethodLabel: f.str("Сохранить"),
  savingMethodLabel: f.str("Сохраняем..."),
  cancelLabel: f.str("Отмена"),
  changeMethodLabel: f.str("изменить"),
  methodSavedFmt: f.str("{method} · {masked}"),
};

export function DefaultPayoutRequestBlockView({ block, context }: TypedBlockViewProps<"defaultPayoutRequest">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, SCHEMA);

  const isPreview = previewMode === true;
  const api = useBlockApi({ needs: ["summary"], disabled: isPreview, mock: isPreview });
  const balance = api.summary.data?.partner_balance ?? 0;
  const headerHint = cfg.panelHeaderHintFmt.replace("{balance}", balance.toLocaleString("ru-RU"));

  const history = usePayoutHistory(isPreview, 20);
  const method = usePayoutMethod(isPreview);
  const availableMethods = method.state.methods;
  const configured = method.state.configured;

  const [amount, setAmount] = useState("");
  const [submitBusy, setSubmitBusy] = useState(false);
  const [statusMsg, setStatusMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const mut = useAccountMutations();

  const [setupOpen, setSetupOpen] = useState(false);
  const screenFlip = useScreenFlip(setupOpen ? "setup" : "request", PAYOUT_SCREENS);
  const openSetup = () => { if (!isPreview) setSetupOpen(true); };

  const onSubmit = async (e: React.SyntheticEvent) => {
    e.preventDefault();
    if (isPreview || submitBusy) return;
    const num = Number(amount.replace(/\s+/g, "").replace(",", "."));
    if (!Number.isFinite(num) || num <= 0 || num < cfg.minAmount) {
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
      setStatusMsg({ kind: "ok", text: res?.message?.trim() || cfg.successText });
      setAmount("");
      void history.refetch();
    } catch (err) {
      setStatusMsg({ kind: "err", text: isApiError(err) && err.message ? err.message : cfg.errorText });
    } finally {
      setSubmitBusy(false);
    }
  };

  const statusColor = statusMsg?.kind === "ok" ? t.success : statusMsg?.kind === "err" ? t.error : t.inkDim;

  const body = () => {
    if (!method.loaded) {
      return (
        <DefaultBody t={t}>
          <DefaultEmptyState t={t} text={cfg.loadingText} />
        </DefaultBody>
      );
    }
    if (setupOpen) {
      if (availableMethods.length === 0) {
        return (
          <DefaultBody t={t}>
            <DefaultEmptyState t={t} text={cfg.noMethodsText} />
          </DefaultBody>
        );
      }
      return (
        <DefaultBody t={t} gap={t.space.smPlus}>
          <PayoutMethodEditor
            state={method.state}
            disabled={isPreview}
            onSaved={async () => { await method.refetch(); setSetupOpen(false); }}
            onCancel={configured ? () => setSetupOpen(false) : undefined}
            inputStyle={{ background: t.innerBg, color: t.ink, border: "none", borderRadius: CONTROL_R, fontFamily: t.monoFont, fontSize: t.font.smPlus }}
            buttonStyle={btnSolid(t)}
            textStyle={{ color: t.inkDim, fontSize: t.font.sm }}
            errorStyle={{ color: t.error, fontSize: t.font.xs }}
            labels={{ method: cfg.methodSelectLabel, requisites: cfg.requisitesLabel, required: cfg.requisitesRequiredText,
              save: cfg.saveMethodLabel, saving: cfg.savingMethodLabel, cancel: cfg.cancelLabel }}
          />
        </DefaultBody>
      );
    }
    if (!configured) {
      return (
        <DefaultBody t={t}>
          <div style={{ fontSize: t.font.sm, color: t.inkDim }}>
            {availableMethods.length === 0 ? cfg.noMethodsText : cfg.setupHint}
          </div>
          <DefaultFooter t={t}>
            {availableMethods.length > 0 ? (
              <button
                type="button"
                disabled={isPreview}
                onClick={openSetup}
                style={{ ...btnSolid(t), width: "100%", justifyContent: "center", cursor: "pointer" }}
              >
                {cfg.setupCtaLabel}
              </button>
            ) : null}
          </DefaultFooter>
        </DefaultBody>
      );
    }
    return (
      <DefaultBody t={t} gap={t.space.smPlus}>
        <div style={{ display: "flex", alignItems: "center", gap: t.space.sm, fontSize: t.font.xs, color: t.inkDim, flexWrap: "wrap" }}>
          <span>{cfg.methodSavedFmt.replace("{method}", method.state.method_label || "").replace("{masked}", method.state.masked || "")}</span>
          <button type="button" disabled={isPreview} onClick={openSetup} style={{ ...linkBtn(t), cursor: "pointer" }}>{cfg.changeMethodLabel}</button>
        </div>
        <DefaultFooter t={t} stack>
          <input
            value={amount}
            inputMode="numeric"
            onChange={(e) => {
              setAmount(e.target.value.replace(/[^\d.,]/g, ""));
              if (statusMsg) setStatusMsg(null);
            }}
            onKeyDown={(e) => { if (e.key === "Enter") void onSubmit(e); }}
            placeholder={cfg.amountPlaceholder}
            disabled={submitBusy}
            style={{
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
          <button
            type="button"
            onClick={(e) => void onSubmit(e)}
            disabled={submitBusy}
            style={{ ...btnSolid(t), width: "100%", justifyContent: "center", cursor: "pointer" }}
          >
            {submitBusy ? cfg.submittingLabel : cfg.submitLabel}
          </button>
          {statusMsg ? <div style={{ fontSize: t.font.xs, color: statusColor }}>{statusMsg.text}</div> : null}
        </DefaultFooter>
      </DefaultBody>
    );
  };

  return wrap(
    <DefaultPanel t={t} header={cfg.panelHeader} hint={headerHint} bodyRef={screenFlip} noBodyPadding>
      {body()}
    </DefaultPanel>,
    false,
    true,
  );
}
