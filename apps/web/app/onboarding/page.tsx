"use client";

import Link from "next/link";
import { ChangeEvent, FormEvent, useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Bot, Building2, Check, ImageIcon, LoaderCircle, Plus, Save, Trash2, Upload, X } from "lucide-react";
import { api, messageFrom } from "@/lib/api";
import { currentClient } from "@/lib/clients";
import { useLanguage } from "@/lib/i18n";
import { agentTemplates, localize } from "@/lib/agent-templates";
import { defaultModelFor } from "@/lib/providers";
import { useToast } from "@/components/toast";
import { BrandLogo } from "@/components/brand";
import type { Client, DeliveryZone, MenuCategory, RestaurantOnboarding, RestaurantOnboardingAgent, RestaurantPaymentMethod, RestaurantReadiness, RestaurantStaff, RestaurantWelcomeFlyer } from "@/types";

const STEPS = ["Datos del restaurante", "Carta / menú", "Modalidades", "Datos para pagos", "Personal", "Agente IA"];
const DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"];
const ROLE_LABELS: Record<RestaurantStaff["role"], string> = {
  admin: "Administrador", cashier: "Caja", waiter: "Mesero", kitchen: "Cocina", delivery: "Delivery",
};

type ProductDraft = { id?: string; category_id: string; name: string; description: string; price: string; image_url: string; variants: string; extras: string };
type ZoneDraft = { id?: string; name: string; fee: string; minimum_order: string; estimated_minutes: string };
type PaymentDraft = { instructions: string; account_name: string; account_number: string; qr_image_url: string; receipt_required: boolean };
type ProfileDraft = { address: string; phone: string; currency: string; opening_hours: Record<string, string> };
type ModalitiesDraft = { dine_in_enabled: boolean; pickup_enabled: boolean; delivery_enabled: boolean; delivery_whatsapp: string };
type StringSetter = (value: string) => void;
type ProductSetter = (value: ProductDraft) => void;
type PaymentSetter = (value: Record<string, PaymentDraft>) => void;
type ZoneSetter = (value: ZoneDraft) => void;

const emptyProduct = (categoryId = ""): ProductDraft => ({ category_id: categoryId, name: "", description: "", price: "", image_url: "", variants: "", extras: "" });
const emptyZone: ZoneDraft = { name: "", fee: "0", minimum_order: "0", estimated_minutes: "45" };
const emptyPayment = (): PaymentDraft => ({ instructions: "", account_name: "", account_number: "", qr_image_url: "", receipt_required: false });

function isRestaurantIndustry(industry: string) {
  return industry.trim().normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase() === "restaurante";
}

function parseOptions(value: string) {
  return value.split("\n").map((line) => line.trim()).filter(Boolean).map((line, position) => {
    const [name, price = "0"] = line.split("|");
    return { name: name.trim(), price: Number(price.trim() || 0), position, is_available: true };
  });
}

function paymentCompleteness(payment: RestaurantPaymentMethod) {
  return [payment.account_number, payment.account_name, payment.instructions, payment.qr_image_url].filter((value) => value?.trim()).length;
}

function canonicalPaymentMethod(payments: RestaurantPaymentMethod[]) {
  return [...payments].sort((left, right) => {
    const activeDifference = Number(right.is_active) - Number(left.is_active);
    if (activeDifference) return activeDifference;
    const completenessDifference = paymentCompleteness(right) - paymentCompleteness(left);
    if (completenessDifference) return completenessDifference;
    return new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime();
  })[0] || null;
}

function paymentDraftFromLegacyRows(rows: RestaurantPaymentMethod[]): PaymentDraft {
  const ordered = [...rows].sort((left, right) => {
    const activeDifference = Number(right.is_active) - Number(left.is_active);
    if (activeDifference) return activeDifference;
    const completenessDifference = paymentCompleteness(right) - paymentCompleteness(left);
    if (completenessDifference) return completenessDifference;
    return new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime();
  });
  const firstValue = (field: "account_number" | "account_name" | "instructions" | "qr_image_url") => ordered.find((row) => row[field]?.trim())?.[field] || "";
  return {
    account_number: firstValue("account_number"),
    account_name: firstValue("account_name"),
    instructions: firstValue("instructions"),
    qr_image_url: firstValue("qr_image_url"),
    receipt_required: rows.some((row) => row.receipt_required),
  };
}

