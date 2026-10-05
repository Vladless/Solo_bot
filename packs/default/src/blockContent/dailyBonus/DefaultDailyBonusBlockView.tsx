"use client";

import { useEffect, useRef, useState } from "react";
import type { TypedBlockViewProps } from "@/components/constructor/blockContent/viewTypes";
import {
  useDefaultTheme,
  btnSolid,
  DefaultPanel,
  f,
  parseBlockData,
  cardEdge,
} from "..";
import { hexToRgba } from "@/components/constructor/utils";
import { useAccountMutations } from "@/components/constructor/blockContent/account/useAccountMutations";
import { isApiError } from "@/lib/api";
import { ladderDayLabel, ladderWindow, bonusAmountText, formatCountdown, useDailyBonus, type DailyBonusState } from "@/components/constructor/blockContent/dailyBonusData";
import { DefaultBody, DefaultFooter, GROUP_GAP } from "../layout";

const SCHEMA = {
  panelHeader: f.str("Ежедневный бонус"),
  panelHeaderHintFmt: f.str("серия {streak}"),
  introText: f.str("Забирай бонус на баланс каждый день — чем дольше серия, тем больше сумма."),
  amountFmt: f.str("{amount} ₽"),
  amountRangeFmt: f.str("{min}–{max} ₽"),
  claimLabel: f.str("Забрать бонус"),
  claimingLabel: f.str("Начисляем..."),
  successFmt: f.str("✓ Начислено {amount} ₽"),
  errorText: f.str("✕ Не удалось начислить бонус"),
  loadingText: f.str("Загрузка..."),
  waitLabel: f.str("Следующий бонус через"),
  countdownFmt: f.str("{hh}:{mm}:{ss}"),
  reasonCooldownText: f.str("Бонус на сегодня уже получен"),
  reasonLimitText: f.str("Лимит бонусов за период исчерпан"),
  reasonSubscriptionText: f.str("Бонус доступен с активной подпиской"),
  reasonAccountAgeText: f.str("Бонус откроется чуть позже после регистрации"),
  reasonDisabledText: f.str("Ежедневный бонус сейчас недоступен"),
  showStreak: f.bool(true),
  showLadder: f.bool(true),
  ladderTitle: f.str("Серия"),
  ladderDayFmt: f.str("{days} д"),
  ladderMaxItems: f.num(7),
  showTotal: f.bool(true),
  totalFmt: f.str("всего получено {total} ₽"),
  showClaimsLeft: f.bool(true),
  claimsLeftFmt: f.str("осталось бонусов: {left}"),
};

