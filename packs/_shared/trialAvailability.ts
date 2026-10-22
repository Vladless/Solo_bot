"use client";

import { useState } from "react";
import { useAppInfo } from "@/app/AppInfoProvider";
import { useAccountSummary } from "@/components/constructor/blockContent/account/useAccountSummary";
import { useIdentityKey, useIdentityPending } from "@/lib/useIdentityKey";

/** Проверяет доступность пробника для текущего клиента. */
export function useTrialAvailability(preview = false) {
  const appInfo = useAppInfo();
  const identityId = useIdentityKey();
  const identityPending = useIdentityPending();
  const enabled = preview || appInfo.features.trialEnabled;
  const summary = useAccountSummary({ disabled: preview || !enabled });
  const [usedIdentityId, setUsedIdentityId] = useState<string | null>(null);
  const trialStatus = summary.data?.trial_status;
  const knownStatus = typeof trialStatus === "number" && Number.isFinite(trialStatus);
  const used = !preview && Boolean(identityId) && (
    usedIdentityId === identityId || (knownStatus && trialStatus !== 0 && trialStatus !== -1)
  );
  const pending = !preview && enabled && (identityPending || (Boolean(identityId) && summary.isLoading));
  const available = preview || (enabled && !pending && !used && (
    !identityId || (!summary.error && knownStatus && (trialStatus === 0 || trialStatus === -1))
  ));
  const markUsed = () => {
    if (preview || !identityId) return;
    setUsedIdentityId(identityId);
    void summary.mutate().catch(() => undefined);
  };
  return { enabled, available, used, pending, markUsed };
}
