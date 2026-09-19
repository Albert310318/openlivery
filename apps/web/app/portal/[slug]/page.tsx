"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { Bot, Building2, ContactRound, House, Inbox, LoaderCircle, LogOut, MessageSquareText, Radio, Send, ShieldCheck, UserRound } from "lucide-react";
import { PortalEmailVerification, type VerificationPending } from "@/components/portal-email-verification";
import { SubscriptionSummary, AvailablePlans } from "@/components/subscription-summary";
import { Alert, EmptyState } from "@/components/ui";
import { api, ApiError, messageFrom } from "@/lib/api";
import { formatWhen, isNearBottom, isSameOpenThread } from "@/lib/datetime";
import { useLanguage, useT } from "@/lib/i18n";
import type { AgentSummary, Conversation, Lead, PortalPublic } from "@/types";

const POLL_MS = 8000;

type Session = { client_id: string; client_name: string; portal_slug: string; agency_name: string };

export default function PortalPage() {
  const t = useT();
  const { slug } = useParams<{ slug: string }>();
  const [pending, setPending] = useState<VerificationPending | null>(null);
  const [portal, setPortal] = useState<PortalPublic | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => { Promise.all([api<PortalPublic>(`/portal/${slug}`), api<Session>(`/portal/${slug}/me`).catch((err) => { if (err instanceof ApiError && err.status === 401) return null; throw err; })]).then(([info, me]) => { setPortal(info); setSession(me); }).catch((err) => setError(messageFrom(err))).finally(() => setLoading(false)); }, [slug]);
  async function login(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setError(""); const data = new FormData(event.currentTarget); try { const result = await api<Session | VerificationPending>(`/portal/${slug}/login`, { method: "POST", body: JSON.stringify({ email: data.get("email"), password: data.get("password") }) }); if ("status" in result) setPending(result); else setSession(result); } catch (err) { setError(messageFrom(err)); } }
  async function logout() { await api(`/portal/${slug}/logout`, { method: "POST" }); setSession(null); }
  if (loading) return <div className="portal-loader"><LoaderCircle className="spin" /> {t("portal.loader.loading")}</div>;
  if (!portal) return <div className="portal-loader">{error || t("portal.loader.unavailable")}</div>;
  if (pending) return <main className="access-page portal-access"><section className="access-form-wrap"><PortalEmailVerification pending={pending} onBack={() => setPending(null)} /></section></main>;
  if (!session) return <main className="access-page portal-access" style={{ "--portal-color": portal.agency_brand_color } as React.CSSProperties}><header className="access-topbar"><div className="access-brand portal-access-brand">{portal.agency_logo_url ? <img src={`${portal.agency_logo_url}`} alt={portal.agency_name} /> : <span>{portal.agency_name.slice(0, 1)}</span>}<strong>{portal.agency_name}</strong></div><small>{t("portal.access.secureBadge")}</small></header><div className="access-layout"><section className="access-intro"><span className="access-eyebrow">{t("portal.access.eyebrow")}</span><h1>{portal.portal_title}</h1><p>{t("portal.access.intro")}</p><div className="access-preview portal-preview" aria-hidden="true"><header><div><span className="preview-logo"><Inbox size={16} /></span><strong>{t("portal.access.preview.inbox")}</strong></div><small>{t("portal.access.preview.conversationsCount")}</small></header><div className="portal-preview-thread"><div className="active"><span className="preview-icon"><UserRound size={16} /></span><p><strong>{t("portal.access.preview.newInquiry")}</strong><small>{t("portal.access.preview.newInquiryMeta")}</small></p><em>2</em></div><div><span className="preview-icon"><MessageSquareText size={16} /></span><p><strong>{t("portal.access.preview.salesFollowUp")}</strong><small>{t("portal.access.preview.salesFollowUpMeta")}</small></p></div><div><span className="preview-icon"><Building2 size={16} /></span><p><strong>{t("portal.access.preview.servicesInfo")}</strong><small>{t("portal.access.preview.servicesInfoMeta")}</small></p></div></div><footer><span><Bot size={15} /> {t("portal.access.preview.agentReplying")}</span><strong>{t("portal.access.preview.takeControl")}</strong></footer></div></section><section className="access-form-wrap"><form className="access-card access-form" onSubmit={login}><span className="portal-client-avatar">{portal.client_name.slice(0, 2).toUpperCase()}</span><span className="access-card-label"><ShieldCheck size={15} /> {t("portal.access.form.cardLabel")}</span><h2>{t("portal.access.form.welcome", { name: portal.client_name })}</h2><p>{t("portal.access.form.subtitle")}</p><label>{t("portal.access.form.emailLabel")}<input name="email" type="email" required autoFocus placeholder={t("portal.access.form.emailPlaceholder")} /></label><label>{t("portal.access.form.passwordLabel")}<input name="password" type="password" required placeholder={t("portal.access.form.passwordPlaceholder")} /></label>{error && <Alert>{error}</Alert>}<button className="button primary full">{t("portal.access.form.submit")}</button><small className="access-security"><ShieldCheck size={14} /> {t("portal.access.form.security", { name: portal.agency_name })}</small></form></section></div></main>;
  return <ClientPortal slug={slug} portal={portal} logout={logout} />;
}

