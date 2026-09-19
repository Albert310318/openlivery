import makeWASocket, {
  Browsers,
  DisconnectReason,
  downloadMediaMessage,
  type WASocket,
  type WAMessage,
} from "@whiskeysockets/baileys";
import pino from "pino";
import QRCode from "qrcode";
import { backend, setStatus } from "./api.js";
import { createDatabaseAuth } from "./auth.js";
import { directIncomingForUpsert, incomingMedia, incomingText, isDirectIncoming, normalizePhoneJid, trustedPhoneJid } from "./messages.js";

// Skip forwarding media larger than this; the backend also caps at 20 MB.
const MAX_MEDIA_BYTES = 18 * 1024 * 1024;

type ChannelConfig = {
  id: string;
  client_id: string;
  agent_id: string;
  enabled: boolean;
  auth_state: unknown | null;
};

type ChannelRuntime = {
  socket: WASocket;
  stopRequested: boolean;
  reconnectTimer?: NodeJS.Timeout;
};

type InboundResult = {
  accepted: boolean;
  reply?: string | null;
  outbound_message_id?: string | null;
  delivery_id?: string | null;
};

const logger = pino({ level: process.env.WHATSAPP_LOG_LEVEL || "silent" });
const runtimes = new Map<string, ChannelRuntime>();

function errorCode(error: unknown): number | undefined {
  return (error as { output?: { statusCode?: number } } | undefined)?.output?.statusCode;
}

function cleanNumber(jid?: string | null): string | null {
  if (!jid) return null;
  return jid.split(":")[0]?.split("@")[0] || null;
}

function activationFailureState(error: unknown): "FAILED" | "UNKNOWN" {
  return (error as { activationState?: string } | undefined)?.activationState === "FAILED" ? "FAILED" : "UNKNOWN";
}

async function resolveTrustedSenderJid(socket: WASocket, message: WAMessage): Promise<string | null> {
  const alternate = trustedPhoneJid(message);
  if (alternate) return alternate;

  const remoteJid = message.key.remoteJid;
  if (!remoteJid?.endsWith("@lid")) return null;

  try {
    const repository = (socket as WASocket & {
      signalRepository?: {
        lidMapping?: {
          getPNForLID?: (jid: string) => Promise<string | undefined | null>;
        };
      };
    }).signalRepository;
    const mapped = await repository?.lidMapping?.getPNForLID?.(remoteJid);
    const normalized = normalizePhoneJid(mapped);
    console.info(
      `[WhatsApp] LID phone resolution ${normalized ? "succeeded" : "unavailable"} for live message`,
    );
    return normalized;
  } catch (error) {
    console.warn("[WhatsApp] LID phone resolution failed:", (error as Error).message);
    return null;
  }
}

function messageTimestampIso(message: WAMessage): string | null {
  const raw = message.messageTimestamp;
  const seconds = typeof raw === "number"
    ? raw
    : raw && typeof raw === "object" && "toNumber" in raw && typeof raw.toNumber === "function"
      ? raw.toNumber()
      : Number(raw);
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  const timestamp = new Date(seconds * 1000);
  return Number.isNaN(timestamp.getTime()) ? null : timestamp.toISOString();
}

