"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, BadgeCheck, Bot, Clock3, ContactRound, Cpu, MessagesSquare, Percent, Sparkles, Trophy, UserPlus, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";
import { api, messageFrom } from "@/lib/api";
import { getOriginatingConversationId } from "@/lib/leads";
import { useLanguage, useT } from "@/lib/i18n";
import { useToast } from "@/components/toast";
import { PageHead, StatusBadge } from "@/components/ui";
import { ListRowsSkeleton, PanelSkeleton, Skeleton } from "@/components/skeleton";
import { BrandLogo } from "@/components/brand";
import type { Agent, AgentSummary, Lead, LeadStatus, User } from "@/types";
import { PublicLanding } from "@/components/public-landing";

type PendingFollowUp = { id: string; name: string | null; interest: string | null; next_follow_up_at: string; is_overdue: boolean };
type Dashboard = { clients: number; active_clients: number; agents: number; active_agents: number; conversations: number; channels: number; connected_channels: number; recent_agents: AgentSummary[]; total_leads: number; leads_by_status: Record<LeadStatus, number>; conversion_rate: number; pending_follow_ups: PendingFollowUp[] };
type DailyPoint = { date: string; count: number };
type TopAgent = { id: string; name: string; conversations: number };
type ModelUsage = { model: string; input_tokens: number; output_tokens: number };
type Metrics = { messages: number; human_conversations: number; by_channel: Record<string, number>; daily_conversations: DailyPoint[]; top_agents: TopAgent[]; tokens_in: number; tokens_out: number; usage_by_model: ModelUsage[] };
const LEAD_STATUSES: LeadStatus[] = ["new", "qualified", "follow_up", "won", "lost"];

