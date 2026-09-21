"use client";

import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { ChefHat, LoaderCircle, LogOut, PackageCheck, Plus, RefreshCw, ShoppingCart, Truck, Users } from "lucide-react";
import { Alert } from "@/components/ui";
import { api, ApiError, messageFrom } from "@/lib/api";
import type { RestaurantOrder } from "@/types";

const POLL_MS = 5000;

type PublicInfo = {
  client_name: string;
  portal_slug: string;
};

type StaffSession = {
  id: string;
  client_id: string;
  client_name: string;
  portal_slug: string;
  name: string;
  phone: string;
  role: "waiter" | "kitchen" | "delivery";
  is_active: boolean;
};

type StaffMenuItem = {
  id: string;
  name: string;
  description: string;
  price: string;
  currency: string;
};

type CartItem = {
  menu_item_id: string;
  name: string;
  quantity: number;
  notes: string;
  unitPrice: number;
  currency: string;
};

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    kitchen: "En cocina",
    paid: "Pagado",
    ready: "Listo",
    out_for_delivery: "En reparto",
    served: "Servido",
    delivered: "Entregado",
  };
  return labels[status] || status;
}

function sourceLabel(source: string) {
  if (source === "waiter") return "Mesero";
  if (source === "whatsapp") return "WhatsApp";
  return source;
}

export default function RestaurantStaffPortal({ params }: { params: { slug: string } }) {
  const slug = params.slug;
  const [info, setInfo] = useState<PublicInfo | null>(null);
  const [session, setSession] = useState<StaffSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      api<PublicInfo>(`/restaurant/staff-portal/${slug}`),
      api<StaffSession>(`/restaurant/staff-portal/${slug}/me`).catch((err) => {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      }),
    ])
      .then(([publicInfo, me]) => {
        setInfo(publicInfo);
        setSession(me);
      })
      .catch((err) => setError(messageFrom(err)))
      .finally(() => setLoading(false));
  }, [slug]);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      const me = await api<StaffSession>(`/restaurant/staff-portal/${slug}/login`, {
        method: "POST",
        body: JSON.stringify({
          phone: data.get("phone"),
          password: data.get("password"),
        }),
      });
      setSession({ ...me, client_name: info?.client_name || "", portal_slug: slug });
    } catch (err) {
      setError(messageFrom(err));
    }
  }

  async function logout() {
    await api(`/restaurant/staff-portal/${slug}/logout`, { method: "POST" });
    setSession(null);
  }

  if (loading) return <main className="access-page"><section className="access-form-wrap"><div className="access-card"><LoaderCircle className="spin" /> Cargando…</div></section></main>;
  if (!info) return <main className="access-page"><section className="access-form-wrap"><div className="access-card">{error || "Portal no disponible."}</div></section></main>;

  if (!session) {
    return <main className="access-page portal-access">
      <div className="access-layout">
        <section className="access-intro">
          <span className="access-eyebrow">AYV · Operaciones</span>
          <h1>{info.client_name}</h1>
          <p>Acceso interno para meseros, cocina y delivery.</p>
          <div className="portal-card">
            <ChefHat size={30} />
            <h3>Un solo flujo de pedidos</h3>
            <p>WhatsApp y meseros envían pedidos a cocina. Cuando quedan listos, se derivan a mesa o delivery.</p>
          </div>
        </section>
        <section className="access-form-wrap">
          <form className="access-card access-form" onSubmit={login}>
            <span className="access-card-label"><Users size={15} /> Personal del restaurante</span>
            <h2>Ingresar</h2>
            <label>Celular
              <input name="phone" inputMode="tel" required autoFocus placeholder="+519..." />
            </label>
            <label>Contraseña
              <input name="password" type="password" required />
            </label>
            {error && <Alert>{error}</Alert>}
            <button className="button primary full">Entrar</button>
          </form>
        </section>
      </div>
    </main>;
  }

  if (session.role === "waiter") return <WaiterPanel slug={slug} session={session} logout={logout} />;
  if (session.role === "kitchen") return <KitchenPanel slug={slug} session={session} logout={logout} />;
  return <DeliveryPanel slug={slug} session={session} logout={logout} />;
}

function StaffHeader({ session, logout, icon }: { session: StaffSession; logout: () => void; icon: React.ReactNode }) {
  return <header className="page-head">
    <div>
      <span className="eyebrow">AYV · {session.client_name}</span>
      <h1>{icon} {session.role === "waiter" ? "Panel de mesero" : session.role === "kitchen" ? "Panel de cocina" : "Panel de delivery"}</h1>
      <p>{session.name}</p>
    </div>
    <button className="button ghost" onClick={logout}><LogOut size={16} /> Salir</button>
  </header>;
}

