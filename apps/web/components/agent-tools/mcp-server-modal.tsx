"use client";

import { FormEvent, useEffect, useState } from "react";
import { LoaderCircle, Save, Wifi } from "lucide-react";
import { api, messageFrom } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Alert, Modal } from "@/components/ui";
import { useToast } from "@/components/toast";
import type { AgentTool, McpCachedTool } from "@/types";
import { HeadersEditor, headersToDict, type HeaderRow } from "./headers-editor";

type McpTestResult = { ok: boolean; tools: McpCachedTool[] };

export function McpServerModal({ agentId, tool, open, onClose, onSaved }: {
  agentId: string;
  tool: AgentTool | null;
  open: boolean;
  onClose: () => void;
  onSaved: (tool: AgentTool) => void;
}) {
  const t = useT();
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [url, setUrl] = useState("");
  const [transport, setTransport] = useState<"sse" | "streamable_http">("streamable_http");
  const [headerRows, setHeaderRows] = useState<HeaderRow[]>([]);
  const [headersTouched, setHeadersTouched] = useState(false);
  const [showHeaders, setShowHeaders] = useState(false);
  const [tested, setTested] = useState<McpTestResult | null>(null);
  // null: every tool the server exposes; a Set narrows it to those names.
  const [selected, setSelected] = useState<Set<string> | null>(null);
  const [testing, setTesting] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName(tool?.name || ""); setDescription(tool?.description || "");
    setUrl(tool?.url || ""); setTransport(tool?.transport || "streamable_http");
    setHeaderRows([]); setHeadersTouched(false); setShowHeaders(!tool?.has_headers);
    setTested(null); setTesting(false);
    setSelected(tool?.enabled_tools ? new Set(tool.enabled_tools) : null);
  }, [open, tool]);

  // Connection settings changed: the previous test no longer proves anything.
  const invalidateTest = () => setTested(null);
  // Editing without touching connection settings keeps the stored (verified) config.
  const connectionUnchanged = !!tool && url === tool.url && transport === tool.transport && !headersTouched;
  // The list to pick from: the fresh test result, else the stored cache while
  // the connection settings are untouched.
  const available: McpCachedTool[] = tested ? tested.tools : connectionUnchanged && tool ? tool.cached_tools : [];
  const isOn = (name: string) => selected === null || selected.has(name);
  const enabledCount = available.filter((item) => isOn(item.name)).length;
  const canSave = (connectionUnchanged || !!tested) && (available.length === 0 || enabledCount > 0);

  function toggleTool(name: string, on: boolean) {
    const next = new Set(available.filter((item) => isOn(item.name)).map((item) => item.name));
    if (on) next.add(name); else next.delete(name);
    // Every tool checked means "all", so tools the server adds later are exposed too.
    setSelected(next.size === available.length ? null : next);
  }

  async function testConnection() {
    setTesting(true);
    try {
      const result = await api<McpTestResult>(`/agents/${agentId}/tools/test-mcp`, {
        method: "POST",
        body: JSON.stringify({
          url, transport,
          headers: headersTouched ? headersToDict(headerRows) : undefined,
          tool_id: tool && !headersTouched ? tool.id : undefined,
        }),
      });
      setTested(result);
      // Keep a narrowed selection, minus tools the server no longer lists.
      setSelected((prev) => (prev ? new Set(result.tools.filter((item) => prev.has(item.name)).map((item) => item.name)) : null));
      toast.success(t("tools.mcp.testSuccess", { count: result.tools.length }));
    } catch (err) { setTested(null); toast.error(messageFrom(err)); } finally { setTesting(false); }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSave) return;
    setBusy(true);
    const payload: Record<string, unknown> = { name, description, url, transport, enabled_tools: selected ? [...selected] : null };
    if (headersTouched) payload.headers = headersToDict(headerRows);
    try {
      const saved = tool
        ? await api<AgentTool>(`/agents/${agentId}/tools/${tool.id}`, { method: "PATCH", body: JSON.stringify(payload) })
        : await api<AgentTool>(`/agents/${agentId}/tools`, { method: "POST", body: JSON.stringify({ type: "mcp", ...payload }) });
      onSaved(saved);
      onClose();
    } catch (err) { toast.error(messageFrom(err)); } finally { setBusy(false); }
  }

  return (
    <Modal open={open} title={tool ? t("tools.mcp.editTitle") : t("tools.mcp.createTitle")} description={t("tools.mcp.subtitle")} onClose={onClose}>
      <form className="modal-form" onSubmit={submit}>
        <label>{t("tools.form.name")}
          <input required value={name} onChange={(e) => setName(e.target.value)} placeholder="weather_service" pattern="[a-z][a-z0-9]*(_[a-z0-9]+)*" maxLength={24} />
          <span className="field-help">{t("tools.form.nameHint")}</span>
        </label>
        <label>{t("tools.form.description")}
          <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} placeholder={t("tools.form.descriptionPlaceholder")} />
        </label>
        <label>{t("tools.mcp.serverUrl")}
          <input required type="url" value={url} onChange={(e) => { setUrl(e.target.value); invalidateTest(); }} placeholder="https://mcp-server.example.com/mcp" />
        </label>
        <span className="field-label">{t("tools.mcp.transport")}</span>
        <div className="transport-toggle">
          <button type="button" className={transport === "sse" ? "active" : ""} onClick={() => { setTransport("sse"); invalidateTest(); }}>{t("tools.mcp.sse")}</button>
          <button type="button" className={transport === "streamable_http" ? "active" : ""} onClick={() => { setTransport("streamable_http"); invalidateTest(); }}>{t("tools.mcp.streamableHttp")}</button>
        </div>
        <span className="field-label">{t("tools.form.headers")}</span>
        {tool?.has_headers && !showHeaders
          ? <div className="stored-headers"><small>{t("tools.form.headersConfigured")}</small><button type="button" className="button ghost" onClick={() => { setShowHeaders(true); setHeadersTouched(true); invalidateTest(); }}>{t("tools.form.replaceHeaders")}</button></div>
          : <HeadersEditor rows={headerRows} onChange={(rows) => { setHeaderRows(rows); setHeadersTouched(true); invalidateTest(); }} />}
        {available.length > 0
          ? <div className="mcp-discovered">
              <div className="mcp-tools-head">
                <div><strong>{t("tools.mcp.toolsTitle")}</strong><small>{t("tools.mcp.toolsHint")}</small></div>
                <div className="mcp-tools-bulk">
                  <span>{t("tools.mcpToolsEnabled", { enabled: enabledCount, count: available.length })}</span>
                  <button type="button" className="button ghost" onClick={() => setSelected(null)}>{t("tools.mcp.selectAll")}</button>
                  <button type="button" className="button ghost" onClick={() => setSelected(new Set())}>{t("tools.mcp.selectNone")}</button>
                </div>
              </div>
              <div className="mcp-tool-list">
                {available.map((item) => (
                  <label className="mcp-tool-row" key={item.name}>
                    <input type="checkbox" checked={isOn(item.name)} onChange={(e) => toggleTool(item.name, e.target.checked)} />
                    <span>
                      <span className="mcp-tool-name"><code>{item.name}</code>{item.destructive && <span className="pill red">{t("tools.mcp.destructive")}</span>}{item.read_only && <span className="pill green">{t("tools.mcp.readOnly")}</span>}</span>
                      {item.description && <small>{item.description}</small>}
                    </span>
                  </label>
                ))}
              </div>
              {enabledCount === 0 && <Alert type="error">{t("tools.mcp.noneSelected")}</Alert>}
            </div>
          : <Alert type="info">{t("tools.mcp.testNote")}</Alert>}
        <div className="modal-actions mcp-actions">
          <button type="button" className="button secondary" onClick={testConnection} disabled={testing || !url}>
            {testing ? <LoaderCircle className="spin" size={16} /> : <Wifi size={16} />} {testing ? t("tools.mcp.testing") : t("tools.mcp.testConnection")}
          </button>
          <span className="spacer" />
          <button type="button" className="button ghost" onClick={onClose}>{t("tools.form.cancel")}</button>
          <button className="button primary" disabled={busy || !canSave}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}
            {busy ? t("tools.form.saving") : tool ? t("tools.mcp.save") : t("tools.mcp.addServer")}
          </button>
        </div>
      </form>
    </Modal>
  );
}
