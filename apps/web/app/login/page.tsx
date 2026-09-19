"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Bot, Building2, LoaderCircle, MessageSquareText, ShieldCheck } from "lucide-react";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { PortalEmailVerification, type VerificationPending } from "@/components/portal-email-verification";
import { Alert } from "@/components/ui";
import { BrandLogo } from "@/components/brand";

type LoginResult = { principal_type: "admin" | "portal"; redirect_to: string };

export default function LoginPage() {
  const t = useT();
  const router = useRouter();
  const [pending, setPending] = useState<VerificationPending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [successMessage, setSuccessMessage] = useState("");

  useEffect(() => {
    if (window.location.search.includes("password_updated=1")) {
      setSuccessMessage("Contraseña actualizada correctamente.");
    }
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = Object.fromEntries(new FormData(event.currentTarget));
    try {
      const result = await api<LoginResult | VerificationPending>("/auth/unified-login", { method: "POST", body: JSON.stringify(data) });
      if ("redirect_to" in result) {
        router.push(result.redirect_to);
        router.refresh();
      } else {
        setPending(result);
      }
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="access-page agency-access">
      <header className="access-topbar">
        <div className="access-brand"><BrandLogo variant="compact" /><strong>Atiende y Vende</strong></div>
        <small>{t("auth.tagline")}</small>
      </header>
      <div className="access-layout">
        <section className="access-intro">
          <span className="access-eyebrow">{t("auth.introEyebrow")}</span>
          <h1>{t("auth.introTitle")}</h1>
          <p>{t("auth.introDescription")}</p>
          <div className="access-preview" aria-hidden="true">
            <header><div><BrandLogo variant="compact" className="preview-logo" /><strong>{t("auth.previewTitle")}</strong></div><small>{t("auth.previewToday")}</small></header>
            <div className="preview-metrics"><article><span>{t("auth.previewClientsActive")}</span><strong>12</strong></article><article><span>{t("auth.previewAgents")}</span><strong>28</strong></article><article><span>{t("auth.previewConversations")}</span><strong>846</strong></article></div>
            <div className="preview-list">
              <div><span className="preview-icon"><Building2 size={16} /></span><p><strong>{t("auth.previewClinicName")}</strong><small>{t("auth.previewClinicMeta")}</small></p><em>{t("auth.previewActive")}</em></div>
              <div><span className="preview-icon"><MessageSquareText size={16} /></span><p><strong>{t("auth.previewInboxTitle")}</strong><small>{t("auth.previewInboxMeta")}</small></p><em>{t("auth.previewInboxTag")}</em></div>
              <div><span className="preview-icon"><Bot size={16} /></span><p><strong>{t("auth.previewKnowledgeTitle")}</strong><small>{t("auth.previewKnowledgeMeta")}</small></p><em>{t("auth.previewReady")}</em></div>
            </div>
          </div>
        </section>
        <section className="access-form-wrap">
          {pending ? <PortalEmailVerification pending={pending} onBack={() => setPending(null)} /> : <div className="access-card">
            <span className="access-card-label"><ShieldCheck size={15} /> {t("auth.cardLabel")}</span>
            <h2>{t("auth.cardTitleLogin")}</h2>
            <p>{t("auth.cardSubtitleLogin")}</p>
            <form onSubmit={submit} className="access-form">
              <label>{t("auth.email")}<input name="email" required type="email" placeholder={t("auth.emailPlaceholder")} /></label>
              <label>{t("auth.password")}<input name="password" required type="password" minLength={8} placeholder={t("auth.passwordPlaceholder")} /></label>
              {error && <Alert>{error}</Alert>}
              <button className="button primary full" disabled={busy}>{busy && <LoaderCircle className="spin" size={17} />}{t("auth.submitLogin")}</button>
            </form>
            <p className="registration-login"><Link href="/recuperar-contrasena">¿Olvidaste tu contraseña?</Link></p>
            <p className="registration-login">¿No tienes una cuenta? <Link href="/registro">Crear cuenta</Link></p>
            {successMessage && <Alert type="success">{successMessage}</Alert>}
            <p className="access-security"><ShieldCheck size={14} /> {t("auth.securityNote")}</p>
          </div>}
        </section>
      </div>
    </main>
  );
}