export default function OnboardingPage() {
  const { lang } = useLanguage();
  const toast = useToast();
  const [client, setClient] = useState<Client | null>(null);
  const [data, setData] = useState<RestaurantOnboarding | null>(null);
  const [mode, setMode] = useState<"legacy" | "restaurant" | null>(null);
  const [step, setStep] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [profile, setProfile] = useState({ address: "", phone: "", currency: "PEN", opening_hours: {} as Record<string, string> });
  const [welcomeFlyer, setWelcomeFlyer] = useState<RestaurantWelcomeFlyer | null>(null);
  const [flyerBusy, setFlyerBusy] = useState(false);
  const [categoryName, setCategoryName] = useState("");
  const [editingCategory, setEditingCategory] = useState("");
  const [editingCategoryName, setEditingCategoryName] = useState("");
  const [product, setProduct] = useState<ProductDraft>(emptyProduct());
  const [zone, setZone] = useState<ZoneDraft>(emptyZone);
  const [modalities, setModalities] = useState({ dine_in_enabled: false, pickup_enabled: false, delivery_enabled: false, delivery_whatsapp: "" });
  const [payments, setPayments] = useState<Record<string, PaymentDraft>>({});
  const [paymentMethod, setPaymentMethod] = useState<RestaurantPaymentMethod["method"]>("other");
  const [staffUserId, setStaffUserId] = useState("");
  const [staffName, setStaffName] = useState("");
  const [staffEmail, setStaffEmail] = useState("");
  const [staffPassword, setStaffPassword] = useState("");
  const [staffActive, setStaffActive] = useState(true);
  const [staffRole, setStaffRole] = useState<RestaurantStaff["role"]>("admin");
  const [editingStaffId, setEditingStaffId] = useState("");
  const [editingStaffRole, setEditingStaffRole] = useState<RestaurantStaff["role"]>("admin");
  const [agent, setAgent] = useState({ name: "", personality: "", widget_greeting: "", instructions: "" });

  const load = async (clientId: string) => {
    const [current, onboarding] = await Promise.all([api<Client>(`/clients/${clientId}`), api<RestaurantOnboarding>(`/restaurants/${clientId}/onboarding`)]);
    setClient(current); setData(onboarding); setName(current.name); setDescription(current.description);
    setProfile(onboarding.profile); setWelcomeFlyer(onboarding.welcome_flyer); setModalities({ ...onboarding.modalities, delivery_whatsapp: onboarding.modalities.delivery_whatsapp || "" });
    const template = agentTemplates.find((item) => item.id === "restaurant");
    setAgent({ name: onboarding.agent?.name || "", personality: onboarding.agent?.personality || (template ? localize(template.personality, lang) : ""), widget_greeting: onboarding.agent?.widget_greeting || "", instructions: onboarding.agent?.instructions || "" });
    const canonical = canonicalPaymentMethod(onboarding.payment_methods);
    setPaymentMethod(canonical?.method || "other");
    setPayments({ [canonical?.method || "other"]: paymentDraftFromLegacyRows(onboarding.payment_methods) });
  };

  useEffect(() => {
    const requestedClientId = new URLSearchParams(window.location.search).get("client_id");
    const selectedClient = requestedClientId
      ? api<Client>(`/clients/${encodeURIComponent(requestedClientId)}`)
      : currentClient();
    selectedClient.then(async (selected) => {
      setClient(selected);
      if (requestedClientId && isRestaurantIndustry(selected.industry)) {
        setMode("restaurant");
        await load(selected.id);
      } else {
        setMode("legacy");
      }
    }).catch((err) => setError(messageFrom(err))).finally(() => setLoading(false));
  }, []);
  async function refresh() { if (client) await load(client.id); }
  async function run(action: () => Promise<void>, success?: string): Promise<boolean> { setBusy(true); setError(""); try { await action(); if (success) toast.success(success); return true; } catch (err) { setError(messageFrom(err)); toast.error(messageFrom(err)); return false; } finally { setBusy(false); } }

  async function saveProfile(event?: FormEvent) {
    event?.preventDefault(); if (!client) return;
    await run(async () => { await api(`/clients/${client.id}`, { method: "PATCH", body: JSON.stringify({ name, description }) }); await api(`/restaurants/${client.id}/profile`, { method: "PATCH", body: JSON.stringify(profile) }); await refresh(); }, "Datos guardados");
  }
  async function saveWelcomeFlyer(file: File | null, enabled: boolean, message: string) {
    if (!client) return;
    setFlyerBusy(true); setError("");
    try {
      const form = new FormData();
      if (file) form.append("file", file);
      form.append("enabled", String(enabled));
      form.append("message", message);
      const saved = await api<RestaurantWelcomeFlyer>(`/restaurants/${client.id}/welcome-flyer`, { method: "PUT", body: form });
      setWelcomeFlyer(saved);
      setData((current) => current ? { ...current, welcome_flyer: saved } : current);
      toast.success("Publicidad de bienvenida guardada");
    } catch (err) { setError(messageFrom(err)); toast.error(messageFrom(err)); }
    finally { setFlyerBusy(false); }
  }
  async function removeWelcomeFlyer() {
    if (!client) return;
    setFlyerBusy(true); setError("");
    try { await api(`/restaurants/${client.id}/welcome-flyer`, { method: "DELETE" }); setWelcomeFlyer(null); setData((current) => current ? { ...current, welcome_flyer: null } : current); toast.success("Publicidad eliminada"); }
    catch (err) { setError(messageFrom(err)); toast.error(messageFrom(err)); }
    finally { setFlyerBusy(false); }
  }
  async function addCategory(event: FormEvent) { event.preventDefault(); if (!client || !categoryName.trim()) return; await run(async () => { await api(`/restaurants/${client.id}/menu/categories`, { method: "POST", body: JSON.stringify({ name: categoryName.trim() }) }); setCategoryName(""); await refresh(); }, "Categoría agregada"); }
  async function saveCategory(categoryId: string) { if (!client || !editingCategoryName.trim()) return; await run(async () => { await api(`/restaurants/${client.id}/menu/categories/${categoryId}`, { method: "PATCH", body: JSON.stringify({ name: editingCategoryName.trim() }) }); setEditingCategory(""); await refresh(); }, "Categoría actualizada"); }
  async function saveProduct(event: FormEvent) { event.preventDefault(); if (!client) return; const payload = { category_id: product.category_id, name: product.name.trim(), description: product.description, price: Number(product.price), image_url: product.image_url.trim() || null, variants: parseOptions(product.variants), extras: parseOptions(product.extras), is_available: true }; await run(async () => { await api(`/restaurants/${client.id}/menu/products${product.id ? `/${product.id}` : ""}`, { method: product.id ? "PATCH" : "POST", body: JSON.stringify(payload) }); setProduct(emptyProduct(product.category_id)); await refresh(); }, product.id ? "Producto actualizado" : "Producto agregado"); }
  async function remove(path: string, message: string) { await run(async () => { await api(path, { method: "DELETE" }); await refresh(); }, message); }
  async function saveModalities(event: FormEvent) { event.preventDefault(); if (!client) return; await run(async () => { await api(`/restaurants/${client.id}/modalities`, { method: "PATCH", body: JSON.stringify(modalities) }); await refresh(); }, "Modalidades guardadas"); }
  async function saveZone() { return undefined; }
  async function savePayment(method: RestaurantPaymentMethod["method"]) {
    if (!client) return;
    await run(async () => {
      const draft = payments[method] || emptyPayment();
      await api(`/restaurants/${client.id}/payment-methods/${method}`, { method: "PUT", body: JSON.stringify({ display_name: "Datos para pagos", ...draft, qr_image_url: draft.qr_image_url || null, is_active: true }) });
      const legacyRows = (data?.payment_methods || []).filter((row) => row.method !== method && row.is_active);
      await Promise.all(legacyRows.map((row) => api(`/restaurants/${client.id}/payment-methods/${row.method}`, { method: "PUT", body: JSON.stringify({ is_active: false }) })));
      await refresh();
    }, "Datos para pagos guardados");
  }
  async function savePaymentsAndContinue() {
    if (!client) return false;
    const saved = await run(async () => {
      const draft = payments[paymentMethod] || emptyPayment();
      const payload = {
        display_name: "Datos para pagos",
        ...draft,
        qr_image_url: draft.qr_image_url || null,
        is_active: true,
      };
      await api(`/restaurants/${client.id}/payment-methods/${paymentMethod}`, { method: "PUT", body: JSON.stringify(payload) });
      const legacyRows = (data?.payment_methods || []).filter((row) => row.method !== paymentMethod && row.is_active);
      await Promise.all(legacyRows.map((row) => api(`/restaurants/${client.id}/payment-methods/${row.method}`, { method: "PUT", body: JSON.stringify({ is_active: false }) })));
      await refresh();
    }, "Datos para pagos guardados");
    if (saved) setStep((current) => Math.min(5, current + 1));
    return saved;
  }
  async function assignStaff(event: FormEvent) { event.preventDefault(); if (!client || !staffUserId) return; await run(async () => { const result = await api<{ development_verification_bypassed?: boolean }>(`/restaurants/${client.id}/staff`, { method: "POST", body: JSON.stringify({ user_id: staffUserId, role: staffRole }) }); if (result.development_verification_bypassed) setError("Usuario creado en modo desarrollo. Correo no verificado."); setStaffUserId(""); await refresh(); }, "Personal asignado"); }
  async function createStaff(event: FormEvent) { event.preventDefault(); if (!client) return; await run(async () => { const result = await api<{ development_verification_bypassed?: boolean }>(`/restaurants/${client.id}/staff`, { method: "POST", body: JSON.stringify({ name: staffName.trim(), email: staffEmail.trim().toLowerCase(), password: staffPassword, role: staffRole, is_active: staffActive }) }); if (result.development_verification_bypassed) setError("Usuario creado en modo desarrollo. Correo no verificado."); setStaffName(""); setStaffEmail(""); setStaffPassword(""); setStaffActive(true); await refresh(); }, "Personal agregado"); }
  async function updateStaff(staffId: string, payload: { role?: RestaurantStaff["role"]; is_active?: boolean }) { if (!client) return; await run(async () => { await api(`/restaurants/${client.id}/staff/${staffId}`, { method: "PATCH", body: JSON.stringify(payload) }); setEditingStaffId(""); await refresh(); }, "Personal actualizado"); }
  async function saveStaffAndContinue() { if (!client) return false; const hasExisting = Boolean(staffUserId); const hasNew = Boolean(staffName.trim() || staffEmail.trim() || staffPassword); if (!hasExisting && !hasNew) { setStep(5); return true; } const saved = await run(async () => { let result: { development_verification_bypassed?: boolean }; if (hasExisting) { result = await api<{ development_verification_bypassed?: boolean }>(`/restaurants/${client.id}/staff`, { method: "POST", body: JSON.stringify({ user_id: staffUserId, role: staffRole }) }); } else { result = await api<{ development_verification_bypassed?: boolean }>(`/restaurants/${client.id}/staff`, { method: "POST", body: JSON.stringify({ name: staffName.trim(), email: staffEmail.trim().toLowerCase(), password: staffPassword, role: staffRole, is_active: staffActive }) }); } if (result.development_verification_bypassed) setError("Usuario creado en modo desarrollo. Correo no verificado."); setStaffUserId(""); setStaffName(""); setStaffEmail(""); setStaffPassword(""); await refresh(); }, "Personal guardado"); if (saved) setStep(5); return saved; }
  async function saveAgentConfig(active = false) { if (!client) return; await run(async () => { const payload = { client_id: client.id, name: agent.name.trim(), description: "Agente IA para pedidos y atención del restaurante", personality: agent.personality, instructions: agent.instructions, widget_greeting: agent.widget_greeting, provider: "openai", model: defaultModelFor("openai"), is_active: active }; if (data?.agent) await api(`/agents/${data.agent.id}`, { method: "PATCH", body: JSON.stringify(payload) }); else await api("/agents", { method: "POST", body: JSON.stringify(payload) }); await refresh(); }, active ? "Agente activado" : "Configuración del agente guardada"); }
  async function activate() { if (data?.readiness.ready) await saveAgentConfig(true); }

  const readiness = data?.readiness ?? { profile: false, menu: false, modalities: false, payments: false, staff: false, agent: false, ready: false, status: "incomplete" as const };
  const statusLabel = readiness?.status === "agent_active" ? "Agente activo" : readiness?.status === "ready_to_activate" ? "Listo para activar" : "Configuración incompleta";
  const progress = ((step + 1) / STEPS.length) * 100;
  const localPaymentsReady = Boolean((payments[paymentMethod]?.account_number || "").trim());
  const pendingAdmin = staffRole === "admin" && (staffUserId || (staffName.trim() && staffEmail.trim() && staffPassword.length >= 8)) && (staffUserId || staffActive);
  const canNext = Boolean(readiness && (step === 3 ? localPaymentsReady : step === 4 ? readiness.staff || pendingAdmin : [readiness.profile, readiness.menu, readiness.modalities, readiness.payments, readiness.staff, readiness.agent][step]));

  if (loading && mode === "restaurant") return <div className="page"><p className="onboarding-loading"><LoaderCircle className="spin" size={17} /> Preparando tu restaurante…</p></div>;
  if (loading || mode === null) return error && !client ? <div className="page"><div className="alert">{error}</div><Link className="button secondary" href="/">Volver</Link></div> : <LegacyOnboarding client={client} loading={loading} />;
  if (mode === "legacy") return <LegacyOnboarding client={client} loading={false} />;
  if (!client || !data) return <div className="page"><div className="alert">{error || "No se pudo cargar el onboarding."}</div><Link className="button secondary" href="/">Volver</Link></div>;

  return <div className="page restaurant-onboarding"><Link href="/" className="back-link"><ArrowLeft size={16} /> Volver al inicio</Link><header className="page-head"><div><span className="eyebrow">Atiende y Vende · Onboarding</span><h1>Configuración del restaurante</h1><p>Completa la información operativa y prepara tu agente IA.</p></div><span className={`pill ${readiness?.status === "agent_active" ? "green" : readiness?.ready ? "purple" : ""}`}>{statusLabel}</span></header><div className="restaurant-progress"><div><strong>Paso {step + 1} de 6</strong><span>{STEPS[step]}</span></div><div className="progress-track"><i style={{ width: `${progress}%` }} /></div></div><ol className="wizard-steps restaurant-wizard-steps">{STEPS.map((label, index) => <li key={label} className={index === step ? "current" : index < step ? "done" : ""} onClick={() => index <= step && setStep(index)}><span>{index < step ? <Check size={14} /> : index + 1}</span><small>{label}</small></li>)}</ol>{error && <div className="alert">{error}</div>}<section className="wizard-card restaurant-wizard-card">{step === 0 && <ProfileStep name={name} description={description} profile={profile} setName={setName} setDescription={setDescription} setProfile={setProfile} onSave={saveProfile} busy={busy} welcomeFlyer={welcomeFlyer} flyerBusy={flyerBusy} onFlyerUpload={saveWelcomeFlyer} onFlyerDelete={removeWelcomeFlyer} />}{step === 1 && <MenuStep categories={data.categories} categoryName={categoryName} setCategoryName={setCategoryName} addCategory={addCategory} editingCategory={editingCategory} editingCategoryName={editingCategoryName} setEditingCategory={setEditingCategory} setEditingCategoryName={setEditingCategoryName} saveCategory={saveCategory} product={product} setProduct={setProduct} saveProduct={saveProduct} onDelete={remove} busy={busy} />}{step === 2 && <ModalitiesStep modalities={modalities} setModalities={setModalities} zones={data.delivery_zones} zone={zone} setZone={setZone} saveZone={saveZone} onDelete={remove} onSave={saveModalities} busy={busy} />}{step === 3 && <PaymentsStep payments={payments} setPayments={setPayments} savePayment={savePayment} />}{step === 4 && <StaffStep candidates={data.staff_candidates} staff={data.staff} staffUserId={staffUserId} setStaffUserId={setStaffUserId} staffRole={staffRole} setStaffRole={setStaffRole} staffName={staffName} setStaffName={setStaffName} staffEmail={staffEmail} setStaffEmail={setStaffEmail} staffPassword={staffPassword} setStaffPassword={setStaffPassword} staffActive={staffActive} setStaffActive={setStaffActive} editingStaffId={editingStaffId} setEditingStaffId={setEditingStaffId} editingStaffRole={editingStaffRole} setEditingStaffRole={setEditingStaffRole} assignStaff={assignStaff} createStaff={createStaff} updateStaff={updateStaff} onDelete={remove} busy={busy} />}{step === 5 && <AgentStep agent={agent} setAgent={setAgent} existing={data.agent} readiness={readiness} onSave={() => saveAgentConfig(false)} onActivate={activate} busy={busy} />}</section><div className="wizard-nav"><button className="button secondary" onClick={() => setStep((current) => Math.max(0, current - 1))} disabled={step === 0 || busy}><ArrowLeft size={16} /> Volver</button><span className="wizard-progress">{STEPS[step]}</span>{step < 5 ? <button className="button primary" onClick={step === 3 ? savePaymentsAndContinue : step === 4 ? saveStaffAndContinue : () => canNext && setStep((current) => Math.min(5, current + 1))} disabled={!canNext || busy}>Guardar y continuar <ArrowRight size={16} /></button> : <button className="button primary" onClick={activate} disabled={!readiness?.ready || busy || readiness.status === "agent_active"}>{busy ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />} Activar agente</button>}</div></div>;
}

