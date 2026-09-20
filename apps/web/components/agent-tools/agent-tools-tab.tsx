"use client";

import { type FormEvent, useEffect, useState } from "react";
import { CalendarDays, CheckCircle2, Pencil, Plus, Server, Trash2, Wrench, Zap } from "lucide-react";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useToast } from "@/components/toast";
import type { AgentTool, CalendarStatus } from "@/types";
import { HttpToolModal } from "./http-tool-modal";
import { McpServerModal } from "./mcp-server-modal";

export function AgentToolsTab({ agentId, clientId, tools, onToolsChange }: {
  agentId: string;
  clientId: string;
  tools: AgentTool[];
  onToolsChange: (tools: AgentTool[]) => void;
}) {
  const t = useT();
  const toast = useToast();
  // null = closed, "new" = create, otherwise the tool being edited.
  const [httpModal, setHttpModal] = useState<AgentTool | "new" | null>(null);
  const [mcpModal, setMcpModal] = useState<AgentTool | "new" | null>(null);
  const [calendar, setCalendar] = useState<CalendarStatus | null>(null);
  const [calendarBusy, setCalendarBusy] = useState(false);

  async function loadCalendar() {
    try { setCalendar(await api<CalendarStatus>(`/calendar/clients/${clientId}`)); }
    catch { setCalendar(null); }
  }

  useEffect(() => { void loadCalendar(); }, [clientId]);

  async function connectCalendar() {
    setCalendarBusy(true);
    try {
      const result = await api<{ authorization_url: string }>(`/calendar/clients/${clientId}/connect`, { method: "POST" });
      window.location.assign(result.authorization_url);
    } catch (err) { toast.error(messageFrom(err)); setCalendarBusy(false); }
  }

  async function disconnectCalendar() {
    if (!confirm(t("tools.calendar.confirmDisconnect"))) return;
    setCalendarBusy(true);
    try {
      setCalendar(await api<CalendarStatus>(`/calendar/clients/${clientId}`, { method: "DELETE" }));
      toast.success(t("tools.calendar.disconnected"));
    } catch (err) { toast.error(messageFrom(err)); }
    finally { setCalendarBusy(false); }
  }

  async function saveCalendar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setCalendarBusy(true);
    try {
      const payload = {
        calendar_id: data.get("calendar_id"),
        timezone: data.get("timezone"),
        workday_start: data.get("workday_start"),
        workday_end: data.get("workday_end"),
        working_days: data.get("working_days"),
        slot_minutes: Number(data.get("slot_minutes")),
        buffer_minutes: Number(data.get("buffer_minutes")),
        min_notice_minutes: Number(data.get("min_notice_minutes")),
        booking_horizon_days: Number(data.get("booking_horizon_days")),
      };
      setCalendar(await api<CalendarStatus>(`/calendar/clients/${clientId}`, { method: "PATCH", body: JSON.stringify(payload) }));
      toast.success(t("tools.calendar.saved"));
    } catch (err) { toast.error(messageFrom(err)); }
    finally { setCalendarBusy(false); }
  }

  function saved(tool: AgentTool) {
    onToolsChange(
      tools.some((item) => item.id === tool.id)
        ? tools.map((item) => (item.id === tool.id ? tool : item))
        : [...tools, tool],
    );
  }

  async function toggle(tool: AgentTool) {
    try {
      const updated = await api<AgentTool>(`/agents/${agentId}/tools/${tool.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !tool.enabled }) });
      onToolsChange(tools.map((item) => (item.id === tool.id ? updated : item)));
    } catch (err) { toast.error(messageFrom(err)); }
  }

  async function remove(tool: AgentTool) {
    if (!confirm(t("tools.confirmDelete", { name: tool.name }))) return;
    try {
      await api(`/agents/${agentId}/tools/${tool.id}`, { method: "DELETE" });
      onToolsChange(tools.filter((item) => item.id !== tool.id));
    } catch (err) { toast.error(messageFrom(err)); }
  }

  return (
    <>
    <section className="panel tools-panel">
      <div className="panel-head">
        <div><h3>{t("tools.heading")}</h3><p>{t("tools.copy")}</p></div>
        <div className="tools-actions">
          <button className="button secondary" onClick={() => setMcpModal("new")}><Server size={15} /> {t("tools.addMcp")}</button>
          <button className="button primary" onClick={() => setHttpModal("new")}><Plus size={15} /> {t("tools.addHttp")}</button>
        </div>
      </div>
      <div className="documents-list">
        {tools.map((tool) => (
          <div className="document-row" key={tool.id}>
            <span className={`document-icon ${tool.enabled ? "" : "error"}`}>{tool.type === "http" ? <Zap size={18} /> : <Server size={18} />}</span>
            <div>
              <strong>{tool.name} <span className="pill">{tool.type === "http" ? `${t("tools.badgeHttp")} · ${tool.http_method}` : t("tools.badgeMcp")}</span></strong>
              <small>{tool.type === "mcp" ? `${tool.url} · ${t("tools.mcpToolCount", { count: tool.cached_tools.length })}` : tool.description || tool.url}</small>
            </div>
            <label className="switch-row compact tool-switch" title={t("tools.enabled")}>
              <input type="checkbox" checked={tool.enabled} onChange={() => toggle(tool)} />
            </label>
            <div className="tools-row-actions">
              <button className="icon-button" onClick={() => (tool.type === "http" ? setHttpModal(tool) : setMcpModal(tool))} title={t("tools.edit")}><Pencil size={15} /></button>
              <button className="icon-button danger-icon" onClick={() => remove(tool)} title={t("tools.delete")}><Trash2 size={15} /></button>
            </div>
          </div>
        ))}
        {!tools.length && (
          <div className="inline-empty slim">
            <Wrench size={22} />
            <div><strong>{t("tools.emptyTitle")}</strong><span>{t("tools.emptyHint")}</span></div>
          </div>
        )}
      </div>
      <HttpToolModal agentId={agentId} tool={httpModal === "new" ? null : httpModal} open={httpModal !== null} onClose={() => setHttpModal(null)} onSaved={saved} />
      <McpServerModal agentId={agentId} tool={mcpModal === "new" ? null : mcpModal} open={mcpModal !== null} onClose={() => setMcpModal(null)} onSaved={saved} />
    </section>

    <section className="panel tools-panel">
      <div className="panel-head">
        <div>
          <h3><CalendarDays size={18} style={{ verticalAlign: "text-bottom", marginRight: 8 }} />{t("tools.calendar.title")}</h3>
          <p>{t("tools.calendar.copy")}</p>
        </div>
        {calendar?.connected ? (
          <div className="tools-actions">
            <span className="pill purple"><CheckCircle2 size={14} /> {t("tools.calendar.connected")}</span>
            <button className="button ghost" onClick={disconnectCalendar} disabled={calendarBusy}>{t("tools.calendar.disconnect")}</button>
          </div>
        ) : (
          <button className="button primary" onClick={connectCalendar} disabled={calendarBusy || calendar?.oauth_configured === false}>
            <CalendarDays size={15} /> {t("tools.calendar.connect")}
          </button>
        )}
      </div>

      {calendar?.oauth_configured === false && <div className="inline-empty slim"><div><strong>{t("tools.calendar.oauthMissing")}</strong><span>{t("tools.calendar.oauthMissingHint")}</span></div></div>}

      {calendar && (
        <form className="settings-form" onSubmit={saveCalendar}>
          <section className="settings-section">
            <div className="settings-copy">
              <h3>{t("tools.calendar.settings")}</h3>
              <p>{t("tools.calendar.settingsCopy")}</p>
            </div>
            <div className="settings-fields">
              <div className="form-grid">
                <label>{t("tools.calendar.calendarId")}<input name="calendar_id" defaultValue={calendar.calendar_id} /></label>
                <label>{t("tools.calendar.timezone")}<input name="timezone" defaultValue={calendar.timezone} /></label>
              </div>
              <div className="form-grid">
                <label>{t("tools.calendar.workdayStart")}<input name="workday_start" type="time" defaultValue={calendar.workday_start} /></label>
                <label>{t("tools.calendar.workdayEnd")}<input name="workday_end" type="time" defaultValue={calendar.workday_end} /></label>
              </div>
              <label>{t("tools.calendar.workingDays")}<input name="working_days" defaultValue={calendar.working_days} /><span className="field-help">{t("tools.calendar.workingDaysHelp")}</span></label>
              <div className="form-grid">
                <label>{t("tools.calendar.slotMinutes")}<input name="slot_minutes" type="number" min="5" max="240" defaultValue={calendar.slot_minutes} /></label>
                <label>{t("tools.calendar.bufferMinutes")}<input name="buffer_minutes" type="number" min="0" max="120" defaultValue={calendar.buffer_minutes} /></label>
              </div>
              <div className="form-grid">
                <label>{t("tools.calendar.minNotice")}<input name="min_notice_minutes" type="number" min="0" defaultValue={calendar.min_notice_minutes} /></label>
                <label>{t("tools.calendar.horizon")}<input name="booking_horizon_days" type="number" min="1" max="365" defaultValue={calendar.booking_horizon_days} /></label>
              </div>
              <div className="form-footer"><button className="button secondary" disabled={calendarBusy}>{t("tools.calendar.save")}</button></div>
            </div>
          </section>
        </form>
      )}

      {calendar?.connected && (
        <div className="documents-list">
          {["consultar_disponibilidad", "crear_cita", "reprogramar_cita", "cancelar_cita"].map((name) => (
            <div className="document-row" key={name}>
              <span className="document-icon"><CalendarDays size={18} /></span>
              <div><strong>{name} <span className="pill">{t("tools.calendar.native")}</span></strong><small>{t(`tools.calendar.tool_${name}`)}</small></div>
            </div>
          ))}
        </div>
      )}
    </section>
    </>
  );
}
