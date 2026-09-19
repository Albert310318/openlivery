"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, ApiError, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Alert } from "@/components/ui";

export type VerificationPending = { status: "verification_required"; masked_email: string; retry_after: number };

export function PortalEmailVerification({ pending, onBack, backLabel }: { pending: VerificationPending; onBack: () => void; backLabel?: string }) {
  const t = useT();
  const [code, setCode] = useState("");
  const [wait, setWait] = useState(pending.retry_after);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);
  useEffect(() => { const timer = setInterval(() => setWait(value => Math.max(0, value - 1)), 1000); return () => clearInterval(timer); }, []);
  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = await api<{ redirect_to: string }>("/portal/email-verification/confirm", { method: "POST", body: JSON.stringify({ code }) });
      window.location.assign(result.redirect_to);
    } catch (err) { setError(messageFrom(err)); } finally { setBusy(false); }
  }
  async function resend() {
    setBusy(true); setError(""); setSent(false);
    try {
      const result = await api<{ retry_after: number }>("/portal/email-verification/resend", { method: "POST" });
      setWait(result.retry_after); setCode(""); setSent(true);
    } catch (err) { setError(messageFrom(err)); if (err instanceof ApiError) setWait(err.retryAfter ?? 0); }
    finally { setBusy(false); }
  }
  return <form className="access-card access-form" onSubmit={confirm}>
    <h2>{t("auth.verifyTitle")}</h2><p>{t("auth.verifyCopy", { email: pending.masked_email })}</p>
    <label>{t("auth.verifyCode")}<input value={code} onChange={event => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required autoFocus /></label>
    <div aria-live="polite">{error && <Alert>{error}</Alert>}{sent && <Alert type="success">{t("auth.verifySent")}</Alert>}</div>
    <button className="button primary full" disabled={busy}>{t("auth.verifySubmit")}</button>
    <button className="button secondary" type="button" disabled={busy || wait > 0} onClick={resend}>{t("auth.verifyResend")}{wait > 0 ? ` (${wait}s)` : ""}</button>
    <button className="button secondary" type="button" disabled={busy} onClick={onBack}>{backLabel || t("auth.verifyBack")}</button>
  </form>;
}
