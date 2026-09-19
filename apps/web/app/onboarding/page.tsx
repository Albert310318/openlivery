"use client";

import Link from "next/link";
import { ArrowRight, Bot, Building2, Check, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { currentClient } from "@/lib/clients";
import type { Client } from "@/types";
import { BrandLogo } from "@/components/brand";

export default function OnboardingPage() {
  const [client, setClient] = useState<Client | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    currentClient().then(setClient).catch(() => setClient(null)).finally(() => setLoading(false));
  }, []);

  const clientHref = client ? `/clients/${client.id}` : "/";
  const agentHref = client ? `/agents/new?client=${client.id}` : "/";

  return <div className="page onboarding-page">
    <div className="onboarding-card">
      <BrandLogo variant="compact" className="onboarding-mark" />
      <span className="eyebrow">AYV · Primer paso</span>
      <h1>Tu empresa ya está lista.</h1>
      <p className="onboarding-lead">El siguiente paso es configurar tu negocio y crear tu primer agente para empezar a trabajar tus conversaciones.</p>
      {loading ? <p className="onboarding-loading"><LoaderCircle className="spin" size={17} /> Preparando tu espacio…</p> : <>
        <div className="onboarding-steps">
          <div className="onboarding-step done"><span><Check size={16} /></span><div><strong>Empresa registrada</strong><small>{client?.name || "Tu empresa"}</small></div></div>
          <div className="onboarding-step"><span><Building2 size={16} /></span><div><strong>Configura tu negocio</strong><small>Completa el contexto que usará tu equipo.</small></div></div>
          <div className="onboarding-step"><span><Bot size={16} /></span><div><strong>Crea tu agente</strong><small>Define cómo atenderá a tus clientes.</small></div></div>
        </div>
        <div className="onboarding-actions"><Link href={clientHref} className="button primary">Configurar mi negocio <ArrowRight size={16} /></Link><Link href={agentHref} className="button secondary">Crear mi agente</Link></div>
      </>}
    </div>
  </div>;
}
