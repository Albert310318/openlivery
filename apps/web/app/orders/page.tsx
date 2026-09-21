"use client";

import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, ChefHat, ClipboardList, MessageCircle, Plus, RefreshCw, ShoppingCart, Truck, Users } from "lucide-react";
import { PageHead } from "@/components/ui";
import { useToast } from "@/components/toast";
import { api, messageFrom } from "@/lib/api";
import { useT, type I18nKey } from "@/lib/i18n";
import type { Client, RestaurantMenuItem, RestaurantOrder, RestaurantSettings } from "@/types";

const STATUS_KEYS: Record<string, I18nKey> = {
  draft: "orders.statuses.draft",
  awaiting_confirmation: "orders.statuses.awaiting_confirmation",
  awaiting_payment: "orders.statuses.awaiting_payment",
  payment_reported: "orders.statuses.payment_reported",
  paid: "orders.statuses.paid",
  kitchen: "orders.statuses.kitchen",
  ready: "orders.statuses.ready",
  out_for_delivery: "orders.statuses.out_for_delivery",
  served: "orders.statuses.served",
  delivered: "orders.statuses.delivered",
  cancelled: "orders.statuses.cancelled",
};

const PAYMENT_KEYS: Record<string, I18nKey> = {
  pending: "orders.paymentStatuses.pending",
  reported: "orders.paymentStatuses.reported",
  confirmed: "orders.paymentStatuses.confirmed",
  rejected: "orders.paymentStatuses.rejected",
  pay_at_table: "orders.paymentStatuses.pay_at_table",
};

type WaiterCartItem = {
  menu_item_id: string;
  name: string;
  quantity: number;
  notes: string;
  unitPrice: number;
};

function restaurantHint(client: Client): boolean {
  const text = `${client.industry} ${client.description}`.toLowerCase();
  return /(restaurant|restaurante|poller|pizzer|cafeter|comida|gastronom|cevicher)/.test(text);
}