type PortalView = "home" | "leads" | "inbox" | "agents" | "channels" | "plan" | "plans";
type PortalSummary = { client_name: string; industry: string; description: string; is_active: boolean; agents: number; active_agents: number; conversations: number; leads: number; channels: number; connected_channels: number };
type PortalChannel = { type: string; status: string; display_name: string | null; phone_number: string | null; is_enabled: boolean };

function PortalNavigation({ slug, active }: { slug: string; active: PortalView }) {
  const t = useT();
  const links: { id: PortalView; icon: React.ReactNode; label: string }[] = [
    { id: "home", icon: <House size={18} />, label: t("portal.nav.home") }, { id: "leads", icon: <ContactRound size={18} />, label: t("portal.nav.leads") },
    { id: "inbox", icon: <Inbox size={18} />, label: t("portal.inbox.nav.inbox") }, { id: "agents", icon: <Bot size={18} />, label: t("portal.inbox.nav.agents") },
    { id: "plan", icon: <Building2 size={18} />, label: t("subscriptions.myPlan") },
    { id: "channels", icon: <Radio size={18} />, label: t("portal.nav.channels") },
  ];
  return <nav>{links.map((item) => <a key={item.id} className={active === item.id ? "active" : ""} href={`/portal/${slug}?view=${item.id}`}>{item.icon}<span>{item.label}</span></a>)}</nav>;
}

