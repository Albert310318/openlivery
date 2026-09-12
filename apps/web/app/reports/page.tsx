"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Bot, Coins, Download, LoaderCircle, MessageSquareText, Search, Sparkles } from "lucide-react";
import { EmptyState, PageHead } from "@/components/ui";
import { useToast } from "@/components/toast";
import { api, apiUrl, messageFrom } from "@/lib/api";
import { useLanguage } from "@/lib/i18n";
import type { Agent, Client, CostReport, ReportGroup, ReportReply } from "@/types";

const RANGES = [7, 30, 90] as const;
const PAGE = 25;
const REFRESH_MS = 30_000;

function localISO(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function daysAgoISO(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return localISO(d);
}

function money(value: number): string {
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(5)}`;
  if (value < 1) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

export default function ReportsPage() {
  const { t, lang } = useLanguage();
  const toast = useToast();
  const locale = lang === "es" ? "es" : "en";
  const [range, setRange] = useState<number | "custom">(30);
  const [customFrom, setCustomFrom] = useState(daysAgoISO(29));
  const [customTo, setCustomTo] = useState(daysAgoISO(0));
  const [clientId, setClientId] = useState("");
  const [agentId, setAgentId] = useState("");
  const [model, setModel] = useState("");
  const [query, setQuery] = useState("");
  const [clients, setClients] = useState<Client[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [report, setReport] = useState<CostReport | null>(null);
  const [rows, setRows] = useState<ReportReply[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    api<Client[]>("/clients").then(setClients).catch(() => {});
    api<Agent[]>("/agents").then(setAgents).catch(() => {});
  }, []);

  const from = range === "custom" ? customFrom : daysAgoISO(range - 1);
  const to = range === "custom" ? customTo : daysAgoISO(0);
  const tz = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", []);
  const params = useMemo(() => {
    const p = new URLSearchParams({ from, to, tz });
    if (clientId) p.set("client_id", clientId);
    if (agentId) p.set("agent_id", agentId);
    if (model) p.set("model", model);
    return p;
  }, [from, to, tz, clientId, agentId, model]);
  const replyParams = useMemo(() => {
    const p = new URLSearchParams(params);
    if (query.trim()) p.set("q", query.trim());
    return p;
  }, [params, query]);

  useEffect(() => { setPage(0); }, [replyParams]);

  const load = useCallback(async () => {
    if (!from || !to || from > to) return;
    const [summary, replies] = await Promise.all([
      api<CostReport>(`/reports/costs?${params}`),
      api<{ items: ReportReply[]; total: number }>(`/reports/replies?${replyParams}&limit=${PAGE}&offset=${page * PAGE}`),
    ]);
    setReport(summary);
    setRows(replies.items);
    setTotal(replies.total);
  }, [from, to, params, replyParams, page]);

  useEffect(() => {
    let cancelled = false;
    load().catch((err) => { if (!cancelled) toast.error(messageFrom(err)); }).finally(() => { if (!cancelled) setLoading(false); });
    const timer = setInterval(() => { load().catch(() => {}); }, REFRESH_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [load, toast]);

  const exportCsv = useCallback(async () => {
    setExporting(true);
    try {
      const response = await fetch(apiUrl(`/reports/replies?${replyParams}&format=csv`), { credentials: "include" });
      if (!response.ok) throw new Error("Export failed");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url; link.download = `replies-${from}-${to}.csv`; link.click();
      URL.revokeObjectURL(url);
    } catch (err) { toast.error(messageFrom(err)); } finally { setExporting(false); }
  }, [replyParams, from, to, toast]);

  const fmtInt = (n: number) => n.toLocaleString(locale);
  const fmtWhen = (iso: string) => new Date(iso).toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
  const dayLabel = (iso: string) => new Date(`${iso}T00:00`).toLocaleDateString(locale, { day: "numeric", month: "short" });
  const channelLabel = (value: string | null) => {
    if (!value) return "—";
    if (value === "playground") return t("inbox.channelPlayground");
    if (value === "whatsapp") return t("inbox.channelWhatsapp");
    if (value === "whatsapp_cloud") return t("inbox.channelWhatsappCloud");
    if (value === "instagram") return t("social.instagram.title");
    if (value === "messenger") return t("social.messenger.title");
    if (value === "widget") return t("inbox.channelWidget");
    return value;
  };
  const agentsForClient = clientId ? agents.filter((a) => a.client_id === clientId) : agents;
  const models = report?.by_model.map((m) => m.name) ?? [];
  const totalCost = report?.totals.cost_usd ?? 0;
  const estimatedReplies = rows.filter((r) => r.estimated).length;
  const pageCount = Math.max(1, Math.ceil(total / PAGE));
  const maxDay = Math.max(0.000001, ...(report?.by_day.map((d) => d.cost_usd) ?? [0]));

  const groupTable = (items: ReportGroup[], head: string, emptyName: string) => (
    <div className="table-shell"><table className="data-table">
      <thead><tr><th>{head}</th><th>{t("reports.cols.replies")}</th><th>{t("reports.cols.tokensIn")}</th><th>{t("reports.cols.tokensOut")}</th><th>{t("reports.cols.cost")}</th><th>{t("reports.cols.share")}</th></tr></thead>
      <tbody>{items.map((item) => (
        <tr key={item.id ?? "none"}>
          <td><strong>{item.id ? item.name || "—" : emptyName}</strong></td>
          <td>{fmtInt(item.replies)}</td><td>{fmtInt(item.input_tokens)}</td><td>{fmtInt(item.output_tokens)}</td>
          <td><strong>{money(item.cost_usd)}</strong></td>
          <td><small className="muted">{totalCost ? Math.round((item.cost_usd / totalCost) * 100) : 0}%</small></td>
        </tr>
      ))}</tbody>
    </table></div>
  );

  return <div className="page">
    <PageHead eyebrow={t("reports.head.eyebrow")} title={t("reports.head.title")} description={t("reports.head.description")} />

    <div className="toolbar filters">
      <div className="report-ranges">
        {RANGES.map((value) => <button key={value} type="button" className={value === range ? "active" : ""} onClick={() => setRange(value)}>
          {value === 7 ? t("reports.filters.range7") : value === 30 ? t("reports.filters.range30") : t("reports.filters.range90")}
        </button>)}
        <button type="button" className={range === "custom" ? "active" : ""} onClick={() => setRange("custom")}>{t("reports.filters.custom")}</button>
      </div>
      {range === "custom" && <div className="report-custom-range">
        <input type="date" value={customFrom} max={customTo || undefined} onChange={(e) => setCustomFrom(e.target.value)} aria-label={t("reports.filters.from")} />
        <span>{t("reports.filters.to")}</span>
        <input type="date" value={customTo} min={customFrom || undefined} max={daysAgoISO(0)} onChange={(e) => setCustomTo(e.target.value)} aria-label={t("reports.filters.to")} />
      </div>}
      <label className="filter-select">{t("reports.cols.client")}<select value={clientId} onChange={(e) => { setClientId(e.target.value); setAgentId(""); }}><option value="">{t("reports.filters.allClients")}</option>{clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
      <label className="filter-select">{t("reports.cols.agent")}<select value={agentId} onChange={(e) => setAgentId(e.target.value)}><option value="">{t("reports.filters.allAgents")}</option>{agentsForClient.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label className="filter-select">{t("reports.cols.model")}<select value={model} onChange={(e) => setModel(e.target.value)}><option value="">{t("reports.filters.allModels")}</option>{models.map((m) => <option key={m} value={m}>{m}</option>)}</select></label>
    </div>

    {loading || !report ? <p className="muted" style={{ padding: "24px 0" }}>{t("reports.loading")}</p> : (
      <>
        <section className="metrics-grid">
          <article className="metric-card"><span className="metric-icon green"><Coins size={20} /></span><div><small>{t("reports.tiles.cost")}</small><strong>{money(report.totals.cost_usd)}</strong><p>{t("reports.tiles.costHint", { replies: fmtInt(report.totals.replies) })}</p></div></article>
          <article className="metric-card"><span className="metric-icon blue"><Bot size={20} /></span><div><small>{t("reports.tiles.avg")}</small><strong>{money(report.totals.avg_cost_per_reply_usd)}</strong><p>{t("reports.tiles.avgHint", { tokens: fmtInt(report.totals.input_tokens + report.totals.output_tokens) })}</p></div></article>
          <article className="metric-card"><span className="metric-icon violet"><MessageSquareText size={20} /></span><div><small>{t("reports.tiles.conversations")}</small><strong>{fmtInt(report.totals.conversations)}</strong><p>{t("reports.tiles.conversationsHint")}</p></div></article>
          <article className="metric-card"><span className="metric-icon amber"><Sparkles size={20} /></span><div><small>{t("reports.tiles.estimated")}</small><strong>{fmtInt(estimatedReplies)}</strong><p>{t("reports.tiles.estimatedHint")}</p></div></article>
        </section>

        {report.totals.replies === 0 ? <EmptyState icon={<Coins />} title={t("reports.emptyTitle")} description={t("reports.emptyBody")} /> : (
          <>
            <section className="section-block">
              <div className="section-heading"><div><h2>{t("reports.sections.byDay")}</h2></div></div>
              <div className="panel" style={{ padding: "16px 12px 6px" }}>
                <div className="report-chart" role="img" aria-label={t("reports.sections.byDay")}>
                  {report.by_day.map((day, i) => <div key={day.date} className="report-chart-group">
                    <div className="report-chart-tip"><strong>{dayLabel(day.date)}</strong><span>{money(day.cost_usd)} · {fmtInt(day.replies)}</span></div>
                    <div className="report-chart-bars"><i style={{ height: `${(day.cost_usd / maxDay) * 100}%`, background: "var(--purple)" }} /></div>
                    <small>{i % Math.max(1, Math.ceil(report.by_day.length / 8)) === 0 ? dayLabel(day.date) : " "}</small>
                  </div>)}
                </div>
              </div>
            </section>

            <section className="section-block">
              <div className="section-heading"><div><h2>{t("reports.sections.byClient")}</h2></div></div>
              {groupTable(report.by_client, t("reports.cols.client"), t("reports.noClient"))}
            </section>
            <section className="section-block">
              <div className="section-heading"><div><h2>{t("reports.sections.byAgent")}</h2></div></div>
              {groupTable(report.by_agent, t("reports.cols.agent"), "—")}
            </section>
            <section className="section-block">
              <div className="section-heading"><div><h2>{t("reports.sections.byModel")}</h2></div></div>
              {groupTable(report.by_model, t("reports.cols.model"), "—")}
            </section>

            <section className="section-block">
              <div className="section-heading"><div><h2>{t("reports.sections.replies")}</h2><p>{t("reports.sections.repliesHint")}</p></div></div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", marginBottom: 16 }}>
                <label className="search-box" style={{ height: 38 }}><Search size={16} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("reports.filters.search")} /></label>
                <button type="button" className="button secondary" onClick={exportCsv} disabled={exporting || rows.length === 0} style={{ marginLeft: "auto" }}>
                  {exporting ? <LoaderCircle size={15} className="spin" /> : <Download size={15} />} {t("reports.export")}
                </button>
              </div>
              <div className="table-shell" style={{ overflowX: "auto" }}><table className="data-table" style={{ whiteSpace: "nowrap" }}>
                <thead><tr>
                  <th>{t("reports.cols.date")}</th><th>{t("reports.cols.conversation")}</th><th>{t("reports.cols.contact")}</th><th>{t("reports.cols.client")}</th><th>{t("reports.cols.agent")}</th><th>{t("reports.cols.channel")}</th>
                  <th>{t("reports.cols.model")}</th><th>{t("reports.cols.servedBy")}</th><th>{t("reports.cols.duration")}</th><th>{t("reports.cols.tokensIn")}</th><th>{t("reports.cols.tokensOut")}</th><th>{t("reports.cols.cost")}</th>
                </tr></thead>
                <tbody>{rows.map((row) => (
                  <tr key={row.id}>
                    <td>{fmtWhen(row.created_at)}</td>
                    <td>{row.conversation_id ? <code style={{ fontSize: 12 }}>{row.conversation_id.slice(0, 8)}</code> : "—"}</td>
                    <td>{row.contact_name || (row.conversation_id ? t("reports.unknown") : "—")}</td>
                    <td>{row.client_name || "—"}</td><td>{row.agent_name || "—"}</td><td>{channelLabel(row.channel)}</td>
                    <td>{row.model}</td>
                    <td>{row.served_by || "—"}</td>
                    <td>{row.duration_ms == null ? "—" : `${(row.duration_ms / 1000).toFixed(row.duration_ms < 10_000 ? 1 : 0)} s`}</td>
                    <td>{fmtInt(row.input_tokens)}</td><td>{fmtInt(row.output_tokens)}</td>
                    <td><strong>{money(row.cost_usd)}</strong>{row.estimated && <small className="muted"> · {t("reports.estimatedMark")}</small>}</td>
                  </tr>
                ))}</tbody>
              </table></div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 10, marginTop: 14 }}>
                <small className="muted">{t("reports.pageOf", { from: fmtInt(total ? page * PAGE + 1 : 0), to: fmtInt(Math.min(total, (page + 1) * PAGE)), total: fmtInt(total) })}</small>
                <button type="button" className="button secondary" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>{t("reports.prev")}</button>
                <button type="button" className="button secondary" onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))} disabled={page >= pageCount - 1}>{t("reports.next")}</button>
              </div>
            </section>
          </>
        )}
      </>
    )}
  </div>;
}