function LegacyOnboarding({ client, loading }: { client: Client | null; loading: boolean }) {
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

function ProfileStep({ name, description, profile, setName, setDescription, setProfile, onSave, busy, welcomeFlyer, flyerBusy, onFlyerUpload, onFlyerDelete }: { name: string; description: string; profile: ProfileDraft; setName: StringSetter; setDescription: StringSetter; setProfile: (value: ProfileDraft) => void; onSave: (event: FormEvent<HTMLFormElement>) => void; busy: boolean; welcomeFlyer: RestaurantWelcomeFlyer | null; flyerBusy: boolean; onFlyerUpload: (file: File | null, enabled: boolean, message: string) => void; onFlyerDelete: () => void }) { return <form className="wizard-fields" onSubmit={onSave}><div className="wizard-copy"><h2>Datos del restaurante</h2><p>Información básica que usará tu equipo y tu agente.</p></div><div className="form-grid"><label>Nombre comercial<input value={name} onChange={(event) => setName(event.target.value)} required /></label><label>Teléfono comercial<input value={profile.phone} onChange={(event) => setProfile({ ...profile, phone: event.target.value })} required /></label></div><label>Descripción<textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} /></label><label>Dirección<textarea rows={2} value={profile.address} onChange={(event) => setProfile({ ...profile, address: event.target.value })} required /></label><div className="form-grid"><label>Moneda<select value={profile.currency} onChange={(event) => setProfile({ ...profile, currency: event.target.value })}><option value="PEN">PEN — Sol peruano</option><option value="USD">USD — Dólar</option><option value="EUR">EUR — Euro</option></select></label><span /></div><div><span className="field-label">Horarios de atención</span><div className="opening-hours">{DAYS.map((day) => <label key={day}><span>{day}</span><input placeholder="09:00 - 22:00" value={profile.opening_hours[day] || ""} onChange={(event) => setProfile({ ...profile, opening_hours: { ...profile.opening_hours, [day]: event.target.value } })} /></label>)}</div></div><WelcomeFlyerSection welcomeFlyer={welcomeFlyer} flyerBusy={flyerBusy} onFlyerUpload={onFlyerUpload} onFlyerDelete={onFlyerDelete} /><button className="button secondary align-start" disabled={busy}><Save size={15} /> Guardar datos</button></form>; }