export default function HomePage() {
  const t = useT();
  const { lang } = useLanguage();
  const router = useRouter();
  const toast = useToast();
  const [data, setData] = useState<Dashboard | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [recentLeads, setRecentLeads] = useState<Lead[]>([]);
  const [range, setRange] = useState(14);
  const [loadedCore, setLoadedCore] = useState(false);
  const [loadedMetrics, setLoadedMetrics] = useState(false);
  const [authResolved, setAuthResolved] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [currentUser, setCurrentUser] = useState<User | null>(null);

  useEffect(() => {
    api<User>("/auth/me")
      .then((user) => {
        setCurrentUser(user);
        setAuthenticated(true);
      })
      .catch(() => {
        setCurrentUser(null);
        setAuthenticated(false);
      })
      .finally(() => setAuthResolved(true));
  }, []);

  useEffect(() => { if (!authResolved || !authenticated) return; Promise.all([api<Dashboard>("/dashboard"), api<Agent[]>("/agents"), api<Lead[]>("/leads?limit=5")]).then(([d, a, leads]) => { setData(d); setAgents(a); setRecentLeads(leads); }).catch(() => {}).finally(() => setLoadedCore(true)); }, [authResolved, authenticated]);
  useEffect(() => { if (!authResolved || !authenticated) return; setLoadedMetrics(false); api<Metrics>(`/dashboard/metrics?days=${range}`).then(setMetrics).catch(() => {}).finally(() => setLoadedMetrics(true)); }, [authResolved, authenticated, range]);

  if (!authResolved) return <div className="landing-loading" aria-busy="true"><BrandLogo variant="compact" /></div>;
  if (!authenticated) return <PublicLanding />;

  const maxDaily = Math.max(1, ...(metrics?.daily_conversations.map((p) => p.count) ?? [0]));
  const trend = metrics?.daily_conversations ?? [];
  const usage = metrics?.usage_by_model ?? [];
  const maxUsage = Math.max(1, ...usage.map((u) => u.input_tokens + u.output_tokens));
  const maxLeadStatus = Math.max(1, ...LEAD_STATUSES.map((status) => data?.leads_by_status[status] ?? 0));

  async function openLeadConversation(leadId: string) {
    try {
      const conversationId = await getOriginatingConversationId(leadId);
      if (!conversationId) {
        toast.error(t("home.recentLeads.noConversation"));
        return;
      }
      router.push(`/inbox?conversation_id=${encodeURIComponent(conversationId)}`);
    } catch (error) {
      toast.error(messageFrom(error));
    }
  }

  return (
    <div className="page">
      <PageHead eyebrow={t("home.head.eyebrow")} title={t("home.head.title")} description={t("home.head.description")} action={<label className="range-select"><select value={range} onChange={(e) => setRange(Number(e.target.value))}>{[7, 14, 30, 90].map((n) => <option key={n} value={n}>{t("home.range.days", { count: n })}</option>)}</select></label>} />
      {!currentUser?.is_vendiq_admin && <section className="panel next-steps home-next-steps">
        <div className="panel-head"><div><h3>{t("home.nextSteps.title")}</h3><p>{t("home.nextSteps.subtitle")}</p></div></div>
        <ol><li className={loadedCore && data?.clients ? "done" : ""}>{loadedCore ? <span>{data?.clients ? "✓" : "1"}</span> : <span><Skeleton style={{ width: 18, height: 18, borderRadius: 999 }} /></span>}<div><strong>{t("home.nextSteps.step1Title")}</strong><small>{t("home.nextSteps.step1Desc")}</small></div></li><li className={loadedCore && data?.agents ? "done" : ""}>{loadedCore ? <span>{data?.agents ? "✓" : "2"}</span> : <span><Skeleton style={{ width: 18, height: 18, borderRadius: 999 }} /></span>}<div><strong>{t("home.nextSteps.step2Title")}</strong><small>{t("home.nextSteps.step2Desc")}</small></div></li><li><span>3</span><div><strong>{t("home.nextSteps.step3Title")}</strong><small>{t("home.nextSteps.step3Desc")}</small></div></li></ol>
      </section>}
      <section className="metrics-grid commercial-metrics-grid">
        <article className="metric-card"><span className="metric-icon blue"><ContactRound size={20} /></span><div><small>{t("home.commercial.total")}</small><strong>{loadedCore ? data?.total_leads ?? 0 : <Skeleton className="sk-line" style={{ width: 52, height: 28 }} />}</strong><p>{t("home.commercial.totalCaption")}</p></div></article>
        <article className="metric-card"><span className="metric-icon violet"><UserPlus size={20} /></span><div><small>{t("home.commercial.new")}</small><strong>{loadedCore ? data?.leads_by_status.new ?? 0 : <Skeleton className="sk-line" style={{ width: 52, height: 28 }} />}</strong><p>{t("leads.statuses.new")}</p></div></article>
        <article className="metric-card"><span className="metric-icon green"><BadgeCheck size={20} /></span><div><small>{t("home.commercial.qualified")}</small><strong>{loadedCore ? data?.leads_by_status.qualified ?? 0 : <Skeleton className="sk-line" style={{ width: 52, height: 28 }} />}</strong><p>{t("leads.statuses.qualified")}</p></div></article>
        <article className="metric-card"><span className="metric-icon amber"><Clock3 size={20} /></span><div><small>{t("home.commercial.followUp")}</small><strong>{loadedCore ? data?.leads_by_status.follow_up ?? 0 : <Skeleton className="sk-line" style={{ width: 52, height: 28 }} />}</strong><p>{t("leads.statuses.follow_up")}</p></div></article>
        <article className="metric-card"><span className="metric-icon green"><Trophy size={20} /></span><div><small>{t("home.commercial.won")}</small><strong>{loadedCore ? data?.leads_by_status.won ?? 0 : <Skeleton className="sk-line" style={{ width: 52, height: 28 }} />}</strong><p>{t("leads.statuses.won")}</p></div></article>
        <article className="metric-card"><span className="metric-icon violet"><Percent size={20} /></span><div><small>{t("home.commercial.conversion")}</small><strong>{loadedCore ? `${data?.conversion_rate ?? 0}%` : <Skeleton className="sk-line" style={{ width: 52, height: 28 }} />}</strong><p>{t("home.commercial.conversionCaption")}</p></div></article>
      </section>
      <section className="panel pending-follow-ups-panel">
        <div className="panel-head"><div><h3>{t("home.pendingFollowUps.title")}</h3><p>{t("home.pendingFollowUps.subtitle")}</p></div><Link href="/leads" className="text-link">{t("home.pendingFollowUps.viewAll")} <ArrowRight size={15} /></Link></div>
        {!loadedCore ? <ListRowsSkeleton rows={5} /> : data?.pending_follow_ups.length ? <div className="compact-list">{data.pending_follow_ups.map((lead) => <button key={lead.id} className="compact-row pending-follow-up-row" onClick={() => openLeadConversation(lead.id)}><span className={`pending-follow-up-icon ${lead.is_overdue ? "overdue" : "scheduled"}`}><Clock3 size={17} /></span><div><strong>{lead.name || t("leads.unnamed")}</strong><small>{lead.interest || t("home.pendingFollowUps.noInterest")}</small></div><time>{new Date(lead.next_follow_up_at).toLocaleString(lang, { dateStyle: "medium", timeStyle: "short" })}</time><span className={`follow-up-state ${lead.is_overdue ? "overdue" : "scheduled"}`}>{lead.is_overdue ? t("home.pendingFollowUps.overdue") : t("home.pendingFollowUps.scheduled")}</span><ArrowRight size={16} /></button>)}</div> : <div className="inline-empty"><Clock3 size={24} /><div><strong>{t("home.pendingFollowUps.empty")}</strong><span>{t("home.pendingFollowUps.emptyCopy")}</span></div></div>}
      </section>
      <section className="dashboard-row commercial-row">
        <div className="panel">
          <div className="panel-head"><div><h3>{t("home.leadStatus.title")}</h3><p>{t("home.leadStatus.subtitle")}</p></div></div>
          {!loadedCore ? <PanelSkeleton rows={5} /> : <div className="lead-status-bars">{LEAD_STATUSES.map((status) => { const count = data?.leads_by_status[status] ?? 0; return <div className="lead-status-row" key={status}><span>{t(`leads.statuses.${status}`)}</span><div className="usage-track"><div className={`usage-fill lead-status-fill ${status}`} style={{ width: `${Math.round((count / maxLeadStatus) * 100)}%` }} /></div><strong>{count}</strong></div>; })}</div>}
        </div>
        <div className="panel">
          <div className="panel-head"><div><h3>{t("home.recentLeads.title")}</h3><p>{t("home.recentLeads.subtitle")}</p></div><Link href="/leads" className="text-link">{t("home.recentLeads.viewAll")} <ArrowRight size={15} /></Link></div>
          {!loadedCore ? <ListRowsSkeleton rows={5} /> : recentLeads.length ? <div className="compact-list">{recentLeads.map((lead) => <button key={lead.id} className="compact-row recent-lead-row" onClick={() => openLeadConversation(lead.id)}><span className="agent-avatar"><Sparkles size={17} /></span><div><strong>{lead.name || t("leads.unnamed")}</strong><small>{lead.interest || t("home.recentLeads.noInterest")} · {lead.budget || t("home.recentLeads.noBudget")}</small></div><span className={`lead-status-pill ${lead.status}`}>{t(`leads.statuses.${lead.status}`)}</span><ArrowRight size={16} /></button>)}</div> : <div className="inline-empty"><ContactRound size={24} /><div><strong>{t("home.recentLeads.emptyTitle")}</strong><span>{t("home.recentLeads.emptyDescription")}</span></div></div>}
        </div>
      </section>
      <section className="dashboard-row">
        <div className="panel trend-panel">
          <div className="panel-head"><div><h3>{t("home.activity.title")}</h3><p>{t("home.activity.subtitle", { count: range })}</p></div>
            <div className="trend-stats"><span><MessagesSquare size={14} /> {metrics?.messages ?? 0} · {t("home.activity.messages")}</span><span><UserRound size={14} /> {metrics?.human_conversations ?? 0} · {t("home.activity.humanHandled")}</span></div>
          </div>
          {!loadedMetrics ? <PanelSkeleton rows={3} slim /> : trend.some((p) => p.count > 0) ? <>
            <div className="trend-chart">{trend.map((p) => <div key={p.date} className="trend-col" title={`${p.date}: ${p.count}`}><div className="trend-bar" style={{ height: `${Math.round((p.count / maxDaily) * 100)}%` }} /></div>)}</div>
            <div className="trend-legend"><span>{new Date(`${trend[0].date}T00:00:00`).toLocaleDateString("es", { day: "numeric", month: "short" })}</span><span>{new Date(`${trend[trend.length - 1].date}T00:00:00`).toLocaleDateString("es", { day: "numeric", month: "short" })}</span></div>
          </> : <div className="inline-empty slim"><MessagesSquare size={22} /><div><strong>{t("home.activity.empty")}</strong></div></div>}
        </div>
        <div className="panel">
          <div className="panel-head"><div><h3>{t("home.topAgents.title")}</h3><p>{t("home.topAgents.subtitle")}</p></div></div>
          {!loadedMetrics ? <PanelSkeleton rows={3} /> : metrics?.top_agents.length ? <div className="rank-list">{metrics.top_agents.map((agent, index) => <Link href={`/agents/${agent.id}`} key={agent.id} className="rank-row"><span className="rank-num">{index + 1}</span><span className="agent-avatar"><Bot size={16} /></span><strong>{agent.name}</strong><span className="rank-count">{t("home.topAgents.conversations", { count: agent.conversations })}</span></Link>)}</div> : <div className="inline-empty slim"><Bot size={22} /><div><strong>{t("home.topAgents.empty")}</strong></div></div>}
        </div>
      </section>
      <section className="panel">
        <div className="panel-head"><div><h3>{t("home.usage.title")}</h3><p>{t("home.usage.subtitle")}</p></div>
          <div className="trend-stats"><span>↓ {(metrics?.tokens_in ?? 0).toLocaleString("es")} {t("home.usage.in")}</span><span>↑ {(metrics?.tokens_out ?? 0).toLocaleString("es")} {t("home.usage.out")}</span></div>
        </div>
        {!loadedMetrics ? <PanelSkeleton rows={3} /> : usage.length ? <div className="usage-list">{usage.map((item) => { const total = item.input_tokens + item.output_tokens; return <div className="usage-row" key={item.model}><strong>{item.model}</strong><div className="usage-track"><div className="usage-fill" style={{ width: `${Math.round((total / maxUsage) * 100)}%` }} /></div><span className="usage-count">{total.toLocaleString("es")} tok</span></div>; })}</div> : <div className="inline-empty slim"><Cpu size={22} /><div><strong>{t("home.usage.empty")}</strong></div></div>}
      </section>
      <section className="panel">
        <div className="panel-head"><div><h3>{t("home.recentAgents.title")}</h3><p>{t("home.recentAgents.subtitle")}</p></div><Link href="/agents" className="text-link">{t("home.recentAgents.viewAll")} <ArrowRight size={15} /></Link></div>
        {!loadedCore ? <ListRowsSkeleton rows={4} /> : agents.length ? <div className="compact-list">{agents.slice(0, 5).map((agent) => <Link href={`/agents/${agent.id}`} key={agent.id} className="compact-row"><span className="agent-avatar"><Bot size={18} /></span><div><strong>{agent.name}</strong><small>{agent.client.name} · {agent.description || t("common.noDescription")}</small></div><StatusBadge active={agent.is_active} /><ArrowRight size={16} /></Link>)}</div> : <div className="inline-empty"><Bot size={24} /><div><strong>{t("home.recentAgents.emptyTitle")}</strong><span>{t("home.recentAgents.emptyDesc")}</span></div></div>}
      </section>
    </div>
  );
}