export default function OrdersPage() {
  const t = useT();
  const toast = useToast();
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [settings, setSettings] = useState<RestaurantSettings | null>(null);
  const [menu, setMenu] = useState<RestaurantMenuItem[]>([]);
  const [orders, setOrders] = useState<RestaurantOrder[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [tableLabel, setTableLabel] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [selectedMenuItemId, setSelectedMenuItemId] = useState("");
  const [waiterQuantity, setWaiterQuantity] = useState(1);
  const [waiterNotes, setWaiterNotes] = useState("");
  const [waiterCart, setWaiterCart] = useState<WaiterCartItem[]>([]);

  useEffect(() => {
    api<Client[]>("/clients")
      .then((rows) => {
        setClients(rows);
        const preferred = rows.find(restaurantHint) || rows[0];
        if (preferred) setClientId(preferred.id);
      })
      .catch((error) => toast.error(messageFrom(error)));
  }, [toast]);

  const selectedClient = useMemo(
    () => clients.find((client) => client.id === clientId) || null,
    [clients, clientId],
  );
  const activeMenu = useMemo(() => menu.filter((item) => item.is_active), [menu]);
  const waiterTotal = useMemo(
    () => waiterCart.reduce((sum, item) => sum + item.unitPrice * item.quantity, 0),
    [waiterCart],
  );


  const load = useCallback(async () => {
    if (!clientId) return;
    setLoading(true);
    try {
      const [nextSettings, nextMenu, nextOrders] = await Promise.all([
        api<RestaurantSettings>(`/restaurant/clients/${clientId}/settings`),
        api<RestaurantMenuItem[]>(`/restaurant/clients/${clientId}/menu?include_inactive=true`),
        api<RestaurantOrder[]>(`/restaurant/orders?client_id=${clientId}`),
      ]);
      setSettings(nextSettings);
      setMenu(nextMenu);
      setOrders(nextOrders);
    } catch (error) {
      toast.error(messageFrom(error));
    } finally {
      setLoading(false);
    }
  }, [clientId, toast]);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    setTableLabel("");
    setCustomerName("");
    setSelectedMenuItemId("");
    setWaiterQuantity(1);
    setWaiterNotes("");
    setWaiterCart([]);
  }, [clientId]);

  async function saveSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!clientId) return;
    const data = new FormData(event.currentTarget);
    setBusy(true);
    try {
      const updated = await api<RestaurantSettings>(`/restaurant/clients/${clientId}/settings`, {
        method: "PATCH",
        body: JSON.stringify({
          currency: String(data.get("currency") || "PEN"),
          payment_instructions: String(data.get("payment_instructions") || ""),
          kitchen_phone: String(data.get("kitchen_phone") || "") || null,
          delivery_phone: String(data.get("delivery_phone") || "") || null,
        }),
      });
      setSettings(updated);
      toast.success(t("orders.settingsSaved"));
    } catch (error) {
      toast.error(messageFrom(error));
    } finally {
      setBusy(false);
    }
  }

  async function addMenuItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!clientId) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    try {
      await api(`/restaurant/clients/${clientId}/menu`, {
        method: "POST",
        body: JSON.stringify({
          name: String(data.get("name") || ""),
          description: String(data.get("description") || ""),
          price: String(data.get("price") || "0"),
          currency: settings?.currency || "PEN",
          aliases: String(data.get("aliases") || "").split(",").map((value) => value.trim()).filter(Boolean),
          is_active: true,
        }),
      });
      form.reset();
      toast.success(t("orders.itemCreated"));
      await load();
    } catch (error) {
      toast.error(messageFrom(error));
    } finally {
      setBusy(false);
    }
  }

  function addWaiterItem() {
    const menuItem = activeMenu.find((item) => item.id === selectedMenuItemId);
    if (!menuItem || waiterQuantity < 1) return;
    const notes = waiterNotes.trim();
    setWaiterCart((current) => {
      const index = current.findIndex(
        (item) => item.menu_item_id === menuItem.id && item.notes === notes,
      );
      if (index === -1) {
        return [
          ...current,
          {
            menu_item_id: menuItem.id,
            name: menuItem.name,
            quantity: waiterQuantity,
            notes,
            unitPrice: Number(menuItem.price),
          },
        ];
      }
      return current.map((item, itemIndex) => (
        itemIndex === index ? { ...item, quantity: item.quantity + waiterQuantity } : item
      ));
    });
    setSelectedMenuItemId("");
    setWaiterQuantity(1);
    setWaiterNotes("");
  }

  function removeWaiterItem(index: number) {
    setWaiterCart((current) => current.filter((_, itemIndex) => itemIndex !== index));
  }

  async function sendWaiterOrder() {
    if (!clientId || !tableLabel.trim() || waiterCart.length === 0) return;
    setBusy(true);
    try {
      await api<RestaurantOrder>(`/restaurant/clients/${clientId}/waiter-orders`, {
        method: "POST",
        body: JSON.stringify({
          table_label: tableLabel.trim(),
          customer_name: customerName.trim() || null,
          items: waiterCart.map((item) => ({
            menu_item_id: item.menu_item_id,
            quantity: item.quantity,
            notes: item.notes,
          })),
        }),
      });
      toast.success(t("orders.waiterOrderSent"));
      setTableLabel("");
      setCustomerName("");
      setWaiterCart([]);
      await load();
    } catch (error) {
      toast.error(messageFrom(error));
    } finally {
      setBusy(false);
    }
  }

  async function action(order: RestaurantOrder, kind: "confirm-payment" | "ready" | "out_for_delivery" | "served" | "delivered" | "cancelled") {
    setBusy(true);
    try {
      if (kind === "confirm-payment" || kind === "ready") {
        await api(`/restaurant/orders/${order.order_id}/${kind}`, { method: "POST" });
      } else {
        await api(`/restaurant/orders/${order.order_id}/status`, {
          method: "POST",
          body: JSON.stringify({ status: kind }),
        });
      }
      if (kind === "confirm-payment") toast.success(t("orders.paymentConfirmed"));
      else if (kind === "ready") toast.success(t("orders.readyUpdated"));
      else toast.success(t("orders.statusUpdated"));
      await load();
    } catch (error) {
      toast.error(messageFrom(error));
    } finally {
      setBusy(false);
    }
  }

  return <div className="page">
    <PageHead eyebrow={t("orders.eyebrow")} title={t("orders.title")} description={t("orders.description")} />

    <div className="toolbar">
      <div className="filter-select">
        <span>{t("orders.client")}</span>
        <select value={clientId} onChange={(event) => setClientId(event.target.value)}>
          <option value="">{t("orders.selectClient")}</option>
          {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
        </select>
      </div>
      <button className="button ghost" onClick={() => void load()} disabled={!clientId || loading}>
        <RefreshCw size={16} /> {t("orders.refresh")}
      </button>
    </div>

    {selectedClient && settings && !settings.restaurant && (
      <div className="inline-empty slim"><div><strong>{selectedClient.name}</strong><span>{t("orders.notRestaurant")}</span></div></div>
    )}

    {settings?.restaurant && <section className="panel">
      <div className="panel-head">
        <div><h3>{t("orders.entriesTitle")}</h3><p>{t("orders.entriesCopy")}</p></div>
      </div>
      <div className="settings-form">
        <section className="settings-section">
          <div className="settings-copy"><MessageCircle size={24} /></div>
          <div className="settings-fields">
            <strong>{t("orders.whatsappEntry")}</strong>
            <p>{t("orders.whatsappEntryCopy")}</p>
          </div>
        </section>
        <section className="settings-section">
          <div className="settings-copy"><Users size={24} /></div>
          <div className="settings-fields">
            <strong>{t("orders.waiterEntry")}</strong>
            <p>{t("orders.waiterEntryCopy")}</p>
          </div>
        </section>
      </div>
    </section>}

    {settings?.restaurant && <section className="panel">
      <div className="panel-head">
        <div><h3>{t("orders.waiterOrderTitle")}</h3><p>{t("orders.waiterEntryCopy")}</p></div>
        <ShoppingCart size={22} />
      </div>
      <div className="settings-form">
        <section className="settings-section">
          <div className="settings-copy"><Users size={24} /></div>
          <div className="settings-fields">
            <div className="form-grid">
              <label>{t("orders.table")}<input value={tableLabel} onChange={(event) => setTableLabel(event.target.value)} placeholder="8" /></label>
              <label>{t("orders.customerOptional")}<input value={customerName} onChange={(event) => setCustomerName(event.target.value)} /></label>
            </div>
            <div className="form-grid">
              <label>{t("orders.product")}
                <select value={selectedMenuItemId} onChange={(event) => setSelectedMenuItemId(event.target.value)}>
                  <option value="">—</option>
                  {activeMenu.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.currency} {item.price}</option>)}
                </select>
              </label>
              <label>{t("orders.quantity")}<input type="number" min={1} max={99} value={waiterQuantity} onChange={(event) => setWaiterQuantity(Math.max(1, Number(event.target.value) || 1))} /></label>
            </div>
            <label>{t("orders.notes")}<input value={waiterNotes} onChange={(event) => setWaiterNotes(event.target.value)} placeholder="Sin cebolla, término, extras…" /></label>
            <div className="form-footer">
              <button type="button" className="button secondary" disabled={!selectedMenuItemId || busy} onClick={addWaiterItem}>
                <Plus size={15} /> {t("orders.addToTicket")}
              </button>
            </div>

            <div className="table-shell">
              <table className="data-table">
                <thead><tr><th>{t("orders.product")}</th><th>{t("orders.quantity")}</th><th>{t("orders.notes")}</th><th>{t("orders.total")}</th><th></th></tr></thead>
                <tbody>
                  {waiterCart.map((item, index) => <tr key={`${item.menu_item_id}-${item.notes}-${index}`}>
                    <td><strong>{item.name}</strong></td>
                    <td>{item.quantity}</td>
                    <td>{item.notes || "—"}</td>
                    <td>{settings.currency} {(item.unitPrice * item.quantity).toFixed(2)}</td>
                    <td><button type="button" className="button tiny ghost" onClick={() => removeWaiterItem(index)}>{t("orders.remove")}</button></td>
                  </tr>)}
                  {!waiterCart.length && <tr><td colSpan={5}>—</td></tr>}
                </tbody>
              </table>
            </div>
            <div className="form-footer">
              <strong>{t("orders.total")}: {settings.currency} {waiterTotal.toFixed(2)}</strong>
              <button type="button" className="button primary" disabled={busy || !tableLabel.trim() || waiterCart.length === 0} onClick={() => void sendWaiterOrder()}>
                <ChefHat size={15} /> {t("orders.sendKitchen")}
              </button>
            </div>
          </div>
        </section>
      </div>
    </section>}

    {settings && <section className="panel">
      <div className="panel-head">
        <div><h3>{t("orders.settingsTitle")}</h3><p>{t("orders.settingsCopy")}</p></div>
      </div>
      <form className="settings-form" onSubmit={saveSettings}>
        <section className="settings-section">
          <div className="settings-copy"><ChefHat size={24} /></div>
          <div className="settings-fields">
            <div className="form-grid">
              <label>{t("orders.currency")}<input name="currency" defaultValue={settings.currency} maxLength={3} /></label>
              <label>{t("orders.kitchenPhone")}<input name="kitchen_phone" defaultValue={settings.kitchen_phone || ""} placeholder="+519..." /></label>
            </div>
            <label>{t("orders.deliveryPhone")}<input name="delivery_phone" defaultValue={settings.delivery_phone || ""} placeholder="+519..." /></label>
            <label>{t("orders.paymentInstructions")}<textarea name="payment_instructions" defaultValue={settings.payment_instructions} placeholder={t("orders.paymentInstructionsPlaceholder")} rows={4} /></label>
            <div className="form-footer"><button className="button secondary" disabled={busy}>{t("orders.saveSettings")}</button></div>
          </div>
        </section>
      </form>
    </section>}

    {settings?.restaurant && <section className="panel">
      <div className="panel-head">
        <div><h3>{t("orders.menuTitle")}</h3><p>{t("orders.menuCopy")}</p></div>
      </div>
      <form className="settings-form" onSubmit={addMenuItem}>
        <section className="settings-section">
          <div className="settings-copy"><Plus size={24} /></div>
          <div className="settings-fields">
            <div className="form-grid">
              <label>{t("orders.itemName")}<input required name="name" /></label>
              <label>{t("orders.price")}<input required name="price" type="number" min="0" step="0.01" /></label>
            </div>
            <label>{t("orders.itemDescription")}<input name="description" /></label>
            <label>{t("orders.aliases")}<input name="aliases" /><span className="field-help">{t("orders.aliasesHelp")}</span></label>
            <div className="form-footer"><button className="button primary" disabled={busy}><Plus size={15} /> {t("orders.addItem")}</button></div>
          </div>
        </section>
      </form>

      {menu.length > 0 && <div className="table-shell"><table className="data-table">
        <thead><tr><th>{t("orders.itemName")}</th><th>{t("orders.itemDescription")}</th><th>{t("orders.price")}</th><th>{t("orders.active")}</th></tr></thead>
        <tbody>{menu.map((item) => <tr key={item.id}>
          <td><strong>{item.name}</strong></td>
          <td>{item.description || "—"}</td>
          <td>{item.currency} {item.price}</td>
          <td>{item.is_active ? <span className="pill"><CheckCircle2 size={13} /> {t("orders.active")}</span> : "—"}</td>
        </tr>)}</tbody>
      </table></div>}
    </section>}

    {settings?.restaurant && <section className="panel">
      <div className="panel-head">
        <div><h3>{t("orders.ordersTitle")}</h3><p>{t("orders.ordersCopy")}</p></div>
        <ClipboardList size={22} />
      </div>
      {!orders.length ? <div className="inline-empty slim"><div><strong>{t("orders.noOrders")}</strong></div></div> :
      <div className="table-shell"><table className="data-table">
        <thead><tr>
          <th>Pedido</th><th>{t("orders.customer")}</th><th>{t("orders.items")}</th><th>{t("orders.source")}</th>
          <th>{t("orders.total")}</th><th>{t("orders.payment")}</th><th>{t("orders.status")}</th><th>{t("orders.actions")}</th>
        </tr></thead>
        <tbody>{orders.map((order) => <tr key={order.order_id}>
          <td><strong>{order.code}</strong></td>
          <td>{order.customer_name || order.customer_phone || "—"}{order.waiter_name ? <><br /><span className="field-help">{t("orders.waiter")}: {order.waiter_name}</span></> : null}</td>
          <td>{order.items.map((item) => `${item.quantity}× ${item.name}`).join(", ") || "—"}</td>
          <td>{order.table ? `Mesa ${order.table}` : order.source}</td>
          <td><strong>{order.currency} {order.total}</strong></td>
          <td>{t(PAYMENT_KEYS[order.payment_status] || "orders.paymentStatuses.pending")}</td>
          <td>{t(STATUS_KEYS[order.status] || "orders.statuses.draft")}</td>
          <td><div className="table-actions">
            {order.payment_status === "reported" && <button className="button tiny primary" disabled={busy} onClick={() => void action(order, "confirm-payment")}>{t("orders.confirmPayment")}</button>}
            {(order.status === "kitchen" || order.status === "paid") && <button className="button tiny secondary" disabled={busy} onClick={() => void action(order, "ready")}>{t("orders.markReady")}</button>}
            {order.status === "ready" && order.fulfillment_type === "delivery" && <button className="button tiny secondary" disabled={busy} onClick={() => void action(order, "out_for_delivery")}><Truck size={13} /> {t("orders.outForDelivery")}</button>}
            {order.status === "ready" && order.fulfillment_type === "table" && <button className="button tiny secondary" disabled={busy} onClick={() => void action(order, "served")}>{t("orders.served")}</button>}
            {order.status === "out_for_delivery" && <button className="button tiny secondary" disabled={busy} onClick={() => void action(order, "delivered")}>{t("orders.delivered")}</button>}
            {!["delivered","served","cancelled"].includes(order.status) && <button className="button tiny ghost" disabled={busy} onClick={() => void action(order, "cancelled")}>{t("orders.cancel")}</button>}
          </div></td>
        </tr>)}</tbody>
      </table></div>}
    </section>}
  </div>;
}
