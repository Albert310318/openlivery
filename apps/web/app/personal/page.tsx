"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { ExternalLink, LoaderCircle, Pencil, Plus, Power, UsersRound, X } from "lucide-react";
import { api, messageFrom } from "@/lib/api";
import { currentClient } from "@/lib/clients";
import { Modal, PageHead, StatusBadge } from "@/components/ui";
import type { Client, RestaurantModalities, RestaurantStaff, User } from "@/types";

type OperationalRole = "waiter" | "kitchen" | "cashier" | "delivery";
type StaffDraft = { name: string; email: string; phone: string; password: string; role: OperationalRole; is_active: boolean };

const ROLE_LABELS: Record<OperationalRole, string> = {
  waiter: "Mesero",
  kitchen: "Cocina",
  cashier: "Caja",
  delivery: "Delivery",
};

const emptyDraft = (): StaffDraft => ({ name: "", email: "", phone: "", password: "", role: "waiter", is_active: true });

function panelHref(clientId: string, staff: RestaurantStaff) {
  return `/orders?client_id=${encodeURIComponent(clientId)}&view_as_role=${encodeURIComponent(staff.role)}&view_as_staff_id=${encodeURIComponent(staff.id)}`;
}

export default function PersonalPage() {
  const [client, setClient] = useState<Client | null>(null);
  const [staff, setStaff] = useState<RestaurantStaff[]>([]);
  const [modalities, setModalities] = useState<RestaurantModalities | null>(null);
  const [draft, setDraft] = useState<StaffDraft>(emptyDraft());
  const [editing, setEditing] = useState<RestaurantStaff | null>(null);
  const [editDraft, setEditDraft] = useState<StaffDraft>(emptyDraft());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [isGeneralAdminClientView, setIsGeneralAdminClientView] = useState(false);

  async function load() {
    const requested = new URLSearchParams(window.location.search).get("client_id");
    const me = await api<User>("/auth/me");
    setIsGeneralAdminClientView(me.is_vendiq_admin && Boolean(requested));
    const selected = requested ? await api<Client>(`/clients/${encodeURIComponent(requested)}`) : await currentClient();
    const [rows, configuredModalities] = await Promise.all([
      api<RestaurantStaff[]>(`/restaurants/${selected.id}/staff`),
      api<RestaurantModalities>(`/restaurants/${selected.id}/modalities`),
    ]);
    setClient(selected);
    setStaff(rows.filter((row) => row.role !== "admin") as RestaurantStaff[]);
    setModalities(configuredModalities);
  }

  useEffect(() => {
    load().catch((reason) => setError(messageFrom(reason))).finally(() => setLoading(false));
  }, []);

  async function createStaff(event: FormEvent) {
    event.preventDefault();
    if (!client) return;
    setSaving(true); setError(""); setNotice("");
    try {
      await api(`/restaurants/${client.id}/staff`, { method: "POST", body: JSON.stringify({ ...draft, email: draft.email.trim().toLowerCase(), phone: draft.phone.trim() || null }) });
      setDraft(emptyDraft());
      await load();
      setNotice("Acceso creado correctamente.");
    } catch (reason) { setError(messageFrom(reason)); } finally { setSaving(false); }
  }

  function beginEdit(row: RestaurantStaff) {
    setEditing(row);
    setEditDraft({ name: row.user.name, email: row.user.email, phone: row.user.phone || "", password: "", role: row.role as OperationalRole, is_active: row.is_active });
    setError(""); setNotice("");
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault();
    if (!client || !editing) return;
    setSaving(true); setError(""); setNotice("");
    try {
      const payload: Record<string, unknown> = { ...editDraft, email: editDraft.email.trim().toLowerCase(), phone: editDraft.phone.trim() || null };
      if (!editDraft.password) delete payload.password;
      await api(`/restaurants/${client.id}/staff/${editing.id}`, { method: "PATCH", body: JSON.stringify(payload) });
      setEditing(null);
      await load();
      setNotice("Acceso actualizado correctamente.");
    } catch (reason) { setError(messageFrom(reason)); } finally { setSaving(false); }
  }

  async function toggleStaff(row: RestaurantStaff) {
    if (!client) return;
    setSaving(true); setError(""); setNotice("");
    try {
      await api(`/restaurants/${client.id}/staff/${row.id}`, { method: "PATCH", body: JSON.stringify({ is_active: !row.is_active }) });
      await load();
      setNotice(row.is_active ? "Personal desactivado." : "Personal activado.");
    } catch (reason) { setError(messageFrom(reason)); } finally { setSaving(false); }
  }

  if (loading) return <div className="page"><div className="page-loading"><LoaderCircle className="spin" size={18} /> Cargando personal…</div></div>;
  if (!client) return <div className="page"><div className="alert error">{error || "No se encontró la PYME seleccionada."}</div></div>;

  const administrativeBackLink = isGeneralAdminClientView
    ? <Link className="button small secondary" href={`/clients/${encodeURIComponent(client.id)}`}>← Volver a {client.name}</Link>
    : undefined;
  const deliveryWhatsapp = modalities?.delivery_whatsapp?.trim() || "";
  const deliveryExternalActive = Boolean(modalities?.delivery_enabled && deliveryWhatsapp);

  return <div className="page personal-page">
    <PageHead eyebrow={client.name} title="Personal" description={isGeneralAdminClientView ? "Vista administrativa" : "Administra los accesos operativos del restaurante."} action={administrativeBackLink} />
    {error && <div className="alert error">{error}</div>}
    {notice && <div className="alert alert-success">{notice}</div>}

    <section className="card" style={{ padding: 24, marginBottom: 20 }}>
      <div className="section-heading"><div><h2>Agregar personal</h2><p>Crea un acceso reutilizando las credenciales actuales del restaurante.</p></div><UsersRound size={22} /></div>
      <form className="stack-form" onSubmit={createStaff}>
        <div className="form-grid">
          <label>Nombre<input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} required /></label>
          <label>Rol<select value={draft.role} onChange={(event) => setDraft({ ...draft, role: event.target.value as OperationalRole })}>{Object.entries(ROLE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>Correo de acceso<input type="email" value={draft.email} onChange={(event) => setDraft({ ...draft, email: event.target.value })} required /></label>
          <label>Teléfono <span className="field-help">Opcional</span><input value={draft.phone} onChange={(event) => setDraft({ ...draft, phone: event.target.value })} /></label>
          <label>Contraseña<input type="password" minLength={8} value={draft.password} onChange={(event) => setDraft({ ...draft, password: event.target.value })} required /></label>
          <label className="checkbox-label"><input type="checkbox" checked={draft.is_active} onChange={(event) => setDraft({ ...draft, is_active: event.target.checked })} /> Acceso activo</label>
        </div>
        <button className="button primary align-start" type="submit" disabled={saving}><Plus size={16} /> Crear acceso</button>
      </form>
    </section>

    <section className="card" style={{ padding: 24 }}>
      <div className="section-heading"><div><h2>Trabajadores</h2><p>Solo se muestran trabajadores de {client.name}.</p></div><span className="pill">{staff.length} interno(s) · 1 externo</span></div>
      {staff.length === 0 && <div className="inline-empty"><UsersRound size={24} /><div><strong>No hay personal interno registrado</strong><span>Agrega Mesero, Cocina o Caja desde el formulario.</span></div></div>}
      <div className="table-shell"><table className="data-table"><thead><tr><th>Nombre</th><th>Acceso</th><th>Tipo / función</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>{staff.map((row) => <tr key={row.id}><td><strong>{row.user.name}</strong></td><td>{row.user.email}{row.user.phone && <small>{row.user.phone}</small>}</td><td><span className="worker-type-badge internal">Interno</span><small>{ROLE_LABELS[row.role as OperationalRole] || row.role}</small></td><td><StatusBadge active={row.is_active} /></td><td><div className="row-actions"><button className="text-button" type="button" onClick={() => beginEdit(row)}><Pencil size={14} /> Editar</button><button className="text-button" type="button" onClick={() => toggleStaff(row)} disabled={saving}><Power size={14} /> {row.is_active ? "Desactivar" : "Activar"}</button>{row.is_active ? <Link className="text-button" href={panelHref(client.id, row)}><ExternalLink size={14} /> Ver panel</Link> : <span className="field-help">Activa para ver panel</span>}</div></td></tr>)}<tr className="external-worker-row"><td><strong>Trabajador externo – Delivery</strong><small>Responsable configurado en Modalidades</small></td><td>{deliveryWhatsapp || "No configurado"}<small>WhatsApp</small></td><td><span className="worker-type-badge external">Externo</span><small>Delivery</small></td><td><StatusBadge active={deliveryExternalActive} /></td><td><span className="field-help">Sin correo, contraseña ni acceso al panel</span></td></tr></tbody></table></div>
    </section>

    <Modal open={Boolean(editing)} title="Editar acceso" description="Actualiza los datos y credenciales del trabajador." onClose={() => setEditing(null)}>
      <form className="modal-form" onSubmit={saveEdit}>
        <div className="form-grid">
          <label>Nombre<input value={editDraft.name} onChange={(event) => setEditDraft({ ...editDraft, name: event.target.value })} required /></label>
          <label>Rol<select value={editDraft.role} onChange={(event) => setEditDraft({ ...editDraft, role: event.target.value as OperationalRole })}>{Object.entries(ROLE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>Correo de acceso<input type="email" value={editDraft.email} onChange={(event) => setEditDraft({ ...editDraft, email: event.target.value })} required /></label>
          <label>Teléfono<input value={editDraft.phone} onChange={(event) => setEditDraft({ ...editDraft, phone: event.target.value })} /></label>
          <label>Nueva contraseña <span className="field-help">Opcional, mínimo 8 caracteres</span><input type="password" minLength={8} value={editDraft.password} onChange={(event) => setEditDraft({ ...editDraft, password: event.target.value })} /></label>
          <label className="checkbox-label"><input type="checkbox" checked={editDraft.is_active} onChange={(event) => setEditDraft({ ...editDraft, is_active: event.target.checked })} /> Acceso activo</label>
        </div>
        <div className="modal-actions"><button type="button" className="button secondary" onClick={() => setEditing(null)} disabled={saving}><X size={15} /> Cancelar</button><button type="submit" className="button primary" disabled={saving}>{saving ? <LoaderCircle className="spin" size={16} /> : <Pencil size={15} />} Guardar cambios</button></div>
      </form>
    </Modal>
  </div>;
}