function money(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

export function DefaultDailyBonusBlockView({ block, context }: TypedBlockViewProps<"defaultDailyBonus">) {
  const { wrap, previewMode } = context;
  const d = block.data as Record<string, unknown>;
  const t = useDefaultTheme(d);
  const cfg = parseBlockData(d, SCHEMA);

  const isPreview = previewMode === true;
  const bonus = useDailyBonus(isPreview);
  const mut = useAccountMutations();

  const [busy, setBusy] = useState(false);
  const [statusMsg, setStatusMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [seconds, setSeconds] = useState(0);
  const refetchRef = useRef(bonus.refetch);
  refetchRef.current = bonus.refetch;

  const state = bonus.state;

  useEffect(() => {
    const initial = state?.seconds_left ?? 0;
    setSeconds(initial);
    if (initial <= 0) return;
    const deadline = Date.now() + initial * 1000;
    const timer = window.setInterval(() => {
      const left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
      setSeconds(left);
      if (left <= 0) {
        window.clearInterval(timer);
        void refetchRef.current();
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [state?.seconds_left, state?.next_available_at]);

  const reasonText = (value: DailyBonusState): string => {
    if (!value.enabled) return cfg.reasonDisabledText;
    switch (value.reason) {
      case "cooldown":
        return cfg.reasonCooldownText;
      case "limit":
        return cfg.reasonLimitText;
      case "subscription":
        return cfg.reasonSubscriptionText;
      case "account_age":
        return cfg.reasonAccountAgeText;
      case "misconfigured":
        return cfg.reasonDisabledText;
      default:
        return "";
    }
  };

  const onClaim = async () => {
    if (isPreview || busy || !state?.can_claim) return;
    setBusy(true);
    setStatusMsg(null);
    try {
      const res = await mut.claimDailyBonus();
      if (res?.ok) {
        setStatusMsg({ kind: "ok", text: cfg.successFmt.replace("{amount}", money(Number(res.amount || 0))) });
      } else {
        const next = res?.state as unknown as DailyBonusState | undefined;
        setStatusMsg({ kind: "err", text: next ? reasonText(next) || cfg.errorText : cfg.errorText });
      }
      if (res?.state) bonus.writeState(res.state as unknown as DailyBonusState);
      else await bonus.refetch();
    } catch (err) {
      setStatusMsg({ kind: "err", text: isApiError(err) && err.message ? err.message : cfg.errorText });
    } finally {
      setBusy(false);
    }
  };

  const body = (value: DailyBonusState) => {
    const blocked = !value.enabled || !value.can_claim;
    const ladderView = cfg.showLadder ? ladderWindow(value.ladder, cfg.ladderMaxItems, value.streak_next) : { items: [], offset: 0 };
    const ladder = ladderView.items;
    const notes: string[] = [];
    if (cfg.showTotal) notes.push(cfg.totalFmt.replace("{total}", money(value.claimed_total)));
    if (cfg.showClaimsLeft && value.claims_per_period > 1) {
      notes.push(cfg.claimsLeftFmt.replace("{left}", String(value.claims_left)));
    }
    const reason = reasonText(value);
    const statusColor = statusMsg?.kind === "ok" ? t.success : statusMsg?.kind === "err" ? t.error : t.inkDim;
    const showTimer = blocked && seconds > 0;

    return (
      <DefaultBody t={t} gap={t.px(6)}>
        {cfg.introText ? <div style={{ fontSize: t.font.sm, color: t.inkDim }}>{cfg.introText}</div> : null}

        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: t.px(GROUP_GAP), minWidth: 0 }}>
          <span
            style={{
              fontSize: t.font.xl,
              fontWeight: t.weight.bold,
              color: value.enabled ? t.accent : t.inkMute,
              fontVariantNumeric: "tabular-nums",
              lineHeight: 1.1,
            }}
          >
            {bonusAmountText(value, cfg.amountFmt, cfg.amountRangeFmt)}
          </span>
          {notes.length > 0 ? (
            <span style={{ fontSize: t.font.xs, color: t.inkMute, textAlign: "right", whiteSpace: "nowrap" }}>
              {notes.join(" · ")}
            </span>
          ) : null}
        </div>

        {ladder.length > 1 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: t.px(4), minWidth: 0, flex: "1 1 auto" }}>
            {cfg.ladderTitle ? <div style={{ fontSize: t.font.xs, color: t.inkMute, lineHeight: 1 }}>{cfg.ladderTitle}</div> : null}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: `repeat(${ladder.length}, minmax(0, 1fr))`,
                gap: t.space.sm,
                minWidth: 0,
              }}
            >
              {ladder.map((amount, index) => {
                const day = ladderView.offset + index + 1;
                const active = day === value.streak_next;
                return (
                  <span
                    key={day}
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: t.px(2),
                      minWidth: 0,
                      minHeight: t.px(40),
                      padding: `${t.px(4)}px ${t.px(4)}px`,
                      borderRadius: t.radius.sm,
                      background: active ? hexToRgba(t.accent, 0.1) : t.innerBg,
                      boxShadow: active ? `inset 0 0 0 1.5px ${hexToRgba(t.accent, 0.4)}` : cardEdge(t),
                    }}
                  >
                    <span style={{ fontSize: t.font.xxs, color: t.inkMute, lineHeight: 1 }}>
                      {ladderDayLabel(cfg.ladderDayFmt, day, value.cooldown_hours)}
                    </span>
                    <span
                      style={{
                        fontSize: t.font.smPlus,
                        fontWeight: t.weight.bold,
                        color: active ? t.accent : t.ink,
                        fontVariantNumeric: "tabular-nums",
                        lineHeight: 1.1,
                      }}
                    >
                      {money(amount)}
                    </span>
                  </span>
                );
              })}
            </div>
          </div>
        ) : null}

        {statusMsg ? <div style={{ fontSize: t.font.xs, color: statusColor }}>{statusMsg.text}</div> : null}
        {!showTimer && blocked && reason ? <div style={{ fontSize: t.font.sm, color: t.inkDim }}>{reason}</div> : null}

        <DefaultFooter t={t}>
          {value.can_claim ? (
            <button
              type="button"
              onClick={() => void onClaim()}
              disabled={busy}
              style={{ ...btnSolid(t), flex: "1 1 auto", justifyContent: "center", cursor: busy ? "default" : "pointer" }}
            >
              {busy ? cfg.claimingLabel : cfg.claimLabel}
            </button>
          ) : showTimer ? (
            <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
              <span style={{ fontSize: t.font.xs, color: t.inkMute }}>{cfg.waitLabel}</span>
              <span
                style={{
                  fontSize: t.font.lg,
                  fontWeight: t.weight.bold,
                  color: t.ink,
                  fontVariantNumeric: "tabular-nums",
                  lineHeight: 1.1,
                }}
              >
                {formatCountdown(seconds, cfg.countdownFmt)}
              </span>
            </span>
          ) : null}
        </DefaultFooter>
      </DefaultBody>
    );
  };

  const headerHint =
    cfg.showStreak && state && state.streak > 0
      ? cfg.panelHeaderHintFmt.replace("{streak}", String(state.streak))
      : undefined;

  return wrap(
    <DefaultPanel
      t={t}
      header={cfg.panelHeader}
      hint={headerHint}
      loading={!bonus.loaded}
      loadingText={cfg.loadingText}
      noBodyPadding
    >
      {state ? body(state) : null}
    </DefaultPanel>,
    false,
    true,
  );
}