function WelcomeFlyerSection({ welcomeFlyer, flyerBusy, onFlyerUpload, onFlyerDelete }: { welcomeFlyer: RestaurantWelcomeFlyer | null; flyerBusy: boolean; onFlyerUpload: (file: File | null, enabled: boolean, message: string) => void; onFlyerDelete: () => void }) {
  const [enabled, setEnabled] = useState(welcomeFlyer?.enabled ?? true); const [message, setMessage] = useState(welcomeFlyer?.message ?? ""); const [preview, setPreview] = useState(welcomeFlyer?.image_url ?? "");
  useEffect(() => { setEnabled(welcomeFlyer?.enabled ?? true); setMessage(welcomeFlyer?.message ?? ""); setPreview(welcomeFlyer?.image_url ?? ""); }, [welcomeFlyer]);
  const choose = (event: ChangeEvent<HTMLInputElement>) => { const file = event.target.files?.[0]; if (!file) return; if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 8 * 1024 * 1024) { event.target.value = ""; return; } setPreview(URL.createObjectURL(file)); onFlyerUpload(file, enabled, message); };
  return <section className="nested-section welcome-flyer-section"><div className="wizard-copy"><h3><ImageIcon size={17} /> Publicidad de bienvenida</h3><p>Se enviará una sola vez al iniciar una conversación nueva, después del saludo.</p></div>{preview ? <img className="welcome-flyer-preview" src={preview} alt="Vista previa de publicidad de bienvenida" /> : <div className="welcome-flyer-empty"><ImageIcon size={24} /><span>Aún no has configurado un flyer</span></div>}<div className="row-actions"><label className="button secondary"><Upload size={15} /> {welcomeFlyer ? "Reemplazar flyer" : "Subir flyer"}<input hidden type="file" accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" onChange={choose} /></label>{welcomeFlyer && <button type="button" className="button secondary" onClick={onFlyerDelete} disabled={flyerBusy}><Trash2 size={15} /> Eliminar</button>}</div><label className="switch-row"><span><strong>Enviar automáticamente al iniciar una conversación</strong><small>{enabled ? "Activado" : "Desactivado"}</small></span><input type="checkbox" checked={enabled} onChange={(event) => { setEnabled(event.target.checked); if (welcomeFlyer) onFlyerUpload(null, event.target.checked, message); }} disabled={flyerBusy || !welcomeFlyer} /></label><label>Mensaje opcional<textarea rows={2} maxLength={1000} value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Conoce nuestras promociones de bienvenida…" /><small className="field-help">Se mostrará como texto de la multimedia.</small></label>{welcomeFlyer && <button type="button" className="button secondary align-start" onClick={() => onFlyerUpload(null, enabled, message)} disabled={flyerBusy}><Save size={15} /> Guardar publicidad</button>}<small className="field-help">JPG, PNG o WEBP · máximo 8 MB.</small></section>;
}