function ClientPortal({ slug, portal, logout }: { slug: string; portal: PortalPublic; logout: () => void }) {
  const t = useT();
  const requested = useSearchParams().get("view");
  const view: PortalView = requested && ["home", "leads", "inbox", "agents", "channels", "plan", "plans"].includes(requested) ? requested as PortalView : "home";
  const [summary, setSummary] = useState<PortalSummary | null>(null);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [channels, setChannels] = useState<PortalChannel[]>([]);
  const [error, setError] = useState("");
  useEffect(() => { Promise.all([api<PortalSummary>(`/portal/${slug}/summary`), api<Lead[]>(`/portal/${slug}/leads`), api<AgentSummary[]>(`/portal/${slug}/agents`), api<PortalChannel[]>(`/portal/${slug}/channels`)]).then(([s, l, a, c]) => { setSummary(s); setLeads(l); setAgents(a); setChannels(c); }).catch((err) => setError(messageFrom(err))); }, [slug]);
  if (view === "inbox") return <PortalInbox slug={slug} portal={portal} logout={logout} />;
  return <main className="portal-app" style={{ "--portal-color": portal.agency_brand_color } as React.CSSProperties}><aside className="portal-nav"><div className="portal-brand">{portal.agency_logo_url ? <img src={portal.agency_logo_url} alt="Logo" /> : <span>{portal.agency_name.slice(0, 1)}</span>}<strong>{portal.client_name}</strong></div><PortalNavigation slug={slug} active={view} /><button onClick={logout}><LogOut size={17} /><span>{t("portal.inbox.nav.logout")}</span></button></aside><section className="portal-main"><header><div><small>{t("portal.inbox.header.eyebrow")}</small><h1>{view === "plan" ? t("subscriptions.myPlan") : view === "plans" ? t("subscriptions.availablePlans") : t(`portal.titles.${view}`)}</h1></div></header>{error && <Alert>{error}</Alert>}
    {view === "plan" && <SubscriptionSummary key={slug} endpoint={`/portal/${slug}/subscription`} improveHref={`/portal/${slug}?view=plans`} />}
    {view === "plans" && <AvailablePlans key={slug} endpoint={`/portal/${slug}/plans`} />}
    {view === "home" && summary && <><section className="portal-metrics"><article><small>{t("portal.metrics.leads")}</small><strong>{summary.leads}</strong></article><article><small>{t("portal.metrics.conversations")}</small><strong>{summary.conversations}</strong></article><article><small>{t("portal.metrics.activeAgents")}</small><strong>{summary.active_agents}/{summary.agents}</strong></article><article><small>{t("portal.metrics.connectedChannels")}</small><strong>{summary.connected_channels}/{summary.channels}</strong></article></section><section className="portal-card"><h2>{summary.client_name}</h2><small>{summary.industry || t("portal.common.notConfigured")}</small><p>{summary.description || t("portal.home.noDescription")}</p></section></>}
    {view === "leads" && <div className="portal-card portal-table"><table><thead><tr><th>{t("portal.leads.name")}</th><th>{t("portal.leads.contact")}</th><th>{t("portal.leads.interest")}</th><th>{t("portal.leads.status")}</th></tr></thead><tbody>{leads.map((lead) => <tr key={lead.id}><td>{lead.name || "—"}</td><td>{lead.phone || lead.email || "—"}</td><td>{lead.interest || "—"}</td><td><span className="pill">{lead.status}</span></td></tr>)}</tbody></table>{!leads.length && <p>{t("portal.leads.empty")}</p>}</div>}
    {view === "agents" && <section className="portal-grid">{agents.map((agent) => <article className="portal-card" key={agent.id}><Bot size={22} /><h3>{agent.name}</h3><p>{agent.description || t("portal.agents.noDescription")}</p><span className={`pill ${agent.is_active ? "green" : ""}`}>{agent.is_active ? t("portal.common.active") : t("portal.common.inactive")}</span></article>)}</section>}
    {view === "channels" && <section className="portal-grid">{channels.map((channel, index) => <article className="portal-card" key={`${channel.type}-${index}`}><Radio size={22} /><h3>{channel.display_name || channel.type.replace("_", " ")}</h3><p>{channel.phone_number || t("portal.common.notConfigured")}</p><span className={`pill ${channel.status === "connected" ? "green" : ""}`}>{channel.status}</span></article>)}{!channels.length && <EmptyState icon={<Radio />} title={t("portal.channels.empty")} description={t("portal.channels.emptyDescription")} />}</section>}
  </section></main>;
}