async function processIncoming(channelId: string, socket: WASocket, message: WAMessage): Promise<void> {
  if (!isDirectIncoming(message)) return;
  const text = incomingText(message);
  const media = incomingMedia(message);
  const remoteJid = message.key.remoteJid;
  const externalMessageId = message.key.id;
  if ((!text && !media) || !remoteJid || !externalMessageId) return;

  const body: Record<string, unknown> = {
    external_message_id: externalMessageId,
    remote_jid: remoteJid,
    sender_name: message.pushName || null,
    text: text || "",
  };
  const timestamp = messageTimestampIso(message);
  if (timestamp) body.message_timestamp = timestamp;
  const trustedSenderJid = await resolveTrustedSenderJid(socket, message);
  if (trustedSenderJid) body.trusted_sender_jid = trustedSenderJid;
  if (media) {
    try {
      const buffer = (await downloadMediaMessage(
        message,
        "buffer",
        {},
        { logger, reuploadRequest: socket.updateMediaMessage },
      )) as Buffer;
      if (buffer.length <= MAX_MEDIA_BYTES) {
        body.media_kind = media.kind;
        body.media_mime = media.mimetype;
        body.media_base64 = buffer.toString("base64");
      }
    } catch (error) {
      console.error(`[WhatsApp ${channelId}] Could not download media:`, (error as Error).message);
    }
  }

  let result: InboundResult;
  try {
    result = await backend<InboundResult>(`/channels/${channelId}/inbound`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    console.info(`[WhatsApp ${channelId}] API inbound handoff succeeded; accepted=${result.accepted}`);
  } catch (error) {
    console.error(`[WhatsApp ${channelId}] API inbound handoff failed:`, (error as Error).message);
    throw error;
  }
  if (!result.reply) return;
  try {
    const runtime = runtimes.get(channelId);
    if (!runtime || runtime.socket !== socket || runtime.stopRequested) {
      const error = new Error("WhatsApp is not connected");
      (error as Error & { activationState?: string }).activationState = "FAILED";
      throw error;
    }
    const sent = await socket.sendMessage(remoteJid, { text: result.reply });
    const acceptedAt = new Date().toISOString();
    if (!sent?.key.id) throw new Error("WhatsApp did not confirm the send");
    if (result.outbound_message_id) {
      await backend(`/channels/${channelId}/outbound-confirm`, {
        method: "POST",
        body: JSON.stringify({
          message_id: result.outbound_message_id,
          delivery_id: result.delivery_id || undefined,
          external_message_id: sent.key.id,
          accepted_at: acceptedAt,
        }),
      });
    }
  } catch (error) {
    if (result.delivery_id && result.outbound_message_id) {
      // A rejected promise may still hide an accepted transport operation;
      // classify it as UNKNOWN and never send the same reply again here.
      await backend(`/channels/${channelId}/activation-failure`, {
        method: "POST",
        body: JSON.stringify({
          message_id: result.outbound_message_id,
          delivery_id: result.delivery_id,
          state: activationFailureState(error),
        }),
      }).catch((reportError) => {
        console.error(`[WhatsApp ${channelId}] Could not record activation outcome:`, (reportError as Error).message);
      });
    }
    throw error;
  }
}

export async function connectChannel(channelId: string): Promise<void> {
  const current = runtimes.get(channelId);
  if (current && !current.stopRequested) return;
  const config = await backend<ChannelConfig>(`/channels/${channelId}`);
  if (!config.enabled) throw new Error("The channel is disabled");
  await setStatus(channelId, config.auth_state ? "reconnecting" : "connecting");

  const { state, persist } = createDatabaseAuth(channelId, config.auth_state || undefined);
  const socket = makeWASocket({
    auth: state,
    logger,
    browser: Browsers.macOS("OpenLivery"),
    syncFullHistory: false,
    shouldSyncHistoryMessage: () => false,
    markOnlineOnConnect: false,
    generateHighQualityLinkPreview: false,
  });
  const runtime: ChannelRuntime = { socket, stopRequested: false };
  runtimes.set(channelId, runtime);

  socket.ev.on("creds.update", async (update) => {
    Object.assign(state.creds, update);
    try { await persist(); } catch (error) { console.error(`[WhatsApp ${channelId}] Could not save the session:`, (error as Error).message); }
  });

  socket.ev.on("messages.upsert", async ({ messages, type }) => {
    const incoming = directIncomingForUpsert(type, messages);
    console.info(
      `[WhatsApp ${channelId}] messages.upsert type=${type} count=${messages.length} ` +
      `accepted=${incoming.length} filtered=${messages.length - incoming.length}`,
    );
    for (const message of incoming) {
      try {
        await processIncoming(channelId, socket, message);
      } catch (error) {
        const detail = `Could not process an incoming message: ${(error as Error).message}`;
        console.error(`[WhatsApp ${channelId}] ${detail}`);
        // A processing/API failure does not mean the WhatsApp socket disconnected.
        // Keep the transport status intact; connection.update is the authority for
        // connected/reconnecting/error state.
      }
    }
  });

  socket.ev.on("connection.update", async ({ connection, lastDisconnect, qr }) => {
    try {
      if (qr) {
        const qrCode = await QRCode.toDataURL(qr, { width: 360, margin: 2, errorCorrectionLevel: "M" });
        await setStatus(channelId, "qr", { qr_code: qrCode });
      }
      if (connection === "open") {
        console.info(`[WhatsApp ${channelId}] Connection open`);
        await persist();
        await setStatus(channelId, "connected", {
          phone_number: cleanNumber(socket.user?.id),
          display_name: socket.user?.name || null,
        });
      }
      if (connection === "close") {
        const latest = runtimes.get(channelId);
        if (latest !== runtime) return;
        const code = errorCode(lastDisconnect?.error);
        console.warn(`[WhatsApp ${channelId}] Connection close; code=${code ?? "unknown"}`);
        const loggedOut = code === DisconnectReason.loggedOut || code === DisconnectReason.badSession;
        if (runtime.stopRequested || loggedOut) {
          runtimes.delete(channelId);
          await backend(`/channels/${channelId}/auth`, { method: "DELETE" });
          return;
        }
        await setStatus(channelId, "reconnecting", {
          error: code ? `WhatsApp closed the connection (${code}). Retrying…` : "Connection interrupted. Retrying…",
        });
        console.info(`[WhatsApp ${channelId}] Reconnect scheduled`);
        runtime.reconnectTimer = setTimeout(() => {
          runtimes.delete(channelId);
          void connectChannel(channelId).catch(async (error) => {
            await setStatus(channelId, "error", { error: (error as Error).message.slice(0, 500) }).catch(() => undefined);
          });
        }, 3000);
      }
    } catch (error) {
      await setStatus(channelId, "error", { error: (error as Error).message.slice(0, 500) }).catch(() => undefined);
    }
  });
}

export async function disconnectChannel(channelId: string): Promise<void> {
  const runtime = runtimes.get(channelId);
  if (runtime) {
    runtime.stopRequested = true;
    if (runtime.reconnectTimer) clearTimeout(runtime.reconnectTimer);
    try { await runtime.socket.logout(); } catch {}
    runtimes.delete(channelId);
  }
  await backend(`/channels/${channelId}/auth`, { method: "DELETE" });
}

export async function sendMessage(channelId: string, remoteJid: string, text: string): Promise<string> {
  const runtime = runtimes.get(channelId);
  if (!runtime || runtime.stopRequested) throw new Error("WhatsApp is not connected")
  const sent = await runtime.socket.sendMessage(remoteJid, { text });
  if (!sent?.key.id) throw new Error("WhatsApp did not confirm the send")
  return sent.key.id;
}

export async function restoreChannels(): Promise<void> {
  const channels = await backend<Array<{ id: string }>>("/channels");
  const results = await Promise.allSettled(channels.map((item) => connectChannel(item.id)));
  results.forEach((result, index) => {
    if (result.status === "rejected") console.error(`[WhatsApp ${channels[index]?.id}] Could not restore:`, result.reason?.message || result.reason);
  });
}

export async function shutdown(): Promise<void> {
  for (const runtime of runtimes.values()) {
    runtime.stopRequested = true;
    if (runtime.reconnectTimer) clearTimeout(runtime.reconnectTimer);
    runtime.socket.end(new Error("OpenLivery is shutting down"));
  }
  runtimes.clear();
}