function MenuStep({ categories, categoryName, setCategoryName, addCategory, editingCategory, editingCategoryName, setEditingCategory, setEditingCategoryName, saveCategory, product, setProduct, saveProduct, onDelete, busy }: { categories: MenuCategory[]; categoryName: string; setCategoryName: StringSetter; addCategory: (event: FormEvent<HTMLFormElement>) => void; editingCategory: string; editingCategoryName: string; setEditingCategory: StringSetter; setEditingCategoryName: StringSetter; saveCategory: (id: string) => void; product: ProductDraft; setProduct: ProductSetter; saveProduct: (event: FormEvent<HTMLFormElement>) => void; onDelete: (path: string, message: string) => void; busy: boolean }) { return <div className="wizard-fields"><div className="wizard-copy"><h2>Carta / menú</h2><p>Organiza categorías, productos, variantes y adicionales como datos estructurados.</p></div><form className="inline-create" onSubmit={addCategory}><input value={categoryName} onChange={(event) => setCategoryName(event.target.value)} placeholder="Nueva categoría: Entradas" required /><button className="button secondary" disabled={busy}><Plus size={15} /> Agregar categoría</button></form><div className="restaurant-menu-list">{categories.map((category: MenuCategory) => <article className="restaurant-category" key={category.id}><header><div>{editingCategory === category.id ? <input value={editingCategoryName} onChange={(event) => setEditingCategoryName(event.target.value)} /> : <strong>{category.name}</strong>}<small>{category.products.length} producto(s)</small></div><div className="row-actions">{editingCategory === category.id ? <button className="text-button" onClick={() => saveCategory(category.id)}>Guardar</button> : <button className="text-button" onClick={() => { setEditingCategory(category.id); setEditingCategoryName(category.name); }}>Editar</button>}<button className="icon-button danger-icon" onClick={() => onDelete(`/restaurants/${category.client_id}/menu/categories/${category.id}`, "Categoría eliminada")}><Trash2 size={15} /></button></div></header>{category.products.map((item) => <div className="restaurant-product" key={item.id}><div><strong>{item.name}</strong><small>{item.description || "Sin descripción"} · {item.price}</small><small>{item.variants.length} variante(s) · {item.extras.length} adicional(es)</small></div><div className="row-actions"><button className="text-button" onClick={() => setProduct({ id: item.id, category_id: category.id, name: item.name, description: item.description, price: String(item.price), image_url: item.image_url || "", variants: item.variants.map((option) => `${option.name}|${option.price}`).join("\n"), extras: item.extras.map((option) => `${option.name}|${option.price}`).join("\n") })}>Editar</button><button className="text-button" onClick={() => onDelete(`/restaurants/${category.client_id}/menu/products/${item.id}`, "Producto eliminado")}><Trash2 size={14} /></button><AvailabilityButton category={category} item={item} /></div></div>)}</article>)}</div><form className="product-editor" onSubmit={saveProduct}><div className="editor-head"><h3>{product.id ? "Editar producto" : "Agregar producto"}</h3>{product.id && <button type="button" className="icon-button" onClick={() => setProduct(emptyProduct(categories[0]?.id || ""))}><X size={16} /></button>}</div><div className="form-grid"><label>Categoría<select value={product.category_id} onChange={(event) => setProduct({ ...product, category_id: event.target.value })} required><option value="">Selecciona</option>{categories.map((category: MenuCategory) => <option key={category.id} value={category.id}>{category.name}</option>)}</select></label><label>Nombre<input value={product.name} onChange={(event) => setProduct({ ...product, name: event.target.value })} required /></label></div><div className="form-grid"><label>Precio<input type="number" min="0" step="0.01" value={product.price} onChange={(event) => setProduct({ ...product, price: event.target.value })} required /></label><label>Imagen opcional (URL)<input value={product.image_url} onChange={(event) => setProduct({ ...product, image_url: event.target.value })} /></label></div><label>Descripción<textarea rows={2} value={product.description} onChange={(event) => setProduct({ ...product, description: event.target.value })} /></label><div className="form-grid"><label>Variantes<small className="field-help">Una por línea: nombre|precio adicional</small><textarea rows={3} value={product.variants} onChange={(event) => setProduct({ ...product, variants: event.target.value })} /></label><label>Extras / adicionales<small className="field-help">Una por línea: nombre|precio</small><textarea rows={3} value={product.extras} onChange={(event) => setProduct({ ...product, extras: event.target.value })} /></label></div><button className="button primary align-start" disabled={busy || !categories.length}><Save size={15} /> {product.id ? "Guardar producto" : "Agregar producto"}</button></form></div>; }