function PortalInbox({ slug, portal, logout }: { slug: string; portal: PortalPublic; logout: () => void }) {
  const t = useT();
  const { lang } = useLanguage();
  const [items, setItems] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selectedIdRef = useRef<string | null>(null);
  const messagesRef = useRef<HTMLDivElement>(null);
  useEffect(() => { selectedIdRef.current = selected?.id ?? null; }, [selected]);
  const wasNearBottomRef = useRef(true);
  useEffect(() => {
    const el = messagesRef.current;
    if (el) { const handler = () => { wasNearBottomRef.current = isNearBottom(el); }; el.addEventListener("scroll", handler, { passive: true }); return () => el.removeEventListener("scroll", handler); }
  }, [selected?.id]);
  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    if (wasNearBottomRef.current) {
      const frame = requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
      return () => cancelAnimationFrame(frame);
    }
  }, [selected?.id, selected?.messages?.at(-1)?.id]);

  const refresh = useCallback(async () => {
    const rows = await api<Conversation[]>(`/portal/${slug}/conversations`);
    setItems(rows);
    const openId = selectedIdRef.current ?? rows[0]?.id;
    if (!openId) return;
    const conv = await api<Conversation>(`/portal/${slug}/conversations/${openId}`);
    if (selectedIdRef.current && selectedIdRef.current !== openId) return;
    setSelected((prev) => (isSameOpenThread(prev, conv) ? prev : conv));
  }, [slug]);

  useEffect(() => { refresh().catch((err) => setError(messageFrom(err))); }, [refresh]);
  useEffect(() => {
    const id = setInterval(() => { refresh().catch(() => {}); }, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  async function choose(item: Conversation) {
    selectedIdRef.current = item.id;
    setSelected(await api<Conversation>(`/portal/${slug}/conversations/${item.id}`));
  }
  async function setMode(mode: "ai" | "human") { if (!selected) return; setSelected(await api<Conversation>(`/portal/${slug}/conversations/${selected.id}/mode`, { method: "PATCH", body: JSON.stringify({ mode }) })); await refresh(); }
  async function reply(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (!selected) return; const form = event.currentTarget; const data = new FormData(form); setBusy(true); setError(""); try { setSelected(await api<Conversation>(`/portal/${slug}/conversations/${selected.id}/reply`, { method: "POST", body: JSON.stringify({ content: data.get("content") }) })); form.reset(); await refresh(); } catch (err) { setError(messageFrom(err)); } finally { setBusy(false); } }
  return <main className="portal-app" style={{ "--portal-color": portal.agency_brand_color } as React.CSSProperties}><aside className="portal-nav"><div className="portal-brand">{portal.agency_logo_url ? <img src={`${portal.agency_logo_url}`} alt="Logo" /> : <span>{portal.agency_name.slice(0, 1)}</span>}<strong>{portal.client_name}</strong></div><nav><a className="active"><Inbox size={18} /> {t("portal.inbox.nav.inbox")}</a><a className="disabled"><Bot size={18} /> {t("portal.inbox.nav.agents")}</a><a href={`/portal/${slug}?view=plan`}>{t("subscriptions.myPlan")}</a></nav><button onClick={logout}><LogOut size={17} /> {t("portal.inbox.nav.logout")}</button></aside><section className="portal-main"><header><div><small>{t("portal.inbox.header.eyebrow")}</small><h1>{portal.portal_title}</h1></div><span>{t("portal.inbox.header.conversationsCount", { count: items.length })}</span></header>{items.length ? <div className="portal-inbox"><aside>{items.map((item) => <button key={item.id} onClick={() => choose(item)} className={selected?.id === item.id ? "active" : ""}><span className="entity-avatar tiny"><UserRound size={15} /></span><span><span className="portal-inbox-row-top"><strong>{item.title}</strong><time>{formatWhen(item.updated_at, lang)}</time></span><small className="portal-inbox-preview">{item.preview || t("portal.inbox.list.noMessages")}</small><small>{item.mode === "human" ? t("portal.inbox.list.humanSupport") : t("portal.inbox.list.aiAgent")}</small></span></button>)}</aside><section>{selected && <><header><div><strong>{selected.title}</strong><small>{t("portal.inbox.conversation.channel", { channel: selected.channel })}</small></div><button className={`mode-toggle ${selected.mode}`} onClick={() => setMode(selected.mode === "ai" ? "human" : "ai")}>{selected.mode === "ai" ? t("portal.inbox.conversation.takeControl") : t("portal.inbox.conversation.returnToAi")}</button></header><div className="portal-messages" ref={messagesRef}>{selected.messages?.map((message) => <article key={message.id} className={message.role}><small>{message.sender_name || (message.role === "assistant" ? t("portal.inbox.conversation.agent") : t("portal.inbox.conversation.visitor"))} · {formatWhen(message.created_at, lang)}</small><p>{message.content}</p></article>)}</div>{error && <Alert>{error}</Alert>}<form onSubmit={reply} className="portal-composer"><input name="content" required disabled={selected.mode !== "human" || busy} placeholder={selected.mode === "human" ? t("portal.inbox.conversation.replyPlaceholder") : t("portal.inbox.conversation.takeControlToReply")} /><button disabled={selected.mode !== "human" || busy}>{busy ? <LoaderCircle className="spin" size={18} /> : <Send size={18} />}</button></form></>}</section></div> : <EmptyState icon={<Inbox />} title={t("portal.inbox.empty.title")} description={t("portal.inbox.empty.description")} />}</section></main>;
}
