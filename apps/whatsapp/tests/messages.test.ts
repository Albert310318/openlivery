import assert from "node:assert/strict";
import test from "node:test";
import type { WAMessage } from "@whiskeysockets/baileys";
import { directIncomingForUpsert, incomingText, isDirectIncoming, trustedPhoneJid } from "../src/messages.js";

function message(overrides: Partial<WAMessage> = {}): WAMessage {
  return {
    key: { id: "abc", remoteJid: "573001234567@s.whatsapp.net", fromMe: false },
    message: { conversation: " Hello from WhatsApp " },
    messageTimestamp: 1,
    ...overrides,
  } as WAMessage;
}

test("extracts text from a direct conversation", () => {
  const item = message();
  assert.equal(isDirectIncoming(item), true);
  assert.equal(incomingText(item), "Hello from WhatsApp");
});

test("processes live direct notify events and rejects history append events", () => {
  const item = message();
  assert.deepEqual(directIncomingForUpsert("notify", [item]), [item]);
  assert.deepEqual(directIncomingForUpsert("append", [item]), []);
  assert.deepEqual(directIncomingForUpsert("replace", [item]), []);
});

test("filters own, group, status, and newsletter messages", () => {
  assert.equal(isDirectIncoming(message({ key: { id: "a", remoteJid: "57300@s.whatsapp.net", fromMe: true } })), false);
  assert.equal(isDirectIncoming(message({ key: { id: "b", remoteJid: "group@g.us", fromMe: false } })), false);
  assert.equal(isDirectIncoming(message({ key: { id: "c", remoteJid: "status@broadcast", fromMe: false } })), false);
  assert.equal(isDirectIncoming(message({ key: { id: "d", remoteJid: "updates@newsletter", fromMe: false } })), false);
  assert.deepEqual(directIncomingForUpsert("append", [
    message({ key: { id: "a", remoteJid: "57300@s.whatsapp.net", fromMe: true } }),
    message({ key: { id: "b", remoteJid: "group@g.us", fromMe: false } }),
    message({ key: { id: "c", remoteJid: "status@broadcast", fromMe: false } }),
  ]), []);
});

test("preserves the external message ID across duplicate deliveries for API deduplication", () => {
  const item = message({ key: { id: "stable-external-id", remoteJid: "57300@s.whatsapp.net", fromMe: false } });
  const first = directIncomingForUpsert("notify", [item]);
  const replay = directIncomingForUpsert("append", [item]);
  assert.equal(first[0]?.key.id, "stable-external-id");
  assert.deepEqual(replay, []);
});

test("uses a valid phone alternate only for linked-identity direct messages", () => {
  const lid = message({
    key: {
      id: "lid-message",
      remoteJid: "opaque-identity@lid",
      remoteJidAlt: "573001234567@s.whatsapp.net",
      fromMe: false,
    },
  });
  assert.equal(isDirectIncoming(lid), true);
  assert.equal(trustedPhoneJid(lid), "573001234567@s.whatsapp.net");
  assert.equal(lid.key.remoteJid, "opaque-identity@lid");
});

test("rejects missing, malformed, and non-phone alternates", () => {
  assert.equal(trustedPhoneJid(message({ key: { id: "a", remoteJid: "opaque@lid", fromMe: false } })), null);
  assert.equal(trustedPhoneJid(message({ key: { id: "b", remoteJid: "opaque@lid", remoteJidAlt: "group@g.us", fromMe: false } })), null);
  assert.equal(trustedPhoneJid(message({ key: { id: "c", remoteJid: "opaque@lid", remoteJidAlt: "short@s.whatsapp.net", fromMe: false } })), null);
  assert.equal(trustedPhoneJid(message()), null);
});
