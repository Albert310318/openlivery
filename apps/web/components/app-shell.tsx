"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { BarChart3, Bot, Building2, ClipboardList, ContactRound, CreditCard, Inbox, LayoutDashboard, LogOut, Menu, MessageSquareText, Radio, Settings, Settings2, ShoppingBag, Sparkles, UsersRound, Wallet, X } from "lucide-react";
import { api } from "@/lib/api";
import { useT, type I18nKey } from "@/lib/i18n";
import { LanguageSwitcher } from "@/components/language-switcher";
import { BrandLogo } from "@/components/brand";
import { currentClient } from "@/lib/clients";
import type { Client, User } from "@/types";

const navigation: { href: string; labelKey: I18nKey; icon: typeof LayoutDashboard }[] = [
  { href: "/", labelKey: "nav.home", icon: LayoutDashboard },
  { href: "/clients", labelKey: "nav.clients", icon: Building2 },
  { href: "/agents", labelKey: "nav.agents", icon: Bot },
  { href: "/inbox", labelKey: "nav.inbox", icon: Inbox },
  { href: "/leads", labelKey: "nav.leads", icon: ContactRound },
  { href: "/reports", labelKey: "nav.reports", icon: BarChart3 },
  { href: "/orders", labelKey: "nav.orders", icon: ClipboardList },
  { href: "/playground", labelKey: "nav.playground", icon: MessageSquareText },
  { href: "/channels", labelKey: "nav.channels", icon: Radio },
  { href: "/settings", labelKey: "nav.settings", icon: Settings },
];

// Extra path prefixes served without a session (comma-separated, baked at
// build). Lets a deployment add public pages without patching the shell.
const EXTRA_PUBLIC_PATHS = (process.env.NEXT_PUBLIC_PUBLIC_PATHS || "")
  .split(",")
  .map((path) => path.trim())
  .filter(Boolean);

const EXTRA_NAV_ICONS: Record<string, typeof LayoutDashboard> = {
  wallet: Wallet,
  "credit-card": CreditCard,
  billing: Wallet,
  sparkles: Sparkles,
};

// Extra sidebar links (comma-separated `label|href|icon`, baked at build). Lets a
// deployment add nav entries without patching the shell; icon falls back to Wallet.
const EXTRA_NAV = (process.env.NEXT_PUBLIC_EXTRA_NAV || "")
  .split(",")
  .map((entry) => entry.trim())
  .filter(Boolean)
  .map((entry) => {
    const [label, href, icon] = entry.split("|").map((part) => (part || "").trim());
    return { label, href, icon: EXTRA_NAV_ICONS[icon] || Wallet };
  })
  .filter((item) => item.label && item.href);

const RESTAURANT_OPERATIONAL_ROLES = new Set(["cashier", "waiter", "kitchen", "delivery"]);
const RESTAURANT_ROLE_LABELS: Record<string, string> = {
  cashier: "Caja",
  waiter: "Mesero",
  kitchen: "Cocina",
  delivery: "Delivery",
};

