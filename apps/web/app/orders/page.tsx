"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChefHat, LoaderCircle, Pencil, Plus, Trash2, Truck, Utensils } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { api, messageFrom } from "@/lib/api";
import { currentClient } from "@/lib/clients";
import { Modal, PageHead } from "@/components/ui";
import type { Client, DeliveryNotification, DeliveryOrder, RestaurantMenuCategory, RestaurantOrder, RestaurantOrderItem, RestaurantTableAccount, User } from "@/types";

type CartLine = { product_id: string; quantity: number; variant_ids: string[]; extra_ids: string[]; observations: string };
type CashierLine = { key: string; productName: string; quantity: number; subtotal: number; variants: string[]; extras: string[]; observations: string | null };
type PaidTableAccount = { id: string; tableNumber: string; orders: RestaurantOrder[]; total: number; paidAt: string | null; paymentMethod: string | null };
type PaymentMethod = "cash" | "yape" | "plin" | "transfer" | "card" | "other";
type PaymentDialog = { order: RestaurantOrder; tableNumber: string; total: number | string };
const paymentMethods: { value: PaymentMethod; label: string }[] = [
  { value: "cash", label: "Efectivo" },
  { value: "yape", label: "Yape" },
  { value: "plin", label: "Plin" },
  { value: "transfer", label: "Transferencia" },
  { value: "card", label: "Tarjeta" },
  { value: "other", label: "Otro" },
];
const paymentMethodLabels: Record<PaymentMethod, string> = { cash: "Efectivo", yape: "Yape", plin: "Plin", transfer: "Transferencia", card: "Tarjeta", other: "Otro" };
const statusLabels: Record<string, string> = { pending_payment: "Pendiente de pago", confirmed: "Enviado a cocina", preparing: "En preparación", ready: "Listo para servir", served: "✓ Entregado en mesa", on_the_way: "En camino", delivered: "Entregado", closed: "Cerrado", cancelled: "Cancelado" };
const roleLabels: Record<string, string> = { waiter: "Mesero", kitchen: "Cocina", cashier: "Caja", delivery: "Delivery" };

function money(value: number | string) { return `S/ ${Number(value).toFixed(2)}`; }
function paymentMethodLabel(method: string | null) { return method ? paymentMethodLabels[method as PaymentMethod] || method : null; }
function paymentVerificationLabel(status: string) { return status === "confirmed" ? "Pago confirmado" : status === "review" ? "Pago por verificar" : status === "rejected" ? "Pago rechazado" : "Pendiente de pago"; }
function activeItems(order: RestaurantOrder) { return order.items.filter((item) => !item.is_cancelled); }
function kitchenPostState(order: RestaurantOrder) {
  if (order.modality === "dine_in") return order.order_status === "served" ? "Entregado en mesa" : order.order_status === "closed" ? "Cuenta cerrada" : "Esperando mesero";
  if (order.modality === "pickup") return order.order_status === "delivered" ? "Recogido" : "Esperando recojo";
  return order.order_status === "on_the_way" ? "En camino" : order.order_status === "delivered" ? "Entregado" : "Pendiente de delivery";
}
function cashierLines(orders: RestaurantOrder[]): CashierLine[] {
  const lines = new Map<string, CashierLine>();
  orders.forEach((order) => activeItems(order).forEach((item) => {
    const variants = item.variants.map((variant) => variant.name);
    const extras = item.extras.map((extra) => extra.name);
    const key = [item.product_name, variants.join("|"), extras.join("|"), item.observations || ""].join("::");
    const current = lines.get(key);
    if (current) {
      current.quantity += item.quantity;
      current.subtotal += Number(item.line_subtotal);
      return;
    }
    lines.set(key, { key, productName: item.product_name, quantity: item.quantity, subtotal: Number(item.line_subtotal), variants, extras, observations: item.observations });
  }));
  return Array.from(lines.values());
}

function PaymentModal({ dialog, method, error, saving, onClose, onSelect, onConfirm }: { dialog: PaymentDialog | null; method: PaymentMethod | null; error: string; saving: boolean; onClose: () => void; onSelect: (value: PaymentMethod) => void; onConfirm: () => void }) {
  return <Modal open={Boolean(dialog)} title="Confirmar pago" description="Selecciona el método de pago" onClose={saving ? () => {} : onClose}>
    {dialog && <div className="modal-form cashier-payment-form">
      <div className="cashier-payment-summary"><div><span>Mesa</span><strong>{dialog.tableNumber}</strong></div><div><span>Total de la cuenta</span><strong>{money(dialog.total)}</strong></div></div>
      <div className="cashier-payment-options" role="radiogroup" aria-label="Método de pago">
        {paymentMethods.map((payment) => <label className="cashier-payment-option" key={payment.value}>
          <input type="radio" name="cashier-payment-method" value={payment.value} checked={method === payment.value} onChange={() => onSelect(payment.value)} disabled={saving} />
          <span>{payment.label}</span>
        </label>)}
      </div>
      {error && <div className="alert error" role="alert">{error}</div>}
      <div className="modal-actions">
        <button type="button" className="button secondary" onClick={onClose} disabled={saving}>Cancelar</button>
        <button type="button" className="button primary" onClick={onConfirm} disabled={!method || saving}>{saving ? <><LoaderCircle className="spin" size={16} /> Procesando...</> : "Confirmar pago"}</button>
      </div>
    </div>}
  </Modal>;
}

