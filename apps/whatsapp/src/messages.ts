import { extractMessageContent, type WAMessage } from "@whiskeysockets/baileys";

export function incomingText(message: WAMessage): string | null {
  const content = extractMessageContent(message.message);
  if (!content) return null;
  const text =
    content.conversation ||
    content.extendedTextMessage?.text ||
    content.imageMessage?.caption ||
    content.videoMessage?.caption ||
    content.buttonsResponseMessage?.selectedDisplayText ||
    content.listResponseMessage?.title ||
    content.templateButtonReplyMessage?.selectedDisplayText;
  return text?.trim() || null;
}

export type IncomingMedia = { kind: "image" | "audio"; mimetype: string };

export function incomingMedia(message: WAMessage): IncomingMedia | null {
  const content = extractMessageContent(message.message);
  if (!content) return null;
  if (content.imageMessage) return { kind: "image", mimetype: content.imageMessage.mimetype || "image/jpeg" };
  if (content.audioMessage) return { kind: "audio", mimetype: content.audioMessage.mimetype || "audio/ogg" };
  return null;
}

export function isDirectIncoming(message: WAMessage): boolean {
  const jid = message.key.remoteJid;
  return Boolean(
    jid &&
    message.key.id &&
    !message.key.fromMe &&
    !jid.endsWith("@g.us") &&
    jid !== "status@broadcast" &&
    !jid.endsWith("@newsletter"),
  );
}

export function trustedPhoneJid(message: WAMessage): string | null {
  const remoteJid = message.key.remoteJid;
  if (!remoteJid?.endsWith("@lid")) return null;
  const alternate = message.key.remoteJidAlt;
  return alternate && /^[0-9]{7,15}@s\.whatsapp\.net$/.test(alternate) ? alternate : null;
}

export function directIncomingForUpsert(type: string, messages: WAMessage[]): WAMessage[] {
  // Baileys uses append for history sync/replay. It must never trigger a new
  // AI turn or commercial activation; only live notify events enter the API.
  if (type !== "notify") return [];
  return messages.filter(isDirectIncoming);
}
