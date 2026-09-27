import { api } from "@/lib/api";
import type { User } from "@/types";

const RESTAURANT_ROLES = new Set(["admin", "cashier", "waiter", "kitchen", "delivery"]);

export async function restaurantAwareRedirect(fallback: string) {
  try {
    const user = await api<User>("/auth/me");
    if (!user.is_vendiq_admin && user.restaurant_client_id && user.restaurant_role && RESTAURANT_ROLES.has(user.restaurant_role)) {
      if (user.restaurant_role === "admin") {
        return `/?client_id=${encodeURIComponent(user.restaurant_client_id)}`;
      }
      return `/orders?client_id=${encodeURIComponent(user.restaurant_client_id)}`;
    }
  } catch {
    // Use the server-provided destination if the session cannot be read yet.
  }
  return fallback;
}
