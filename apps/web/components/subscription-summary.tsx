"use client";

import { useEffect, useState } from "react";
import { api, messageFrom } from "@/lib/api";
import { useLanguage, useT } from "@/lib/i18n";
import type { Plan, Subscription } from "@/lib/subscriptions";
import { Alert } from "@/components/ui";

function PlanDetails({ plan }: { plan: Plan }) {
  const t = useT();
  const { lang } = useLanguage();
  const price = plan.monthly_price === null ? t("subscriptions.quote") :
    `${plan.currency} ${new Intl.NumberFormat(lang, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(plan.monthly_price))}`;
  return <>
    <h2>{plan.name}</h2><p>{plan.description}</p>
    <p><strong>{t("subscriptions.monthlyPrice")}:</strong> {price}</p>
    <h3>{t("subscriptions.modules")}</h3>
    {plan.modules.length ? <ul>{plan.modules.map((module) => <li key={module.code}>
      {module.name}{!module.is_available && <> — {t("subscriptions.unavailable")}</>}
    </li>)}</ul> : <p>{t("subscriptions.noModules")}</p>}
  </>;
}

export function SubscriptionSummary({ endpoint, improveHref }: { endpoint: string; improveHref?: string }) {
  const t = useT();
  const { lang } = useLanguage();
  const [result, setResult] = useState<{ endpoint: string; subscription: Subscription | null } | null>(null);
  const [error, setError] = useState<{ endpoint: string; message: string } | null>(null);
  useEffect(() => {
    let cancelled = false;
    api<Subscription | null>(endpoint).then((subscription) => {
      if (!cancelled) setResult({ endpoint, subscription });
    }).catch((err) => {
      if (!cancelled) setError({ endpoint, message: messageFrom(err) });
    });
    return () => { cancelled = true; };
  }, [endpoint]);
  // Preserve null: only undefined means the request is still pending.
  const subscription = result?.endpoint === endpoint ? result.subscription : undefined;
  const date = (value: string | null) => value ? new Date(value).toLocaleString(lang) : t("subscriptions.notScheduled");
  return <section className="portal-card">
    {error?.endpoint === endpoint ? <Alert>{error.message}</Alert> : subscription === undefined ?
      <p role="status">{t("subscriptions.loading")}</p> : subscription === null ?
      <p>{t("subscriptions.noSubscription")}</p> : <>
        <PlanDetails plan={subscription.plan} />
        <dl>
          <dt>{t("subscriptions.status")}</dt><dd>{t(`subscriptions.states.${subscription.status}`)}</dd>
          <dt>{t("subscriptions.firstActivation")}</dt><dd>{date(subscription.first_activated_at)}</dd>
          <dt>{t("subscriptions.trialStart")}</dt><dd>{date(subscription.trial_started_at)}</dd>
          <dt>{t("subscriptions.trialEnd")}</dt><dd>{date(subscription.trial_ends_at)}</dd>
          <dt>{t("subscriptions.renewal")}</dt><dd>{date(subscription.next_renewal_at)}</dd>
        </dl>
        {subscription.status === "TRIAL" && !subscription.first_activated_at && <p>{t("subscriptions.pendingActivation")}</p>}
      </>}
    {improveHref && <a className="button secondary" href={improveHref}>{t("subscriptions.improve")}</a>}
  </section>;
}

export function AvailablePlans({ endpoint }: { endpoint: string }) {
  const t = useT();
  const [result, setResult] = useState<{ endpoint: string; plans: Plan[] } | null>(null);
  const [error, setError] = useState<{ endpoint: string; message: string } | null>(null);
  useEffect(() => {
    let cancelled = false;
    api<Plan[]>(endpoint).then((plans) => { if (!cancelled) setResult({ endpoint, plans }); })
      .catch((err) => { if (!cancelled) setError({ endpoint, message: messageFrom(err) }); });
    return () => { cancelled = true; };
  }, [endpoint]);
  if (error?.endpoint === endpoint) return <Alert>{error.message}</Alert>;
  if (result?.endpoint !== endpoint) return <p role="status">{t("subscriptions.loading")}</p>;
  return <>
    <p>{t("subscriptions.readOnly")}</p>
    {result.plans.length ? <section className="portal-grid">{result.plans.map((plan) =>
      <article className="portal-card" key={plan.id}><PlanDetails plan={plan} /></article>)}</section> :
      <p>{t("subscriptions.noPlans")}</p>}
  </>;
}