function restaurantRouteAllowed(pathname: string, user: User) {
  if (pathname === "/orders" || pathname.startsWith("/orders/")) return true;
  if (user.restaurant_role === "admin" && ["/", "/agents", "/inbox", "/channels"].some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`))) return true;
  if (pathname === "/personal" || pathname.startsWith("/personal/")) return user.restaurant_role === "admin";
  if (pathname === "/reports" || pathname.startsWith("/reports/")) return user.restaurant_role === "admin";
  if (user.restaurant_role === "admin" && (pathname === "/onboarding" || pathname.startsWith("/onboarding/"))) return true;
  return user.restaurant_role === "admin" && Boolean(
    user.restaurant_client_id && (
      pathname === `/clients/${user.restaurant_client_id}` ||
      pathname.startsWith(`/clients/${user.restaurant_client_id}/channels/`)
    ),
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const t = useT();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(pathname !== "/login");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [routeRedirecting, setRouteRedirecting] = useState(false);
  const [restaurantClient, setRestaurantClient] = useState<Client | null>(null);
  const selectedClientId = searchParams.get("client_id");
  const isLogin = pathname === "/login";
  const isRegistration = pathname === "/registro";
  const isPasswordRecovery = pathname === "/recuperar-contrasena";
  const isHome = pathname === "/";
  const isClientManagementRoute = ["/clients", "/clients/", "/clients/new", "/clients/new/"].includes(pathname);
  const isPortal = pathname.startsWith("/portal/");
  const isWidget = pathname.startsWith("/widget/");
  const isExtraPublic = EXTRA_PUBLIC_PATHS.some(
    (path) => pathname === path || pathname.startsWith(`${path}/`),
  );
  const isBare = isLogin || isRegistration || isPasswordRecovery || isPortal || isWidget || isExtraPublic;

  // Check the session on entry and revalidate it on every navigation, without
  // taking the shell off screen to do it: `loading` starts true and is only ever
  // cleared, so the full-screen loader covers the first render and nothing more.
  // Once a user is known, navigating keeps the sidebar and the page mounted while
  // the check runs in the background. Losing the session clears the user, which
  // puts the loader back up until the redirect lands.
  useEffect(() => {
    if (isBare) { setLoading(false); setUser(null); setRouteRedirecting(false); return; }
    let cancelled = false;
    setRouteRedirecting(false);
    api<User>("/auth/me")
      .then(async (current) => {
        const restaurantOnly = !current.is_vendiq_admin && Boolean(current.restaurant_role);
        if (restaurantOnly && !restaurantRouteAllowed(pathname, current)) {
          setRouteRedirecting(true);
          if (!cancelled) router.replace(`/orders?client_id=${encodeURIComponent(current.restaurant_client_id || "")}`);
          return;
        }
        if (isClientManagementRoute && !current.is_vendiq_admin) {
          setRouteRedirecting(true);
          try {
            const own = await currentClient();
            if (!cancelled) router.replace(`/clients/${own.id}`);
          } catch {
            if (!cancelled) router.replace("/onboarding");
          }
          return;
        }
        if (!cancelled) setUser(current);
      })
      .catch(() => { if (!cancelled) { setUser(null); if (!isHome) router.replace("/login"); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [isBare, isClientManagementRoute, isHome, pathname, router]);

  useEffect(() => {
    if (!user) { setRestaurantClient(null); return; }
    if (user.restaurant_client_id && !user.is_vendiq_admin) { setRestaurantClient(null); return; }
    if (user.is_vendiq_admin && !selectedClientId) { setRestaurantClient(null); return; }
    let cancelled = false;
    setRestaurantClient(null);
    const selected = selectedClientId
      ? api<Client>(`/clients/${encodeURIComponent(selectedClientId)}`)
      : currentClient();
    selected.then((client) => {
      if (!cancelled && client.industry.trim().toLocaleLowerCase() === "restaurante") setRestaurantClient(client);
    }).catch(() => { if (!cancelled) setRestaurantClient(null); });
    return () => { cancelled = true; };
  }, [selectedClientId, user]);

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    // The shell lives in the root layout and survives client-side navigation, so
    // the signed-out identity has to be dropped explicitly.
    setUser(null);
    router.push("/login");
    router.refresh();
  }

  if (isBare) return <>{children}</>;
  if (loading) return <div className="app-loader"><BrandLogo variant="compact" /><span>{t("shell.loading")}</span></div>;
  if (routeRedirecting) return <div className="app-loader"><BrandLogo variant="compact" /><span>{t("shell.loading")}</span></div>;
  if (!user) {
    if (isHome) return <>{children}</>;
    return <div className="app-loader"><BrandLogo variant="compact" /><span>{t("shell.loading")}</span></div>;
  }

  const restaurantClientId = user.is_vendiq_admin ? restaurantClient?.id : user.restaurant_client_id || restaurantClient?.id;
  const isRestaurantOperator = Boolean(!user.is_vendiq_admin && user.restaurant_role && RESTAURANT_OPERATIONAL_ROLES.has(user.restaurant_role));
  const isRestaurantAdmin = Boolean(
    !user.is_vendiq_admin && restaurantClientId && user.restaurant_role === "admin",
  );
  const operationalRoleLabel = user.restaurant_role ? RESTAURANT_ROLE_LABELS[user.restaurant_role] || user.restaurant_role : "";
  const restaurantAdminNavigation = restaurantClientId ? [
    { href: "/", labelKey: "nav.home" as I18nKey, icon: LayoutDashboard },
    { href: `/agents?client_id=${restaurantClientId}`, labelKey: "nav.agents" as I18nKey, icon: Bot },
    { href: `/inbox?client_id=${restaurantClientId}`, labelKey: "nav.inbox" as I18nKey, icon: Inbox },
    { href: `/reports?client_id=${restaurantClientId}`, labelKey: "nav.reports" as I18nKey, icon: BarChart3 },
    { href: `/channels?client_id=${restaurantClientId}`, labelKey: "nav.channels" as I18nKey, icon: Radio },
    { href: `/orders?client_id=${restaurantClientId}`, labelKey: "nav.orders" as I18nKey, icon: ShoppingBag },
    { href: `/personal?client_id=${restaurantClientId}`, labelKey: "nav.personal" as I18nKey, icon: UsersRound },
    { href: `/onboarding?client_id=${restaurantClientId}`, labelKey: "nav.restaurantSetup" as I18nKey, icon: Settings2 },
  ] : [];
  const sidebarNavigation = user.is_vendiq_admin
    ? navigation
    : isRestaurantAdmin
    ? restaurantAdminNavigation
    : [
        ...navigation.filter((item) => item.href !== "/clients"),
        ...(restaurantClientId ? [
          { href: `/orders?client_id=${restaurantClientId}`, labelKey: "nav.orders" as I18nKey, icon: ShoppingBag },
          { href: `/personal?client_id=${restaurantClientId}`, labelKey: "nav.personal" as I18nKey, icon: UsersRound },
        ] : []),
      ];

  if (isRestaurantOperator) {
    return <div className="operational-layout"><main className="main-content operational-content"><div className="operational-topbar"><strong className="operational-restaurant-name">{user.restaurant_client_name || "Restaurante"}</strong><div className="operational-user"><span className="operational-identity">{user.name} · {operationalRoleLabel}</span><span className="operational-separator" aria-hidden="true">|</span><button className="button ghost small operational-logout" onClick={logout}><LogOut size={15} /> {t("shell.logout")}</button></div></div>{children}</main></div>;
  }

  return (
    <div className="app-layout">
      <button className="mobile-menu" onClick={() => setMobileOpen(true)} aria-label={t("shell.openMenu")}><Menu /></button>
      {mobileOpen && <div className="sidebar-overlay" onClick={() => setMobileOpen(false)} />}
      <aside className={`sidebar ${mobileOpen ? "sidebar-open" : ""}`}>
        <div className="brand-row">
          <Link href="/" className="brand" aria-label="Atiende y Vende, inicio"><BrandLogo variant="compact" /><span>AYV</span></Link>
          <button className="sidebar-close" onClick={() => setMobileOpen(false)} aria-label={t("shell.closeMenu")}><X /></button>
        </div>
        <div className="sidebar-workspace"><Building2 size={14} /><span>{user.is_vendiq_admin ? user.agency.name : user.restaurant_client_name || user.agency.name}</span></div>
        <nav>
          <span className="nav-label">{t("nav.section")}</span>
          {sidebarNavigation.map((item) => {
            const routePath = item.href.split("?")[0];
            const active = routePath === "/" ? pathname === "/" : pathname === routePath || pathname.startsWith(`${routePath}/`);
            return <Link key={item.href} href={item.href} className={active ? "active" : ""} onClick={() => setMobileOpen(false)}><item.icon size={18} /><span>{t(item.labelKey)}</span></Link>;
          })}
          {EXTRA_NAV.map((item) => {
            const Icon = item.icon;
            const active = pathname.startsWith(item.href);
            return <Link key={item.href} href={item.href} className={active ? "active" : ""} onClick={() => setMobileOpen(false)}><Icon size={18} /><span>{item.label}</span></Link>;
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-foot">
            <div className="user-avatar">{user.name.slice(0, 1).toUpperCase()}</div>
            <div className="user-meta"><strong>{user.name}</strong><span>{user.email}</span></div>
            <button className="icon-button inverse" onClick={logout} title={t("shell.logout")}><LogOut size={17} /></button>
          </div>
          <LanguageSwitcher />
        </div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  );
}
