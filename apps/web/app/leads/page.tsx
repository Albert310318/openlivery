"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRight, ContactRound, LoaderCircle, Save, Search, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { EmptyState, PageHead } from "@/components/ui";
import { TableSkeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { getOriginatingConversationId } from "@/lib/leads";
import type { Client, Lead, LeadStatus } from "@/types";

const LIMIT = 30;
const STATUSES: LeadStatus[] = ["new", "qualified", "follow_up", "won", "lost"];
const SOON_MS = 24 * 60 * 60 * 1000;

function toLocalDateTimeInput(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function followUpState(iso: string | null): "none" | "overdue" | "soon" | "scheduled" {
  if (!iso) return "none";
  const distance = new Date(iso).getTime() - Date.now();
  if (distance < 0) return "overdue";
  return distance <= SOON_MS ? "soon" : "scheduled";
}

export default function LeadsPage() {
  const t = useT();
  const toast = useToast();
  const router = useRouter();
  const [items, setItems] = useState<Lead[]>([]);
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState<string | null>(null);
  const [queryReady, setQueryReady] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<LeadStatus | "">("");
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [savingFollowUpId, setSavingFollowUpId] = useState<string | null>(null);
  const [followUpDrafts, setFollowUpDrafts] = useState<Record<string, string>>({});

  useEffect(() => {
    setClientId(new URLSearchParams(window.location.search).get("client_id"));
    setQueryReady(true);
  }, []);

  useEffect(() => {
    api<Client[]>("/clients")
      .then(setClients)
      .catch((error) => toast.error(messageFrom(error)));
  }, [toast]);

  const clientNames = useMemo(
    () => new Map(clients.map((client) => [client.id, client.name])),
    [clients],
  );

  useEffect(() => {
    const timer = setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const params = useCallback((nextOffset: number) => {
    const value = new URLSearchParams({ limit: String(LIMIT), offset: String(nextOffset) });
    if (search) value.set("search", search);
    if (status) value.set("status", status);
    if (clientId) value.set("client_id", clientId);
    return value.toString();
  }, [clientId, search, status]);

  const loadFirst = useCallback(async () => {
    if (!queryReady) return;
    setLoading(true);
    try {
      const rows = await api<Lead[]>(`/leads?${params(0)}`);
      setItems(rows);
      setOffset(rows.length);
      setHasMore(rows.length === LIMIT);
    } catch (error) {
      toast.error(messageFrom(error));
    } finally {
      setLoading(false);
    }
  }, [params, queryReady, toast]);

  useEffect(() => { loadFirst(); }, [loadFirst]);

  async function loadMore() {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const rows = await api<Lead[]>(`/leads?${params(offset)}`);
      setItems((current) => [...current, ...rows]);
      setOffset((current) => current + rows.length);
      setHasMore(rows.length === LIMIT);
    } catch (error) {
      toast.error(messageFrom(error));
    } finally {
      setLoadingMore(false);
    }
  }

  async function changeStatus(lead: Lead, next: LeadStatus) {
    if (lead.status === next || savingId) return;
    const previous = lead.status;
    setSavingId(lead.id);
    setItems((rows) => rows.map((item) => item.id === lead.id ? { ...item, status: next } : item));
    try {
      const updated = await api<Lead>(`/leads/${lead.id}/status`, {
        method: "PATCH",
        body: JSON.stringify({ status: next }),
      });
      setItems((rows) => rows.map((item) => item.id === lead.id ? updated : item));
      toast.success(t("leads.statusUpdated"));
    } catch (error) {
      setItems((rows) => rows.map((item) => item.id === lead.id ? { ...item, status: previous } : item));
      toast.error(messageFrom(error));
    } finally {
      setSavingId(null);
    }
  }

  async function saveFollowUp(lead: Lead, value: string | null) {
    if (savingFollowUpId) return;
    const previous = lead.next_follow_up_at;
    const next = value ? new Date(value).toISOString() : null;
    setSavingFollowUpId(lead.id);
    setItems((rows) => rows.map((item) => item.id === lead.id ? { ...item, next_follow_up_at: next } : item));
    try {
      const updated = await api<Lead>(`/leads/${lead.id}/follow-up`, {
        method: "PATCH",
        body: JSON.stringify({ next_follow_up_at: next }),
      });
      setItems((rows) => rows.map((item) => item.id === lead.id ? updated : item));
      setFollowUpDrafts((drafts) => ({ ...drafts, [lead.id]: toLocalDateTimeInput(updated.next_follow_up_at) }));
      toast.success(t("leads.followUpUpdated"));
    } catch (error) {
      setItems((rows) => rows.map((item) => item.id === lead.id ? { ...item, next_follow_up_at: previous } : item));
      setFollowUpDrafts((drafts) => ({ ...drafts, [lead.id]: toLocalDateTimeInput(previous) }));
      toast.error(messageFrom(error));
    } finally {
      setSavingFollowUpId(null);
    }
  }

  async function openConversation(leadId: string) {
    try {
      const conversationId = await getOriginatingConversationId(leadId);
      if (!conversationId) {
        toast.error(t("leads.noConversation"));
        return;
      }
      router.push(`/inbox?conversation_id=${encodeURIComponent(conversationId)}`);
    } catch (error) {
      toast.error(messageFrom(error));
    }
  }

  return <div className="page leads-page">
    <PageHead eyebrow={t("leads.eyebrow")} title={t("leads.title")} description={t("leads.description")} />
    <div className="toolbar leads-toolbar">
      <label className="search-box"><Search size={18} /><input value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder={t("leads.searchPlaceholder")} /></label>
      <div className="filter-select"><span>{t("leads.client")}</span><select value={clientId ?? ""} onChange={(event) => setClientId(event.target.value || null)}><option value="">{t("leads.allClients")}</option>{clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}</select></div>
      <div className="filter-select"><span>{t("leads.status")}</span><select value={status} onChange={(event) => setStatus(event.target.value as LeadStatus | "")}><option value="">{t("leads.allStatuses")}</option>{STATUSES.map((value) => <option key={value} value={value}>{t(`leads.statuses.${value}`)}</option>)}</select></div>
    </div>
    {loading ? <TableSkeleton columns={9} /> : items.length ? <>
      <div className="table-shell leads-table-shell"><table className="data-table leads-table"><thead><tr><th>{t("leads.name")}</th><th>{t("leads.client")}</th><th>{t("leads.phone")}</th><th>{t("leads.interest")}</th><th>{t("leads.budget")}</th><th>{t("leads.preferredContactTime")}</th><th>{t("leads.nextFollowUp")}</th><th>{t("leads.status")}</th><th>{t("leads.conversation")}</th></tr></thead><tbody>{items.map((lead) => { const followUp = followUpState(lead.next_follow_up_at); return <tr key={lead.id}>
        <td data-label={t("leads.name")}><span className="entity-cell"><span className="entity-avatar"><ContactRound size={17} /></span><strong>{lead.name || t("leads.unnamed")}</strong></span></td>
        <td data-label={t("leads.client")}><strong>{clientNames.get(lead.client_id) || "—"}</strong></td>
        <td data-label={t("leads.phone")}>{lead.phone || "—"}</td>
        <td data-label={t("leads.interest")} className="lead-interest">{lead.interest || "—"}</td>
        <td data-label={t("leads.budget")}>{lead.budget || "—"}</td>
        <td data-label={t("leads.preferredContactTime")}>{lead.preferred_contact_time || "—"}</td>
        <td data-label={t("leads.nextFollowUp")}><div className="follow-up-control"><input type="datetime-local" value={followUpDrafts[lead.id] ?? toLocalDateTimeInput(lead.next_follow_up_at)} disabled={savingFollowUpId === lead.id} onChange={(event) => setFollowUpDrafts((drafts) => ({ ...drafts, [lead.id]: event.target.value }))} /><div><span className={`follow-up-state ${followUp}`}>{t(`leads.followUp${followUp === "none" ? "None" : followUp === "overdue" ? "Overdue" : followUp === "soon" ? "Soon" : "Scheduled"}`)}</span><button className="icon-button" disabled={savingFollowUpId === lead.id} title={t("leads.saveFollowUp")} aria-label={t("leads.saveFollowUp")} onClick={() => saveFollowUp(lead, followUpDrafts[lead.id] ?? toLocalDateTimeInput(lead.next_follow_up_at))}>{savingFollowUpId === lead.id ? <LoaderCircle className="spin" size={14} /> : <Save size={14} />}</button><button className="icon-button" disabled={savingFollowUpId === lead.id || !lead.next_follow_up_at} title={t("leads.clearFollowUp")} aria-label={t("leads.clearFollowUp")} onClick={() => saveFollowUp(lead, null)}><X size={14} /></button></div></div></td>
        <td data-label={t("leads.status")}><select className="lead-status-select" value={lead.status} disabled={savingId === lead.id} onChange={(event) => changeStatus(lead, event.target.value as LeadStatus)}>{STATUSES.map((value) => <option key={value} value={value}>{t(`leads.statuses.${value}`)}</option>)}</select></td>
        <td data-label={t("leads.conversation")}><button className="row-arrow" aria-label={t("leads.openConversation")} onClick={() => openConversation(lead.id)}><ArrowRight size={17} /></button></td>
      </tr>; })}</tbody></table></div>
      {hasMore && <div className="leads-load-more"><button className="button secondary" disabled={loadingMore} onClick={loadMore}>{loadingMore && <LoaderCircle className="spin" size={16} />}{t("leads.loadMore")}</button></div>}
    </> : <EmptyState icon={<ContactRound />} title={t("leads.emptyTitle")} description={t("leads.emptyDescription")} />}
  </div>;
}
