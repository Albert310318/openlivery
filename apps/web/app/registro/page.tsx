"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { ArrowRight, Bot, Building2, Check, LoaderCircle, ShieldCheck } from "lucide-react";
import { Alert } from "@/components/ui";
import { api, messageFrom } from "@/lib/api";
import { PortalEmailVerification, type VerificationPending } from "@/components/portal-email-verification";
import { BrandLogo } from "@/components/brand";

export default function RegistrationPage() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<VerificationPending | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    const password = String(data.get("password") || "");
    const confirmation = String(data.get("password_confirmation") || "");
    if (password !== confirmation) {
      setError("Las contraseñas no coinciden.");
      setBusy(false);
      return;
    }
    try {
      const result = await api<VerificationPending>("/auth/register", {
        method: "POST",
        body: JSON.stringify({
          agency_name: data.get("agency_name"),
          industry: data.get("industry"),
          name: data.get("name"),
          whatsapp: data.get("whatsapp"),
          email: data.get("email"),
          password,
        }),
      });
      setPending(result);
    } catch (err) {
      setError(messageFrom(err));
      setBusy(false);
    }
  }

  return (
    <main className="access-page agency-access">
      <header className="access-topbar">
        <div className="access-brand"><BrandLogo variant="compact" /><strong>Atiende y Vende</strong></div>
        <small>Atiende y Vende — Inteligencia aplicada a las ventas</small>
      </header>
      <div className="access-layout registration-layout">
        <section className="access-intro">
          <span className="access-eyebrow">Empieza con AYV</span>
          <h1>Configura tu empresa y empieza a vender mejor.</h1>
          <p>Registra tu empresa para preparar tu espacio comercial y dar el siguiente paso: configurar tu negocio y tu primer agente.</p>
          <div className="registration-preview">
            <div><span className="preview-icon"><Building2 size={17} /></span><p><strong>Tu empresa</strong><small>Contexto comercial en un solo lugar</small></p><Check size={17} /></div>
            <div><span className="preview-icon"><Bot size={17} /></span><p><strong>Tu agente</strong><small>El siguiente paso después del registro</small></p><ArrowRight size={17} /></div>
          </div>
        </section>
        <section className="access-form-wrap">
          {pending ? <PortalEmailVerification pending={pending} onBack={() => setPending(null)} backLabel="Volver al registro" /> : <div className="access-card registration-card">
            <span className="access-card-label"><ShieldCheck size={15} /> Registro seguro</span>
            <h2>Crea tu cuenta AYV</h2>
            <p>Completa tus datos para comenzar con tu empresa.</p>
            <form onSubmit={submit} className="access-form registration-form">
              <div className="registration-grid">
                <label>Nombre de la empresa<input name="agency_name" required minLength={2} autoFocus /></label>
                <label>Rubro/industria<input name="industry" required minLength={2} /></label>
                <label>Nombre del responsable<input name="name" required minLength={2} autoComplete="name" /></label>
                <label>WhatsApp<input name="whatsapp" required type="tel" inputMode="tel" autoComplete="tel" placeholder="+51 987 654 321" /></label>
              </div>
              <label>Correo electrónico<input name="email" required type="email" autoComplete="email" /></label>
              <div className="registration-grid">
                <label>Contraseña<input name="password" required type="password" minLength={8} autoComplete="new-password" /></label>
                <label>Confirmar contraseña<input name="password_confirmation" required type="password" minLength={8} autoComplete="new-password" /></label>
              </div>
              {error && <Alert>{error}</Alert>}
              <button className="button primary full" disabled={busy}>{busy && <LoaderCircle className="spin" size={17} />}Crear mi cuenta</button>
            </form>
            <p className="registration-login">¿Ya tienes una cuenta? <Link href="/login">Iniciar sesión</Link></p>
          </div>}
        </section>
      </div>
    </main>
  );
}
