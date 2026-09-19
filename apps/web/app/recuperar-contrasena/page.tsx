"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { LoaderCircle, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { Alert } from "@/components/ui";
import { api, messageFrom } from "@/lib/api";
import { BrandLogo } from "@/components/brand";

type Stage = "email" | "code" | "password" | "done";
type RecoveryStage = Extract<Stage, "code" | "password">;

const RECOVERY_STORAGE_KEY = "ayv.passwordRecovery";

function readStoredRecovery(): { email: string; stage: RecoveryStage } | null {
  try {
    const stored = window.sessionStorage.getItem(RECOVERY_STORAGE_KEY);
    if (!stored) return null;

    const recovery: unknown = JSON.parse(stored);
    if (
      typeof recovery !== "object" || recovery === null ||
      !("email" in recovery) || typeof recovery.email !== "string" || !recovery.email.trim() ||
      !("stage" in recovery) || (recovery.stage !== "code" && recovery.stage !== "password")
    ) return null;

    return { email: recovery.email, stage: recovery.stage };
  } catch {
    return null;
  }
}

function storeRecovery(email: string, stage: RecoveryStage) {
  try {
    window.sessionStorage.setItem(RECOVERY_STORAGE_KEY, JSON.stringify({ email, stage }));
  } catch {
    // The in-memory flow can still continue if browser storage is unavailable.
  }
}

function clearStoredRecovery() {
  try {
    window.sessionStorage.removeItem(RECOVERY_STORAGE_KEY);
  } catch {
    // Nothing else is needed when browser storage is unavailable.
  }
}

export default function PasswordRecoveryPage() {
  const router = useRouter();
  const [stage, setStage] = useState<Stage>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [wait, setWait] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const recovery = readStoredRecovery();
    if (recovery) {
      setEmail(recovery.email);
      setStage(recovery.stage);
    } else {
      clearStoredRecovery();
    }
  }, []);

  useEffect(() => {
    if (!wait) return;
    const timer = setInterval(() => setWait(value => Math.max(0, value - 1)), 1000);
    return () => clearInterval(timer);
  }, [wait]);

  async function requestCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const recoveryEmail = email.trim();
    try {
      const result = await api<{ message: string }>("/auth/password-recovery/request", {
        method: "POST", body: JSON.stringify({ email: recoveryEmail }),
      });
      storeRecovery(recoveryEmail, "code");
      setEmail(recoveryEmail); setNotice(result.message); setWait(60); setStage("code");
    } catch (err) { setError(messageFrom(err)); }
    finally { setBusy(false); }
  }

  async function confirmCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await api("/auth/password-recovery/confirm", { method: "POST", body: JSON.stringify({ code }) });
      storeRecovery(email, "password");
      setStage("password"); setNotice("");
    } catch (err) { setError(messageFrom(err)); }
    finally { setBusy(false); }
  }

  async function resetPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    if (password !== confirmation) { setError("Las contraseñas no coinciden."); setBusy(false); return; }
    try {
      await api("/auth/password-recovery/reset", {
        method: "POST", body: JSON.stringify({ new_password: password, confirm_password: confirmation }),
      });
      clearStoredRecovery();
      setStage("done");
      window.setTimeout(() => router.push("/login?password_updated=1"), 1200);
    } catch (err) { setError(messageFrom(err)); }
    finally { setBusy(false); }
  }

  async function resendCode() {
    const recovery = readStoredRecovery();
    if (!recovery) {
      setEmail(""); setCode(""); setNotice(""); setError(""); setStage("email");
      return;
    }

    setBusy(true); setError("");
    try {
      const result = await api<{ message: string }>("/auth/password-recovery/request", {
        method: "POST", body: JSON.stringify({ email: recovery.email }),
      });
      setEmail(recovery.email); setNotice(result.message); setWait(60); setCode("");
    } catch (err) { setError(messageFrom(err)); }
    finally { setBusy(false); }
  }

  return <main className="access-page agency-access">
    <header className="access-topbar">
      <div className="access-brand"><BrandLogo variant="compact" /><strong>Atiende y Vende</strong></div>
      <small>Atiende y Vende — Inteligencia aplicada a las ventas</small>
    </header>
    <div className="access-layout">
      <section className="access-form-wrap">
        <div className="access-card">
          <span className="access-card-label"><ShieldCheck size={15} /> Recuperación segura</span>
          {stage === "email" && <>
            <h2>Recupera tu contraseña</h2>
            <p>Introduce tu correo electrónico para recibir un código de recuperación.</p>
            <form onSubmit={requestCode} className="access-form">
              <label>Correo electrónico<input value={email} onChange={event => setEmail(event.target.value)} required type="email" autoComplete="email" autoFocus /></label>
              {error && <Alert>{error}</Alert>}
              <button className="button primary full" disabled={busy}>{busy && <LoaderCircle className="spin" size={17} />}Enviar código</button>
            </form>
          </>}
          {stage === "code" && <>
            <h2>Introduce tu código</h2>
            <p>{notice || "Si el correo está registrado, recibirás un código de recuperación."}</p>
            <form onSubmit={confirmCode} className="access-form">
              <label>Código de recuperación<input value={code} onChange={event => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required autoFocus /></label>
              {error && <Alert>{error}</Alert>}
              <button className="button primary full" disabled={busy}>Continuar</button>
              <button className="button secondary" type="button" disabled={busy || wait > 0} onClick={resendCode}>Reenviar código{wait > 0 ? ` (${wait}s)` : ""}</button>
            </form>
          </>}
          {stage === "password" && <>
            <h2>Define una nueva contraseña</h2>
            <p>Elige una contraseña nueva para tu cuenta AYV.</p>
            <form onSubmit={resetPassword} className="access-form">
              <label>Nueva contraseña<input value={password} onChange={event => setPassword(event.target.value)} required type="password" minLength={8} autoComplete="new-password" autoFocus /></label>
              <label>Confirmar contraseña<input value={confirmation} onChange={event => setConfirmation(event.target.value)} required type="password" minLength={8} autoComplete="new-password" /></label>
              {error && <Alert>{error}</Alert>}
              <button className="button primary full" disabled={busy}>{busy && <LoaderCircle className="spin" size={17} />}Actualizar contraseña</button>
            </form>
          </>}
          {stage === "done" && <div aria-live="polite"><h2>Contraseña actualizada correctamente</h2><p>Te dirigiremos al inicio de sesión.</p></div>}
          {stage !== "done" && <p className="registration-login"><Link href="/login">Volver al login</Link></p>}
        </div>
      </section>
    </div>
  </main>;
}