function AvailabilityButton({ category, item }: { category: MenuCategory; item: MenuCategory["products"][number] }) { const [available, setAvailable] = useState(item.is_available); return <button className={`availability-toggle ${available ? "on" : ""}`} onClick={async () => { const next = !available; await api(`/restaurants/${category.client_id}/menu/products/${item.id}`, { method: "PATCH", body: JSON.stringify({ is_available: next }) }); setAvailable(next); }}>{available ? "Disponible" : "No disponible"}</button>; }

function ModalitiesStep({ modalities, setModalities, onSave, busy }: { modalities: ModalitiesDraft; setModalities: (value: ModalitiesDraft) => void; onSave: (event: FormEvent<HTMLFormElement>) => void; busy: boolean; zones?: DeliveryZone[]; zone?: ZoneDraft; setZone?: ZoneSetter; saveZone?: () => void; onDelete?: (path: string, message: string) => void }) { return <form className="wizard-fields" onSubmit={onSave}><div className="wizard-copy"><h2>Modalidades</h2><p>Activa las formas de atención. El delivery coordina su costo directamente con el cliente.</p></div><div className="choice-grid"><label className="choice-card"><input type="checkbox" checked={modalities.dine_in_enabled} onChange={(event) => setModalities({ ...modalities, dine_in_enabled: event.target.checked })} /><span><strong>Consumo en mesa</strong><small>Atención dentro del local</small></span></label><label className="choice-card"><input type="checkbox" checked={modalities.pickup_enabled} onChange={(event) => setModalities({ ...modalities, pickup_enabled: event.target.checked })} /><span><strong>Recojo en local</strong><small>El cliente recoge su pedido</small></span></label><label className="choice-card"><input type="checkbox" checked={modalities.delivery_enabled} onChange={(event) => setModalities({ ...modalities, delivery_enabled: event.target.checked })} /><span><strong>Delivery</strong><small>El personal coordina el costo con el cliente</small></span></label></div>{modalities.delivery_enabled && <div className="nested-section"><h3>Responsable de Delivery</h3><label>WhatsApp del responsable<input value={modalities.delivery_whatsapp || ""} onChange={(event) => setModalities({ ...modalities, delivery_whatsapp: event.target.value })} placeholder="965 396 982" /><small className="field-help">Se usará para enviar la ficha operativa cuando Cocina marque el pedido como Listo.</small></label>{!modalities.delivery_whatsapp?.trim() && <div className="alert">Configura un WhatsApp para que Delivery reciba los pedidos listos.</div>}</div>}<button className="button primary align-start" disabled={busy}><Save size={15} /> Guardar modalidades</button></form>; }

