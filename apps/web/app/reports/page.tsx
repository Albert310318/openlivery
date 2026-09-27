"use client";

import { useEffect, useMemo, useState } from "react";
import { BarChart3, Download, LoaderCircle } from "lucide-react";
import { api, messageFrom } from "@/lib/api";
import { currentClient } from "@/lib/clients";
import { PageHead } from "@/components/ui";
import type { Client, User } from "@/types";

type Report = {
  client_id: string;
  from_datetime: string;
  to_datetime: string;
  generated_at: string;
  total_sales: string;
  paid_orders: number;
  pending_orders: number;
  cancelled_orders: number;
  payment_totals: Record<string, string>;
  origin_totals: Record<string, string>;
  movements: { order_number: string; paid_at: string; origin: string; table_or_customer: string; amount: string; payment_method: string; status: string; confirmed_by: string | null; items: { product_name: string; quantity: number; amount: string }[] }[];
  products: { product_name: string; quantity: number; amount: string }[];
};

const isoDate = (date: Date) => date.toISOString().slice(0, 10);
const money = (value: string | number) => `S/ ${Number(value).toFixed(2)}`;

export default function ReportsPage() {
  const [client, setClient] = useState<Client | null>(null);
  const [from, setFrom] = useState(isoDate(new Date()));
  const [to, setTo] = useState(isoDate(new Date()));
  const [report, setReport] = useState<Report | null>(null);
  const [cashCounted, setCashCounted] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function loadReport(clientId: string, start = from, end = to) {
    const result = await api<Report>(`/restaurants/${clientId}/reports/sales?from=${start}&to=${end}`);
    setReport(result);
  }

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("client_id");
    Promise.all([api<User>("/auth/me"), requested ? api<Client>(`/clients/${requested}`) : currentClient()])
      .then(([, selected]) => { setClient(selected); return loadReport(selected.id); })
      .catch((reason) => setError(messageFrom(reason)))
      .finally(() => setLoading(false));
  }, []);

  const applyPeriod = (period: "today" | "yesterday" | "week" | "month") => {
    const today = new Date();
    let start = new Date(today);
    let end = new Date(today);
    if (period === "yesterday") { start.setDate(today.getDate() - 1); end = new Date(start); }
    if (period === "week") { start.setDate(today.getDate() - 6); }
    if (period === "month") { start = new Date(today.getFullYear(), today.getMonth(), 1); }
    const nextFrom = isoDate(start);
    const nextTo = isoDate(end);
    setFrom(nextFrom); setTo(nextTo);
    if (client) { setLoading(true); loadReport(client.id, nextFrom, nextTo).catch((reason) => setError(messageFrom(reason))).finally(() => setLoading(false)); }
  };

  const refresh = async () => {
    if (!client) return;
    setLoading(true); setError("");
    try { await loadReport(client.id); } catch (reason) { setError(messageFrom(reason)); } finally { setLoading(false); }
  };

  const cashExpected = Number(report?.payment_totals.cash || 0);
  const cashDifference = Number(cashCounted || 0) - cashExpected;
  const paymentRows = useMemo(() => Object.entries(report?.payment_totals || {}), [report]);

  if (loading && !report) return <div className="page"><div className="page-loading"><LoaderCircle className="spin" size={18} /> Cargando reportes…</div></div>;
  return <div className="page reports-page">
    <PageHead eyebrow={client?.name || "RESTAURANTE"} title="Reportes y ventas" description="Solo pagos confirmados por Administrador o Caja." action={client && <a className="button secondary" href={`/api/restaurants/${client.id}/reports/sales.pdf?from=${from}&to=${to}`}><Download size={16} /> Descargar reporte PDF</a>} />
    {error && <div className="alert error">{error}</div>}
    <section className="card report-filters"><div className="row-actions"><button className="button small secondary" onClick={() => applyPeriod("today")}>Hoy</button><button className="button small secondary" onClick={() => applyPeriod("yesterday")}>Ayer</button><button className="button small secondary" onClick={() => applyPeriod("week")}>Esta semana</button><button className="button small secondary" onClick={() => applyPeriod("month")}>Este mes</button></div><div className="form-grid"><label>Desde<input type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label><label>Hasta<input type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label><button className="button primary align-start" onClick={refresh}>Actualizar</button></div></section>
    {report && <><div className="report-summary-grid"><article className="card"><span>Ventas totales</span><strong>{money(report.total_sales)}</strong></article><article className="card"><span>Pedidos cobrados</span><strong>{report.paid_orders}</strong></article><article className="card"><span>Pendientes</span><strong>{report.pending_orders}</strong></article><article className="card"><span>Cancelados</span><strong>{report.cancelled_orders}</strong></article></div><div className="report-columns"><section className="card"><div className="section-heading"><div><h2>Métodos de pago</h2><p>Solo cobros confirmados.</p></div><BarChart3 size={20} /></div>{paymentRows.map(([key, value]) => <div className="report-total-row" key={key}><span>{key === "cash" ? "Efectivo" : key === "yape" ? "Yape" : key === "plin" ? "Plin" : key === "transfer" ? "Transferencia" : key === "card" ? "Tarjeta" : "Otros"}</span><strong>{money(value)}</strong></div>)}</section><section className="card"><div className="section-heading"><div><h2>Cierre de caja</h2><p>Preparación mínima para el efectivo del período.</p></div></div><div className="report-total-row"><span>Efectivo esperado</span><strong>{money(cashExpected)}</strong></div><label>Efectivo contado<input type="number" min="0" step="0.01" value={cashCounted} onChange={(event) => setCashCounted(event.target.value)} /></label><div className="report-total-row"><span>Diferencia</span><strong className={cashDifference < 0 ? "negative" : ""}>{money(cashDifference)}</strong></div></section></div><div className="report-columns"><section className="card"><h2>Detalle de movimientos</h2><div className="table-shell"><table className="data-table"><thead><tr><th>Fecha</th><th>Pedido</th><th>Origen</th><th>Monto</th><th>Método</th><th>Confirmó</th></tr></thead><tbody>{report.movements.map((movement) => <tr key={movement.order_number}><td>{new Date(movement.paid_at).toLocaleString("es-PE")}</td><td><strong>{movement.order_number}</strong><small>{movement.table_or_customer}</small></td><td>{movement.origin}</td><td>{money(movement.amount)}</td><td>{movement.payment_method}</td><td>{movement.confirmed_by || "—"}</td></tr>)}</tbody></table></div></section><section className="card"><h2>Productos vendidos</h2>{report.products.map((item) => <div className="report-total-row" key={item.product_name}><span>{item.quantity} × {item.product_name}</span><strong>{money(item.amount)}</strong></div>)}</section></div></>}
  </div>;
}
