import type { Session } from "./api";

/** The server's current grants are authoritative, including an empty list. */
export function hasPermission(session: Session, permission: string): boolean {
  return Array.isArray(session.permissions) && session.permissions.includes(permission);
}