export default function OrdersPage() {
  const params = useSearchParams();
  const [client, setClient] = useState<Client | null>(null);
  const [role, setRole] = useState("");
  const [userId, setUserId] = useState("");
  const [orders, setOrders] = useState<RestaurantOrder[]>([]);
  const [deliveryOrders, setDeliveryOrders] = useState<DeliveryOrder[]>([]);
  const [deliveryNotifications, setDeliveryNotifications] = useState<DeliveryNotification[]>([]);
  const [tableAccounts, setTableAccounts] = useState<RestaurantTableAccount[]>([]);
  const [menu, setMenu] = useState<RestaurantMenuCategory[]>([]);
  const [filter, setFilter] = useState("all");
  const [cashierView, setCashierView] = useState<"pending" | "paid">("pending");
  const [table, setTable] = useState("");
  const [productId, setProductId] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [variantIds, setVariantIds] = useState<string[]>([]);
  const [extraIds, setExtraIds] = useState<string[]>([]);
  const [observations, setObservations] = useState("");
  const [cart, setCart] = useState<CartLine[]>([]);
  const [isVendiqAdmin, setIsVendiqAdmin] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [paymentDialog, setPaymentDialog] = useState<PaymentDialog | null>(null);
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod | null>(null);
  const [paymentError, setPaymentError] = useState("");
  const composerRef = useRef<HTMLElement | null>(null);
  const tableInputRef = useRef<HTMLInputElement | null>(null);
  const viewAsRole = params.get("view_as_role");
  const viewAsStaffId = params.get("view_as_staff_id");
  const isAdminPanelView = Boolean(viewAsRole && viewAsStaffId);
  const isGeneralAdminClientView = isVendiqAdmin && Boolean(params.get("client_id"));
  const panelQuery = isAdminPanelView ? `?view_as_role=${encodeURIComponent(viewAsRole || "")}&view_as_staff_id=${encodeURIComponent(viewAsStaffId || "")}` : "";

  const products = useMemo(() => menu.flatMap((category) => category.products.filter((product) => product.is_available)), [menu]);
  const selectedProduct = products.find((product) => product.id === productId);
  const openTableNumbers = useMemo(() => tableAccounts.map((account) => account.table_number), [tableAccounts]);
  function cartLineSubtotal(line: CartLine) {
    const product = products.find((item) => item.id === line.product_id);
    if (!product) return 0;
    const variants = product.variants.filter((option) => line.variant_ids.includes(option.id)).reduce((sum, option) => sum + Number(option.price_delta || option.price || 0), 0);
    const extras = product.extras.filter((option) => line.extra_ids.includes(option.id)).reduce((sum, option) => sum + Number(option.price || 0), 0);
    return (Number(product.price) + variants + extras) * line.quantity;
  }

  const cartSubtotal = useMemo(() => cart.reduce((total, line) => total + cartLineSubtotal(line), 0), [cart, products]);

  useEffect(() => { if (!productId && products[0]) setProductId(products[0].id); }, [productId, products]);

  async function load(clientId: string) {
    const me = await api<User>("/auth/me");
    setIsVendiqAdmin(me.is_vendiq_admin);
    const effectiveRole = isAdminPanelView ? viewAsRole || "" : me.is_vendiq_admin ? "admin" : me.restaurant_role || (me.role === "admin" ? "admin" : "");
    setRole(effectiveRole);
    setUserId(me.id);
    const nextMenu = await api<RestaurantMenuCategory[]>(`/restaurants/${clientId}/menu`);
    setMenu(nextMenu);
    if (effectiveRole === "waiter") {
      setTableAccounts(await api<RestaurantTableAccount[]>(`/restaurants/${clientId}/table-accounts${panelQuery}`));
      setOrders([]);
      return;
    }
    if (effectiveRole === "cashier") {
      const [nextOrders, nextTableAccounts] = await Promise.all([
        api<RestaurantOrder[]>(`/restaurants/${clientId}/orders${panelQuery}`),
        api<RestaurantTableAccount[]>(`/restaurants/${clientId}/table-accounts${panelQuery}`),
      ]);
      setOrders(nextOrders);
      setTableAccounts(nextTableAccounts);
      return;
    }
    if (effectiveRole === "delivery") {
      setDeliveryOrders(await api<DeliveryOrder[]>(`/restaurants/${clientId}/delivery-orders`));
      setOrders([]);
      setTableAccounts([]);
      return;
    }
    if (effectiveRole === "admin") setDeliveryNotifications(await api<DeliveryNotification[]>(`/restaurants/${clientId}/delivery-notifications`));
    setOrders(await api<RestaurantOrder[]>(`/restaurants/${clientId}/orders${panelQuery}`));
    setTableAccounts([]);
  }

  useEffect(() => {
    const requested = params.get("client_id");
    api<User>("/auth/me").then((me) => {
      const ownedId = me.restaurant_client_id || requested;
      if (me.restaurant_role && ownedId) {
        const ownedClient = { id: ownedId, name: me.restaurant_client_name || "Restaurante", industry: "restaurante" } as Client;
        setClient(ownedClient);
        return load(ownedId);
      }
      return (requested ? api<Client>(`/clients/${requested}`) : currentClient()).then((next) => { setClient(next); return load(next.id); });
    }).catch((reason) => setError(messageFrom(reason)));
  }, [params]);

  async function refresh() { if (client) await load(client.id); }
  async function updateOrder(orderId: string, nextStatus: string) {
    if (!client) return;
    setSaving(true); setError("");
    try { await api(`/restaurants/${client.id}/orders/${orderId}/status`, { method: "PATCH", body: JSON.stringify({ status: nextStatus }) }); await refresh(); } catch (reason) { setError(messageFrom(reason)); } finally { setSaving(false); }
  }
  async function retryDeliveryNotification(orderId: string) {
    if (!client || saving) return;
    setSaving(true); setError("");
    try { await api(`/restaurants/${client.id}/orders/${orderId}/delivery-notification/retry`, { method: "POST" }); await refresh(); }
    catch (reason) { setError(messageFrom(reason)); }
    finally { setSaving(false); }
  }
  function pay(order: RestaurantOrder, total: number | string = order.total, tableNumber = order.table_number || "Sin mesa") {
    setPaymentError("");
    setPaymentMethod(null);
    setPaymentDialog({ order, total, tableNumber });
  }
  function payTable(account: RestaurantTableAccount) {
    if (Number(account.total) <= 0) return;
    const payableOrder = account.orders.find((order) => order.payment_status !== "confirmed" && order.order_status !== "cancelled" && Number(order.total) > 0 && activeItems(order).length > 0);
    if (payableOrder) pay(payableOrder, account.total, `Mesa ${account.table_number}`);
  }
  async function confirmPayment() {
    if (!client || !paymentDialog || !paymentMethod || saving) return;
    setSaving(true);
    setPaymentError("");
    try {
      await api(`/restaurants/${client.id}/orders/${paymentDialog.order.id}/payment`, { method: "POST", body: JSON.stringify({ payment_method: paymentMethod }) });
    } catch (reason) {
      setPaymentError(messageFrom(reason));
      setSaving(false);
      return;
    }
    setPaymentDialog(null);
    setPaymentMethod(null);
    setPaymentError("");
    try { await refresh(); } catch (reason) { setError(messageFrom(reason)); } finally { setSaving(false); }
  }
  async function decideRestaurantPayment(order: RestaurantOrder, decision: "confirm" | "reject") {
    if (!client || saving || role !== "admin") return;
    setSaving(true); setError("");
    try {
      await api(`/restaurants/${client.id}/orders/${order.id}/payment${decision === "reject" ? "/reject" : ""}`, {
        method: "POST",
        body: JSON.stringify(decision === "reject" ? { reason: "Comprobante no confirmado por el administrador" } : {}),
      });
      await refresh();
    } catch (reason) { setError(messageFrom(reason)); } finally { setSaving(false); }
  }
  function addLine() {
    if (!selectedProduct || quantity < 1) return;
    setCart((current) => [...current, { product_id: selectedProduct.id, quantity, variant_ids: variantIds, extra_ids: extraIds, observations }]);
    setVariantIds([]); setExtraIds([]); setObservations(""); setQuantity(1);
  }
  async function createWaiterOrder() {
    if (!client || !table.trim() || !cart.length) return;
    setSaving(true); setError("");
    try { await api(`/restaurants/${client.id}/orders`, { method: "POST", body: JSON.stringify({ table_number: table.trim(), items: cart, idempotency_key: `ui-${Date.now()}-${Math.random()}` }) }); setCart([]); await refresh(); } catch (reason) { setError(messageFrom(reason)); } finally { setSaving(false); }
  }
  async function editItem(order: RestaurantOrder, item: RestaurantOrderItem) {
    if (!client || !canEdit(order)) return;
    const nextQuantity = window.prompt("Cantidad", String(item.quantity));
    if (nextQuantity === null) return;
    const parsed = Number(nextQuantity);
    if (!Number.isInteger(parsed) || parsed < 1) { setError("La cantidad debe ser un número entero mayor que cero."); return; }
    try { await api(`/restaurants/${client.id}/orders/${order.id}/items/${item.id}`, { method: "PATCH", body: JSON.stringify({ quantity: parsed, observations: item.observations }) }); await refresh(); } catch (reason) { setError(messageFrom(reason)); }
  }
  async function cancelItem(order: RestaurantOrder, item: RestaurantOrderItem) {
    if (!client || !canEdit(order) || !window.confirm(`¿Cancelar ${item.product_name}?`)) return;
    try { await api(`/restaurants/${client.id}/orders/${order.id}/items/${item.id}`, { method: "DELETE" }); await refresh(); } catch (reason) { setError(messageFrom(reason)); }
  }
  function canEdit(order: RestaurantOrder) { return !isAdminPanelView && role === "waiter" && order.created_by_user_id === userId && order.payment_status !== "confirmed" && ["pending_payment", "confirmed"].includes(order.order_status); }
  function canShowItemActions(order: RestaurantOrder) { return !isAdminPanelView && role === "waiter" && order.created_by_user_id === userId; }
  function adminPanelBanner() {
    if (!isAdminPanelView && role !== "admin" && !(role === "admin" && filter === "pending_payment" && pendingTableAccounts.length)) return null;
    return <>
      {isAdminPanelView && <div className="alert admin-panel-banner"><strong>Vista de administrador · {roleLabels[viewAsRole || ""] || viewAsRole}</strong><Link className="button small secondary" href={`/personal?client_id=${encodeURIComponent(client?.id || "")}`}>Volver al panel administrativo</Link></div>}
      {role === "admin" && deliveryNotifications.filter((item) => item.status !== "sent").map((item) => <section className="card delivery-notification-alert" key={item.id}><div className="section-heading"><div><h2>Notificación a Delivery pendiente/error</h2><p>Pedido {item.order_number} · Destino {item.recipient} · {item.error_message || "Pendiente de confirmación del proveedor"}</p></div><button className="button secondary" disabled={saving} onClick={() => retryDeliveryNotification(item.order_id)}>Reintentar notificación</button></div></section>)}
      {role === "admin" && filter === "pending_payment" && pendingTableAccounts.length > 0 && <section className="card pending-table-review"><div className="section-heading"><div><h2>Cuentas de mesa pendientes</h2><p>Pedidos servidos que aún deben cobrarse en Caja.</p></div><span className="pill">{pendingTableAccounts.length}</span></div><div className="cashier-tables-grid">{pendingTableAccounts.map((account) => <article className="cashier-table-card" key={account.table}><header className="cashier-table-header"><div className="cashier-table-title"><span className="table-kicker">PAGO PENDIENTE</span><h2>Mesa {account.table}</h2></div><div className="cashier-table-total"><span>Total pendiente</span><strong>{money(account.total)}</strong></div></header><div className="cashier-table-orders">{cashierLines(account.orders).map((line) => <div className="cashier-line-item" key={line.key}><span><strong>{line.quantity} × {line.productName}</strong>{line.observations && <small>{line.observations}</small>}</span><span>{money(line.subtotal)}</span></div>)}</div><footer className="cashier-table-footer"><span>Estado: {account.orders.some((order) => order.order_status === "served") ? "Entregada en mesa" : "Operativa"}</span><span>Pago: Pendiente · Caja cobra</span></footer></article>)}</div></section>}
    </>;
  }
  function selectTable(tableNumber: string) {
    setTable(tableNumber);
    requestAnimationFrame(() => {
      composerRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      tableInputRef.current?.focus({ preventScroll: true });
    });
  }

  const reviewOrders = orders.filter((order) => order.payment_review_status === "review");
  const rawVisible = orders.filter((order) => filter === "all" || (filter === "payment_review" ? order.payment_review_status === "review" : filter === "paid" ? order.payment_status === "confirmed" : filter === "table" ? order.modality === "dine_in" : filter === "delivery" ? order.modality === "delivery" : filter === "kitchen" ? ["confirmed", "preparing"].includes(order.order_status) : order.order_status === filter));
  const visible = filter === "pending_payment" && role === "admin" ? rawVisible.filter((order) => order.modality !== "dine_in") : rawVisible;
  const kitchenOrders = useMemo(() => orders.filter((order) => ["confirmed", "preparing"].includes(order.order_status)).sort((left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime()), [orders]);
  const kitchenRecentOrders = useMemo(() => {
    return orders.filter((order) => order.ready_at && ["ready", "on_the_way", "delivered", "served", "closed"].includes(order.order_status)).sort((left, right) => new Date(right.ready_at || 0).getTime() - new Date(left.ready_at || 0).getTime()).slice(0, 5);
  }, [orders]);
  const kitchenTables = useMemo(() => {
    const grouped = new Map<string, { tableNumber: string | null; orders: RestaurantOrder[] }>();
    kitchenOrders.forEach((order) => {
      const key = order.table_number || "__without_table__";
      const group = grouped.get(key) || { tableNumber: order.table_number, orders: [] };
      group.orders.push(order);
      grouped.set(key, group);
    });
    return Array.from(grouped.values());
  }, [kitchenOrders]);
  const cashierPendingAccounts = useMemo(() => tableAccounts.filter((account) => Number(account.total) > 0 && account.orders.some((order) => order.order_status !== "cancelled" && order.payment_status !== "confirmed" && activeItems(order).length > 0)), [tableAccounts]);
  const cashierPaidAccounts = useMemo(() => {
    const grouped = new Map<string, PaidTableAccount>();
    orders.filter((order) => order.modality === "dine_in" && order.payment_status === "confirmed" && order.order_status !== "cancelled" && Number(order.total) > 0).forEach((order) => {
      const key = order.table_account_id || `table:${order.table_number || "without-table"}`;
      const current = grouped.get(key) || { id: key, tableNumber: order.table_number || "—", orders: [], total: 0, paidAt: null, paymentMethod: null };
      current.orders.push(order);
      current.total += Number(order.total);
      if (!current.paymentMethod && order.payment_method) current.paymentMethod = order.payment_method;
      if (!current.paidAt || (order.payment_confirmed_at && new Date(order.payment_confirmed_at).getTime() > new Date(current.paidAt).getTime())) current.paidAt = order.payment_confirmed_at;
      grouped.set(key, current);
    });
    return Array.from(grouped.values()).sort((left, right) => (right.paidAt || "").localeCompare(left.paidAt || ""));
  }, [orders]);
  const pendingTableAccounts = (() => {
    const grouped = new Map<string, { table: string; orders: RestaurantOrder[]; total: number }>();
    orders.filter((order) => order.modality === "dine_in" && order.payment_status !== "confirmed" && order.order_status !== "cancelled" && activeItems(order).length > 0).forEach((order) => {
      const key = order.table_account_id || `table:${order.table_number || "without-table"}`;
      const current = grouped.get(key) || { table: order.table_number || "—", orders: [], total: 0 };
      current.orders.push(order);
      current.total += Number(order.total);
      grouped.set(key, current);
    });
    return Array.from(grouped.values()).sort((left, right) => left.table.localeCompare(right.table));
  })();
  const administrativeBackLink = isGeneralAdminClientView && client
    ? <Link className="button small secondary" href={`/clients/${encodeURIComponent(client.id)}`}>← Volver a {client.name}</Link>
    : undefined;
  const pageDescription = isGeneralAdminClientView ? "Vista administrativa" : "Operación de pedidos, cocina, caja y delivery.";
  function kitchenTime(value: string) { return new Date(value).toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit" }); }
  if (error && !client) return <div className="page"><PageHead eyebrow="RESTAURANTE" title="Pedidos" description="Operación de pedidos, cocina, caja y delivery." /><div className="alert error">{error}</div></div>;
  if (!role) return <div className="page"><PageHead eyebrow={client?.name || "RESTAURANTE"} title="Pedidos" description="Cargando operación…" /><div className="page-loading">Cargando mesas…</div></div>;

  if (role === "waiter") return <div className="page waiter-page">
    <PageHead eyebrow={client?.name || "RESTAURANTE"} title="Mesas" description="Registra pedidos y sigue el estado de cada mesa." />
    {adminPanelBanner()}
    {error && <div className="alert error">{error}</div>}
    {!isAdminPanelView && <section ref={composerRef} className="card waiter-composer">
      <div className="waiter-section-heading"><div><h3><Utensils size={18} /> Nuevo pedido</h3><p>Agrega una o varias líneas a una mesa nueva o abierta.</p></div></div>
      <div className="form-grid waiter-order-form">
        <label>Mesa<input ref={tableInputRef} list="open-table-numbers" inputMode="numeric" value={table} onChange={(event) => setTable(event.target.value)} placeholder="Selecciona o escribe una mesa" /><datalist id="open-table-numbers">{openTableNumbers.map((number) => <option key={number} value={number} />)}</datalist>{table && openTableNumbers.includes(table.trim()) && <small className="table-selection-note">Cuenta abierta seleccionada. El pedido se sumará a esta mesa.</small>}</label>
        <label>Producto<select value={productId} onChange={(event) => { setProductId(event.target.value); setVariantIds([]); setExtraIds([]); }}>{products.map((product) => <option key={product.id} value={product.id}>{product.name} · {money(product.price)}</option>)}</select></label>
        <label>Cantidad<input type="number" min="1" value={quantity} onChange={(event) => setQuantity(Number(event.target.value))} /></label>
      </div>
      {selectedProduct && <div className="order-options">
        {selectedProduct.variants.length > 0 && <label>Variantes<select multiple value={variantIds} onChange={(event) => setVariantIds(Array.from(event.target.selectedOptions, (option) => option.value))}>{selectedProduct.variants.filter((option) => option.is_available).map((option) => <option key={option.id} value={option.id}>{option.name} (+{money(option.price_delta || option.price)})</option>)}</select></label>}
        {selectedProduct.extras.length > 0 && <label>Extras<select multiple value={extraIds} onChange={(event) => setExtraIds(Array.from(event.target.selectedOptions, (option) => option.value))}>{selectedProduct.extras.filter((option) => option.is_available).map((option) => <option key={option.id} value={option.id}>{option.name} (+{money(option.price)})</option>)}</select></label>}
        <label>Observaciones<textarea value={observations} onChange={(event) => setObservations(event.target.value)} placeholder="Sin cebolla, servir separado…" /></label>
      </div>}
      <div className="order-composer-actions"><button className="button secondary" onClick={addLine} disabled={!selectedProduct}><Plus size={16} /> Agregar producto</button><span>{cart.length} línea(s)</span><strong>Subtotal: {money(cartSubtotal)}</strong><button className="button primary" onClick={createWaiterOrder} disabled={saving || !table.trim() || !cart.length}>Enviar a cocina</button></div>
      {cart.length > 0 && <div className="waiter-draft"><strong>Pedido pendiente para Mesa {table || "—"}</strong>{cart.map((line, index) => <div key={`${line.product_id}-${index}`}><span>{line.quantity} × {products.find((product) => product.id === line.product_id)?.name || "Producto"}</span><span>{money(cartLineSubtotal(line))}</span></div>)}</div>}
    </section>}
    <section className="waiter-tables-section"><div className="waiter-section-heading"><div><h2>Mesas abiertas</h2><p>Cada tarjeta representa una cuenta acumulativa independiente.</p></div><span className="pill">{tableAccounts.length} abierta(s)</span></div>
      {!tableAccounts.length ? <div className="inline-empty"><Utensils size={24} /><div><strong>No hay mesas abiertas</strong><span>Elige una mesa y envía el primer pedido.</span></div></div> : <div className="waiter-tables-grid">{tableAccounts.map((account) => {
        const ready = account.orders.some((order) => order.order_status === "ready");
        const paid = account.orders.length > 0 && account.orders.every((order) => order.payment_status === "confirmed");
        const allServed = account.orders.length > 0 && account.orders.every((order) => order.order_status === "served");
        return <article className={`waiter-table-card ${ready ? "has-ready" : ""}`} key={account.id}>
          <header className="waiter-table-header"><div className="waiter-table-title"><span className="table-kicker">CUENTA ABIERTA</span><h2>Mesa {account.table_number}</h2></div><div className="waiter-table-summary"><div><span>Total</span><strong>{money(account.total)}</strong></div><span className={`payment-state ${paid ? "paid" : "pending"}`}>Pago: {paid ? "Pagado" : "Pendiente"}</span></div>{!isAdminPanelView && <button className="button small secondary" onClick={() => selectTable(account.table_number)}>Agregar pedido</button>}</header>
          {ready && <div className="ready-alert">Pedido listo para servir – Mesa {account.table_number}</div>}
          {allServed && <div className="served-table-alert">✓ Pedido servido</div>}
          <div className="waiter-table-orders">{account.orders.map((order) => <section className="waiter-order-block" key={order.id}><div className="waiter-order-heading"><strong>Pedido {order.order_number}</strong><span className={`order-status ${order.order_status}`}>{statusLabels[order.order_status] || order.order_status}</span></div>{activeItems(order).map((item) => <div className="waiter-line-item" key={item.id}><div><strong>{item.quantity} × {item.product_name}</strong>{item.observations && <small>{item.observations}</small>}</div><span>{money(item.line_subtotal)}</span>{canShowItemActions(order) && <div className="waiter-line-actions"><button className="icon-button" disabled={!canEdit(order)} title={canEdit(order) ? "Editar cantidad" : "No editable: el pedido ya comenzó o fue cerrado"} onClick={() => editItem(order, item)}><Pencil size={14} /></button><button className="icon-button danger-icon" disabled={!canEdit(order)} title={canEdit(order) ? "Cancelar producto" : "No cancelable: el pedido ya comenzó o fue cerrado"} onClick={() => cancelItem(order, item)}><Trash2 size={14} /></button></div>}</div>)}{!isAdminPanelView && order.order_status === "ready" && <div className="waiter-serve-action"><button className="button small primary" disabled={saving} onClick={() => updateOrder(order.id, "served")}><Check size={14} /> Entregado en mesa</button></div>}{order.order_status === "served" && <div className="served-confirmation">✓ Entregado en mesa</div>}</section>)}</div>
        </article>;
      })}</div>}
    </section>
  </div>;

  if (role === "cashier") return <div className="page cashier-page">
    <PageHead eyebrow={client?.name || "RESTAURANTE"} title="Caja" description="Cobro y cierre de cuentas por mesa." />
    {adminPanelBanner()}
    {error && <div className="alert error">{error}</div>}
    <div className="tabs order-tabs cashier-tabs" role="tablist" aria-label="Cuentas de caja"><button className={cashierView === "pending" ? "active" : ""} onClick={() => setCashierView("pending")}>Cuentas pendientes</button><button className={cashierView === "paid" ? "active" : ""} onClick={() => setCashierView("paid")}>Pagadas</button></div>
    {cashierView === "pending" ? <section className="cashier-tables-section">
      <div className="cashier-section-heading"><div><h2>Cuentas pendientes</h2><p>Una cuenta acumulativa por cada mesa abierta.</p></div><span className="pill">{cashierPendingAccounts.length} pendiente(s)</span></div>
      {!cashierPendingAccounts.length ? <div className="inline-empty"><Utensils size={24} /><div><strong>No hay cuentas pendientes</strong><span>Las mesas aparecerán aquí cuando tengan pedidos pendientes.</span></div></div> : <div className="cashier-tables-grid">{cashierPendingAccounts.map((account) => {
        const payable = Number(account.total) > 0 && account.orders.some((order) => order.payment_status !== "confirmed" && order.order_status !== "cancelled" && activeItems(order).length > 0);
        const lines = cashierLines(account.orders);
        return <article className="cashier-table-card" key={account.id}>
          <header className="cashier-table-header"><div className="cashier-table-title"><span className="table-kicker">CUENTA ABIERTA</span><h2>Mesa {account.table_number}</h2></div><div className="cashier-table-total"><span>Total mesa</span><strong>{money(account.total)}</strong></div></header>
          <div className="cashier-table-orders">{lines.map((line) => <div className="cashier-line-item" key={line.key}><div className="cashier-item-main"><strong>{line.quantity} × {line.productName}</strong>{line.variants.length > 0 && <small>Variantes: {line.variants.join(", ")}</small>}{line.extras.length > 0 && <small>Extras: {line.extras.join(", ")}</small>}{line.observations && <small>Observación: {line.observations}</small>}</div><span>{money(line.subtotal)}</span></div>)}</div>
          <footer className="cashier-table-footer"><span>Pago: Pendiente</span>{!isAdminPanelView && <button className="button primary" disabled={!payable || saving} onClick={() => payTable(account)}>Confirmar pago · {money(account.total)}</button>}</footer>
        </article>;
      })}</div>}
    </section> : <section className="cashier-history-section">
      <div className="cashier-section-heading"><div><h2>Pagadas</h2><p>Historial de cuentas cerradas por mesa.</p></div><span className="pill">{cashierPaidAccounts.length} cuenta(s)</span></div>
      {!cashierPaidAccounts.length ? <div className="inline-empty"><Utensils size={24} /><div><strong>No hay cuentas pagadas</strong><span>Las cuentas confirmadas aparecerán aquí.</span></div></div> : <div className="cashier-tables-grid">{cashierPaidAccounts.map((account) => <article className="cashier-table-card cashier-paid-card" key={account.id}>
        <header className="cashier-table-header"><div className="cashier-table-title"><span className="table-kicker">CUENTA CERRADA</span><h2>Mesa {account.tableNumber}</h2></div><div className="cashier-table-total"><span>Total pagado</span><strong>{money(account.total)}</strong></div></header>
        <div className="cashier-table-orders">{cashierLines(account.orders).map((line) => <div className="cashier-line-item" key={line.key}><div className="cashier-item-main"><strong>{line.quantity} × {line.productName}</strong>{line.variants.length > 0 && <small>Variantes: {line.variants.join(", ")}</small>}{line.extras.length > 0 && <small>Extras: {line.extras.join(", ")}</small>}{line.observations && <small>Observación: {line.observations}</small>}</div><span>{money(line.subtotal)}</span></div>)}</div>
        <footer className="cashier-table-footer cashier-paid-footer"><span>Confirmada: {account.paidAt ? new Date(account.paidAt).toLocaleString("es-PE") : "—"}</span><span className="cashier-paid-label">Pago confirmado{paymentMethodLabel(account.paymentMethod) ? ` · ${paymentMethodLabel(account.paymentMethod)}` : ""}</span></footer>
      </article>)}</div>}
    </section>}
    <PaymentModal dialog={paymentDialog} method={paymentMethod} error={paymentError} saving={saving} onClose={() => { setPaymentDialog(null); setPaymentMethod(null); setPaymentError(""); }} onSelect={setPaymentMethod} onConfirm={confirmPayment} />
  </div>;

  if (role === "delivery") return <div className="page delivery-page">
    <PageHead eyebrow={client?.name || "RESTAURANTE"} title="Delivery" description="Pedidos listos para coordinar y entregar." />
    {error && <div className="alert error">{error}</div>}
    {!deliveryOrders.length ? <div className="empty-state"><Truck size={28} /><strong>No hay pedidos de delivery listos</strong><span>Los pedidos aparecerán cuando Cocina marque Listo.</span></div> : <div className="delivery-order-grid">{deliveryOrders.map((order) => <article className="delivery-order-card" key={order.id}><header><div><strong>{order.order_number}</strong><small>{order.order_status === "ready" ? "Listo · pendiente de salida" : order.order_status === "on_the_way" ? "En camino" : "Entregado"}</small></div><span className="pill">DELIVERY</span></header><div className="delivery-order-details"><strong>{order.customer_name || "Cliente sin nombre"}</strong><span>{order.customer_phone || "Teléfono no registrado"}</span><span>{order.address || "Dirección no registrada"}</span>{order.address_reference && <small>Referencia: {order.address_reference}</small>}</div><div className="delivery-order-items">{order.items.map((item) => <div key={item.id}><span>{item.quantity} × {item.product_name}</span>{item.observations && <small>{item.observations}</small>}</div>)}</div><div className="row-actions">{order.customer_phone && <a className="button small secondary" href={`https://wa.me/${order.customer_phone.replace(/\D/g, "")}`} target="_blank" rel="noreferrer">Contactar cliente</a>}{order.order_status === "ready" && <button className="button small primary" disabled={saving} onClick={() => updateOrder(order.id, "on_the_way")}><Truck size={14} /> En camino</button>}{order.order_status === "on_the_way" && <button className="button small primary" disabled={saving} onClick={() => updateOrder(order.id, "delivered")}>Entregado</button>}</div></article>)}</div>}
  </div>;

  if (role === "kitchen") return <div className="page kitchen-page">
    <PageHead eyebrow={client?.name || "RESTAURANTE"} title="Cocina" description="Pedidos pendientes de preparación." />
    {adminPanelBanner()}
    {error && <div className="alert error">{error}</div>}
    {!kitchenTables.length && !kitchenRecentOrders.length ? <div className="empty-state kitchen-empty"><ChefHat size={28} /><strong>No hay pedidos pendientes</strong><span>Los nuevos pedidos aparecerán aquí cuando sean recibidos.</span></div> : <>
      {kitchenTables.length > 0 && <div className="kitchen-tables">{kitchenTables.map((tableGroup) => <section className="kitchen-table-card" key={tableGroup.tableNumber || "without-table"}>
        <header className="kitchen-table-header"><h2>{tableGroup.tableNumber ? `Mesa ${tableGroup.tableNumber}` : "Pedidos sin mesa"}</h2><span>{tableGroup.orders.length} pedido(s)</span></header>
        <div className="kitchen-orders">{tableGroup.orders.map((order) => {
          const preparing = order.order_status === "preparing";
          return <article className="kitchen-order-card" key={order.id}>
            <header className="kitchen-order-header"><div><strong>Pedido {order.order_number}</strong><time>{kitchenTime(order.created_at)}</time></div><span className={`kitchen-status ${preparing ? "preparing" : "received"}`}>{preparing ? "En preparación" : "Recibido"}</span></header>
            <div className="kitchen-items">{activeItems(order).map((item) => <div className="kitchen-item" key={item.id}><div className="kitchen-item-main"><strong>{item.quantity} × {item.product_name}</strong>{item.variants.length > 0 && <small>Variantes: {item.variants.map((variant) => variant.name).join(", ")}</small>}{item.extras.length > 0 && <small>Extras: {item.extras.map((extra) => extra.name).join(", ")}</small>}{item.observations && <small className="kitchen-observation">Observación: {item.observations}</small>}</div></div>)}</div>
            {!isAdminPanelView && <div className="kitchen-order-action">{preparing ? <button className="button primary" disabled={saving} onClick={() => updateOrder(order.id, "ready")}><Check size={16} /> Marcar listo</button> : <button className="button secondary" disabled={saving} onClick={() => updateOrder(order.id, "preparing")}><ChefHat size={16} /> Preparar</button>}</div>}
          </article>;
        })}</div>
      </section>)}</div>}
      {kitchenRecentOrders.length > 0 && <section className="card kitchen-recent-section"><div className="section-heading"><div><h2>Preparados recientemente</h2><p>Últimos pedidos marcados como Listo.</p></div><span className="pill">{kitchenRecentOrders.length}</span></div><div className="kitchen-recent-list">{kitchenRecentOrders.map((order) => <article className="kitchen-recent-card" key={order.id}><header><strong>{order.order_number}</strong><span>{order.ready_at ? kitchenTime(order.ready_at) : "—"}</span></header><small>{order.source === "waiter" ? "Mesa" : order.modality === "pickup" ? "Recojo" : "Delivery"}{order.table_number ? ` · Mesa ${order.table_number}` : ""}</small><div>{activeItems(order).map((item) => <div key={item.id}><strong>{item.quantity} × {item.product_name}</strong>{item.observations && <small> · {item.observations}</small>}</div>)}</div><span className="pill">{kitchenPostState(order)}</span></article>)}</div></section>}
    </>}
  </div>;

  return <div className="page">
    <PageHead eyebrow={client?.name || "RESTAURANTE"} title="Pedidos" description={pageDescription} action={administrativeBackLink} />
    {adminPanelBanner()}
    {error && <div className="alert error">{error}</div>}
    {role === "admin" && reviewOrders.length > 0 && <section className="card payment-review-section"><div className="wizard-copy"><h2>Pagos por verificar</h2><p>Estos comprobantes requieren una decisión manual. Ningún voucher confirma el pago automáticamente.</p></div><div className="payment-review-grid">{reviewOrders.map((order) => <article className="payment-review-card" key={order.id}><header><strong>{order.order_number}</strong><span className="pill">PAGO POR VERIFICAR</span></header><div className="payment-review-details"><span>Cliente<strong>{order.customer_name || "Sin nombre"}</strong></span><span>Teléfono<strong>{order.customer_phone || "—"}</strong></span><span>Total<strong>{money(order.total)}</strong></span><span>Monto reportado<strong>{order.reported_payment_amount ? money(order.reported_payment_amount) : "—"}</strong></span><span>Operación<strong>{order.payment_reference || "—"}</strong></span><span>Fecha/hora<strong>{order.payment_reported_at ? new Date(order.payment_reported_at).toLocaleString("es-PE") : "—"}</strong></span></div>{order.payment_receipt_available && <a className="text-button" href={`/api/restaurants/${client?.id}/orders/${order.id}/payment-receipt`} target="_blank" rel="noreferrer">Ver comprobante{order.payment_receipt_filename ? ` · ${order.payment_receipt_filename}` : ""}</a>}<div className="row-actions"><button className="button primary" disabled={saving} onClick={() => decideRestaurantPayment(order, "confirm")}>Confirmar pago</button><button className="button secondary" disabled={saving} onClick={() => decideRestaurantPayment(order, "reject")}>Rechazar pago</button></div></article>)}</div></section>}
    <div className="tabs order-tabs">{[["all", "Todos"], ["payment_review", "Pagos por verificar"], ["pending_payment", "Pendiente de pago"], ["paid", "Pagado"], ["kitchen", "Cocina"], ["ready", "Listo"], ["delivery", "Delivery"], ["delivered", "Entregado"], ["table", "Mesa"]].map(([value, label]) => <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{label}</button>)}</div>
    <div className="table-shell"><table className="data-table"><thead><tr><th>Pedido</th><th>Origen / modalidad</th><th>Detalle</th><th>Total</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>{visible.map((order) => <tr key={order.id}><td><strong>{order.order_number}</strong><small>{new Date(order.created_at).toLocaleString()}</small></td><td>{order.source === "waiter" ? "Mesero" : "WhatsApp"}<small>{order.modality}{order.table_number ? ` · Mesa ${order.table_number}` : ""}</small></td><td>{activeItems(order).map((item) => <div key={item.id}>{item.quantity} × {item.product_name}</div>)}{order.customer_name && <small>{order.customer_name} · {order.customer_phone || ""}</small>}</td><td>S/ {order.total}</td><td><span className="pill">{statusLabels[order.order_status] || order.order_status}</span><small>{paymentVerificationLabel(order.payment_verification_status)}{order.payment_reference ? ` · Operación ${order.payment_reference}` : ""}</small></td><td><div className="order-actions">
      {!isAdminPanelView && (role === "cashier" || role === "admin") && order.modality !== "dine_in" && order.source !== "whatsapp" && order.payment_status !== "confirmed" && order.payment_verification_status !== "verifying" && <button className="button small secondary" onClick={() => pay(order)}>Confirmar pago</button>}
      {!isAdminPanelView && (role === "kitchen" || role === "admin") && order.order_status === "confirmed" && <button className="button small secondary" disabled={saving} onClick={() => updateOrder(order.id, "preparing")}><ChefHat size={14} /> Preparar</button>}
      {!isAdminPanelView && (role === "kitchen" || role === "admin") && ["confirmed", "preparing"].includes(order.order_status) && <button className="button small primary" disabled={saving} onClick={() => updateOrder(order.id, "ready")}><Check size={14} /> Listo</button>}
      {!isAdminPanelView && (role === "delivery" || (role === "admin" && order.modality === "delivery")) && order.order_status === "ready" && <button className="button small secondary" onClick={() => updateOrder(order.id, "on_the_way")}><Truck size={14} /> En camino</button>}
      {!isAdminPanelView && (role === "delivery" || (role === "admin" && order.modality === "delivery")) && order.order_status === "on_the_way" && <button className="button small primary" onClick={() => updateOrder(order.id, "delivered")}>Entregado</button>}
      {!isAdminPanelView && role === "admin" && order.modality === "pickup" && order.order_status === "ready" && <button className="button small primary" onClick={() => updateOrder(order.id, "delivered")}>Recogido</button>}
    </div></td></tr>)}</tbody></table>{!visible.length && <div className="empty-state"><p>No hay pedidos en este filtro.</p></div>}</div>
    <PaymentModal dialog={paymentDialog} method={paymentMethod} error={paymentError} saving={saving} onClose={() => { setPaymentDialog(null); setPaymentMethod(null); setPaymentError(""); }} onSelect={setPaymentMethod} onConfirm={confirmPayment} />
  </div>;
}
