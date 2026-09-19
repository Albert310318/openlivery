import { api } from "@/lib/api";
import type { LeadDetail } from "@/types";

export async function getOriginatingConversationId(leadId: string): Promise<string | null> {
  const lead = await api<LeadDetail>(`/leads/${leadId}`);
  return lead.conversations[0]?.conversation_id ?? null;
}