function PaymentsStep({ payments, setPayments, savePayment }: { payments: Record<string, PaymentDraft>; setPayments: PaymentSetter; savePayment: (method: RestaurantPaymentMethod["method"]) => void }) {
  const method = (Object.keys(payments)[0] || "other") as RestaurantPaymentMethod["method"];
  const payment = payments[method] || emptyPayment();
  const update = (next: PaymentDraft) => setPayments({ [method]: next });
  return <div className="wizard-fields"><div className="wizard-copy"><h2>Datos para pagos</h2><p>Configura el destino del abono. El cliente puede pagar desde cualquier banco o billetera; un comprobante enviado no significa que el pago esté confirmado.</p></div><div className="form-grid"><label>Número para pagos<input value={payment.account_number} onChange={(event) => update({ ...payment, account_number: event.target.value })} placeholder="+51 923097716" required /></label><label>Titular<input value={payment.account_name} onChange={(event) => update({ ...payment, account_name: event.target.value })} placeholder="Alberto Rojas Garcia" /></label></div><label>Instrucciones para el cliente<textarea rows={3} value={payment.instructions} onChange={(event) => update({ ...payment, instructions: event.target.value })} placeholder="Realiza el pago al número indicado y envía tu comprobante." /></label><label className="switch-row"><span><strong>Solicitar comprobante</strong><small>{payment.receipt_required ? "ON" : "OFF"} · El comprobante no confirma el pago automáticamente.</small></span><input type="checkbox" checked={payment.receipt_required} onChange={(event) => update({ ...payment, receipt_required: event.target.checked })} /></label><button type="button" className="button secondary align-start" onClick={() => savePayment(method)}>Guardar datos para pagos</button></div>;
}

