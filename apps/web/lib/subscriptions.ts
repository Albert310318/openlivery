export type SubscriptionStatus = "TRIAL" | "ACTIVE" | "PAYMENT_PENDING" | "SUSPENDED" | "CANCELLED";
export type Plan = {
  id: string; code: string; name: string; description: string;
  monthly_price: string | null; currency: string;
  is_active: boolean; available_for_new_subscriptions: boolean;
  modules: { code: string; name: string; is_available: boolean }[];
};
export type Subscription = {
  id: string; client_id: string; plan: Plan; status: SubscriptionStatus;
  first_activated_at: string | null; trial_started_at: string | null;
  trial_ends_at: string | null; next_renewal_at: string | null;
};