function WaiterPanel({ slug, session, logout }: { slug: string; session: StaffSession; logout: () => void }) {
  const [menu, setMenu] = useState<StaffMenuItem[]>([]);
  const [orders, setOrders] = useState<RestaurantOrder[]>([]);
  const [cart, setCart] = useState<CartItem[]>([]);
  const [table, setTable] = useState("");
  const [customer, setCustomer] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const [nextMenu, nextOrders] = await Promise.all([
      api<StaffMenuItem[]>(`/restaurant/staff-portal/${slug}/menu`),
      api<RestaurantOrder[]>(`/restaurant/staff-portal/${slug}/orders`),
    ]);
    setMenu(nextMenu);
    setOrders(nextOrders);
  }, [slug]);

  useEffect(() => {
    refresh().catch((err) => setError(messageFrom(err)));
    const timer = setInterval(() => refresh().catch(() => {}), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const total = useMemo(() => cart.reduce((sum, item) => sum + item.unitPrice * item.quantity, 0), [cart]);
  const currency = cart[0]?.currency || menu[0]?.currency || "PEN";

  function addItem() {
    const item = menu.find((row) => row.id === selectedId);
    if (!item) return;
    const cleanNotes = notes.trim();
    setCart((current) => {
      const index = current.findIndex((row) => row.menu_item_id === item.id && row.notes === cleanNotes);
      if (index < 0) {
        return [...current, {
          menu_item_id: item.id,
          name: item.name,
          quantity,
          notes: cleanNotes,
          unitPrice: Number(item.price),
          currency: item.currency,
        }];
      }
      return current.map((row, idx) => idx === index ? { ...row, quantity: row.quantity + quantity } : row);
    });
    setSelectedId("");
    setQuantity(1);
    setNotes("");
  }

  async function sendOrder() {
    if (!table.trim() || !cart.length) return;
    setBusy(true);
    setError("");
    try {
      await api(`/restaurant/staff-portal/${slug}/waiter-orders`, {
        method: "POST",
        body: JSON.stringify({
          table_label: table.trim(),
          customer_name: customer.trim() || null,
          items: cart.map((item) => ({
            menu_item_id: item.menu_item_id,
            quantity: item.quantity,
            notes: item.notes,
          })),
        }),
      });
      setTable("");
      setCustomer("");
      setCart([]);
      await refresh();
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  async function served(order: RestaurantOrder) {
    setBusy(true);
    try {
      await api(`/restaurant/staff-portal/${slug}/orders/${order.order_id}/served`, { method: "POST" });
      await refresh();
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  return <main className="page">
    <StaffHeader session={session} logout={logout} icon={<Users size={30} />} />
    {error && <Alert>{error}</Alert>}

    <section className="panel">
      <div className="panel-head"><div><h3>Nuevo pedido</h3><p>Selecciona la mesa y agrega los productos.</p></div><ShoppingCart /></div>
      <div className="settings-form">
        <section className="settings-section">
          <div className="settings-copy"><ShoppingCart size={24} /></div>
          <div className="settings-fields">
            <div className="form-grid">
              <label>Mesa<input value={table} onChange={(e) => setTable(e.target.value)} placeholder="Ej. 5" /></label>
              <label>Cliente (opcional)<input value={customer} onChange={(e) => setCustomer(e.target.value)} /></label>
            </div>
            <label>Producto
              <select value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
                <option value="">Seleccionar…</option>
                {menu.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.currency} {item.price}</option>)}
              </select>
            </label>
            <div className="form-grid">
              <label>Cantidad<input type="number" min={1} max={99} value={quantity} onChange={(e) => setQuantity(Math.max(1, Number(e.target.value) || 1))} /></label>
              <label>Observaciones<input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Sin cebolla, término…" /></label>
            </div>
            <button type="button" className="button secondary" disabled={!selectedId} onClick={addItem}><Plus size={15} /> Agregar</button>

            <div className="table-shell"><table className="data-table">
              <thead><tr><th>Producto</th><th>Cant.</th><th>Observación</th><th>Total</th><th></th></tr></thead>
              <tbody>{cart.map((item, index) => <tr key={`${item.menu_item_id}-${index}`}>
                <td>{item.name}</td><td>{item.quantity}</td><td>{item.notes || "—"}</td><td>{currency} {(item.unitPrice * item.quantity).toFixed(2)}</td>
                <td><button className="button tiny ghost" onClick={() => setCart((current) => current.filter((_, idx) => idx !== index))}>Quitar</button></td>
              </tr>)}
              {!cart.length && <tr><td colSpan={5}>Aún no agregaste productos.</td></tr>}</tbody>
            </table></div>
            <div className="form-footer">
              <strong>Total: {currency} {total.toFixed(2)}</strong>
              <button className="button primary" disabled={busy || !table.trim() || !cart.length} onClick={() => void sendOrder()}><ChefHat size={16} /> Enviar a cocina</button>
            </div>
          </div>
        </section>
      </div>
    </section>

    <OrderCards
      title="Mis mesas"
      orders={orders}
      empty="No tienes pedidos activos."
      action={(order) => order.status === "ready" ? <button className="button primary" disabled={busy} onClick={() => void served(order)}><PackageCheck size={15} /> Marcar servido</button> : null}
    />
  </main>;
}

function KitchenPanel({ slug, session, logout }: { slug: string; session: StaffSession; logout: () => void }) {
  const [orders, setOrders] = useState<RestaurantOrder[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setOrders(await api<RestaurantOrder[]>(`/restaurant/staff-portal/${slug}/orders`));
  }, [slug]);

  useEffect(() => {
    refresh().catch((err) => setError(messageFrom(err)));
    const timer = setInterval(() => refresh().catch(() => {}), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  async function ready(order: RestaurantOrder) {
    setBusy(true);
    try {
      await api(`/restaurant/staff-portal/${slug}/orders/${order.order_id}/ready`, { method: "POST" });
      await refresh();
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  return <main className="page">
    <StaffHeader session={session} logout={logout} icon={<ChefHat size={30} />} />
    {error && <Alert>{error}</Alert>}
    <div className="toolbar"><button className="button ghost" onClick={() => void refresh()}><RefreshCw size={16} /> Actualizar</button></div>
    <OrderCards
      title="Pedidos de cocina"
      orders={orders}
      empty="No hay pedidos pendientes."
      action={(order) => (order.status === "kitchen" || order.status === "paid") ? <button className="button primary" disabled={busy} onClick={() => void ready(order)}><PackageCheck size={15} /> Pedido listo</button> : null}
    />
  </main>;
}

function DeliveryPanel({ slug, session, logout }: { slug: string; session: StaffSession; logout: () => void }) {
  const [orders, setOrders] = useState<RestaurantOrder[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setOrders(await api<RestaurantOrder[]>(`/restaurant/staff-portal/${slug}/orders`));
  }, [slug]);

  useEffect(() => {
    refresh().catch((err) => setError(messageFrom(err)));
    const timer = setInterval(() => refresh().catch(() => {}), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  async function act(order: RestaurantOrder, endpoint: "start-delivery" | "delivered") {
    setBusy(true);
    try {
      await api(`/restaurant/staff-portal/${slug}/orders/${order.order_id}/${endpoint}`, { method: "POST" });
      await refresh();
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setBusy(false);
    }
  }

  return <main className="page">
    <StaffHeader session={session} logout={logout} icon={<Truck size={30} />} />
    {error && <Alert>{error}</Alert>}
    <div className="toolbar"><button className="button ghost" onClick={() => void refresh()}><RefreshCw size={16} /> Actualizar</button></div>
    <OrderCards
      title="Pedidos de delivery"
      orders={orders}
      empty="No hay pedidos para repartir."
      action={(order) => order.status === "ready"
        ? <button className="button primary" disabled={busy} onClick={() => void act(order, "start-delivery")}><Truck size={15} /> Iniciar reparto</button>
        : order.status === "out_for_delivery"
          ? <button className="button primary" disabled={busy} onClick={() => void act(order, "delivered")}><PackageCheck size={15} /> Entregado</button>
          : null}
    />
  </main>;
}

function OrderCards({
  title,
  orders,
  empty,
  action,
}: {
  title: string;
  orders: RestaurantOrder[];
  empty: string;
  action: (order: RestaurantOrder) => React.ReactNode;
}) {
  return <section className="panel">
    <div className="panel-head"><div><h3>{title}</h3><p>{orders.length} pedido(s)</p></div></div>
    {!orders.length ? <div className="inline-empty slim"><div><strong>{empty}</strong></div></div> :
      <div className="portal-grid">
        {orders.map((order) => <article className="portal-card" key={order.order_id}>
          <div className="panel-head">
            <div><strong>{order.code}</strong><p>{sourceLabel(order.source)} · {order.fulfillment_type === "table" ? `Mesa ${order.table || "—"}` : "Delivery"}</p></div>
            <span className="pill">{statusLabel(order.status)}</span>
          </div>
          {order.customer_name && <p><strong>Cliente:</strong> {order.customer_name}</p>}
          {order.delivery_address && <p><strong>Dirección:</strong> {order.delivery_address}</p>}
          {order.waiter_name && <p><strong>Mesero:</strong> {order.waiter_name}</p>}
          <div>
            {order.items.map((item, index) => <p key={index}><strong>{item.quantity}× {item.name}</strong>{item.notes ? ` · ${item.notes}` : ""}</p>)}
          </div>
          <p><strong>Total: {order.currency} {order.total}</strong></p>
          <div className="form-footer">{action(order)}</div>
        </article>)}
      </div>}
  </section>;
}