function StaffStep({ candidates, staff, staffUserId, setStaffUserId, staffRole, setStaffRole, staffName, setStaffName, staffEmail, setStaffEmail, staffPassword, setStaffPassword, staffActive, setStaffActive, editingStaffId, setEditingStaffId, editingStaffRole, setEditingStaffRole, assignStaff, createStaff, updateStaff, onDelete, busy }: { candidates: RestaurantOnboarding["staff_candidates"]; staff: RestaurantStaff[]; staffUserId: string; setStaffUserId: StringSetter; staffRole: RestaurantStaff["role"]; setStaffRole: (value: RestaurantStaff["role"]) => void; staffName: string; setStaffName: StringSetter; staffEmail: string; setStaffEmail: StringSetter; staffPassword: string; setStaffPassword: StringSetter; staffActive: boolean; setStaffActive: (value: boolean) => void; editingStaffId: string; setEditingStaffId: StringSetter; editingStaffRole: RestaurantStaff["role"]; setEditingStaffRole: (value: RestaurantStaff["role"]) => void; assignStaff: (event: FormEvent<HTMLFormElement>) => void; createStaff: (event: FormEvent<HTMLFormElement>) => void; updateStaff: (staffId: string, payload: { role?: RestaurantStaff["role"]; is_active?: boolean }) => void; onDelete: (path: string, message: string) => void; busy: boolean }) {
  return <div className="wizard-fields">
    <div className="wizard-copy"><h2>Personal</h2><p>Asigna usuarios existentes o agrega personal sin duplicar cuentas.</p></div>
    <section className="nested-section"><h3>Asignar usuario existente</h3><form className="inline-create" onSubmit={assignStaff}><select value={staffUserId} onChange={(event) => setStaffUserId(event.target.value)} required><option value="">Selecciona un usuario</option>{candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name} · {candidate.email}</option>)}</select><select value={staffRole} onChange={(event) => setStaffRole(event.target.value as RestaurantStaff["role"])}>{Object.entries(ROLE_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select><button className="button secondary" disabled={busy}><Plus size={15} /> Asignar</button></form></section>
    <section className="nested-section"><h3>Agregar personal</h3><form className="form-grid" autoComplete="off" onSubmit={createStaff}><label>Nombre<input name="restaurant_staff_name" autoComplete="off" value={staffName} onChange={(event) => setStaffName(event.target.value)} required /></label><label>Correo electrónico<input name="restaurant_staff_email" type="email" autoComplete="email" value={staffEmail} onChange={(event) => setStaffEmail(event.target.value)} required /></label><label>Contraseña inicial<input name="restaurant_staff_initial_password" type="password" autoComplete="new-password" minLength={8} value={staffPassword} onChange={(event) => setStaffPassword(event.target.value)} required /></label><label>Rol<select value={staffRole} onChange={(event) => setStaffRole(event.target.value as RestaurantStaff["role"])}>{Object.entries(ROLE_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><label className="switch-row"><span><strong>Estado</strong><small>{staffActive ? "Activo" : "Inactivo"}</small></span><input type="checkbox" checked={staffActive} onChange={(event) => setStaffActive(event.target.checked)} /></label><button className="button secondary align-start" disabled={busy}><Plus size={15} /> Agregar</button></form></section>
    <section><h3>Personal asignado</h3>{staff.length ? <div className="table-shell"><table className="data-table"><thead><tr><th>Nombre</th><th>Correo electrónico</th><th>Rol</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>{staff.map((item) => { const editing = editingStaffId === item.id; return <tr key={item.id}><td>{item.user.name}</td><td>{item.user.email}</td><td>{editing ? <select value={editingStaffRole} onChange={(event) => setEditingStaffRole(event.target.value as RestaurantStaff["role"])}>{Object.entries(ROLE_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select> : ROLE_LABELS[item.role]}</td><td><button type="button" className="text-button" onClick={() => updateStaff(item.id, { is_active: !item.is_active })} disabled={busy}>{item.is_active ? "Activo" : "Inactivo"}</button></td><td><div className="row-actions">{editing ? <><button type="button" className="text-button" onClick={() => updateStaff(item.id, { role: editingStaffRole })}>Guardar</button><button type="button" className="text-button" onClick={() => setEditingStaffId("")}>Cancelar</button></> : <button type="button" className="text-button" onClick={() => { setEditingStaffId(item.id); setEditingStaffRole(item.role); }}>Editar</button>}<button type="button" className="icon-button danger-icon" onClick={() => onDelete(`/restaurants/${item.client_id}/staff/${item.id}`, "Asignación eliminada")}><Trash2 size={15} /></button></div></td></tr>; })}</tbody></table></div> : <div className="inline-empty slim">Agrega al menos un Administrador activo para continuar.</div>}</section>
  </div>;
}

function AgentStep({ agent, setAgent, existing, readiness, onSave, onActivate, busy }: { agent: { name: string; personality: string; widget_greeting: string; instructions: string }; setAgent: (value: { name: string; personality: string; widget_greeting: string; instructions: string }) => void; existing: RestaurantOnboardingAgent | null; readiness: RestaurantReadiness; onSave: () => void; onActivate: () => void; busy: boolean }) { const checks = [{ label: "Datos del restaurante", done: readiness.profile }, { label: "Carta / menú", done: readiness.menu }, { label: "Modalidades", done: readiness.modalities }, { label: "Datos para pagos", done: readiness.payments }, { label: "Personal administrador", done: readiness.staff }, { label: "Configuración del agente", done: readiness.agent }]; return <div className="wizard-fields"><div className="wizard-copy"><h2>Agente IA</h2><p>El contexto operativo se genera automáticamente con la configuración de los pasos anteriores.</p></div><div className="form-grid"><label>Nombre del agente<input value={agent.name} onChange={(event) => setAgent({ ...agent, name: event.target.value })} placeholder="Sofía" required /></label><label>Tono / personalidad<input value={agent.personality} onChange={(event) => setAgent({ ...agent, personality: event.target.value })} placeholder="Cercano, claro y amable" required /></label></div><label>Saludo<textarea rows={2} value={agent.widget_greeting} onChange={(event) => setAgent({ ...agent, widget_greeting: event.target.value })} placeholder="¡Hola! Soy el asistente de…" required /></label><label>Instrucciones especiales complementarias <small className="field-help">Opcional: agrega reglas particulares. No repitas menú, precios, modalidades, pagos ni personal.</small><textarea rows={7} value={agent.instructions} onChange={(event) => setAgent({ ...agent, instructions: event.target.value })} placeholder="Ej. Deriva reclamos complejos al equipo humano." /></label><button type="button" className="button secondary align-start" onClick={onSave} disabled={busy}><Save size={15} /> Guardar agente</button><section className="activation-summary"><div className="summary-head"><div><h3>Resumen antes de activar</h3><p>El agente usa automáticamente los datos estructurados y se activará cuando los 6 requisitos estén completos.</p></div><span className={`pill ${readiness.ready ? "green" : ""}`}>{readiness.ready ? "Listo para activar" : "Incompleto"}</span></div><ul>{checks.map((check) => <li key={check.label} className={check.done ? "done" : ""}><span>{check.done ? <Check size={14} /> : "·"}</span>{check.label}</li>)}</ul>{existing?.is_active && <div className="alert alert-success">Este agente ya está activo.</div>}{readiness.ready && !existing?.is_active && <button type="button" className="button primary" onClick={onActivate} disabled={busy}><Check size={15} /> Activar agente ahora</button>}</section></div>; }
