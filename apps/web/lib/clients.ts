import { api } from "@/lib/api";
import type { Client, User } from "@/types";

export async function accessibleClients(): Promise<Client[]> {
  const user = await api<User>("/auth/me");
  if (user.is_vendiq_admin) return api<Client[]>("/clients");
  const current = await api<Client>("/clients/current");
  return [current];
}

export function currentClient(): Promise<Client> {
  return api<Client>("/clients/current");
}
