/**
 * Thoth – WhatsApp Bridge (Baileys)
 * ====================================
 * Node.js subprocess that manages a Baileys WhatsApp client.
 * Communicates with the Python parent process via JSON-RPC over
 * stdin (commands) and stdout (events/responses).
 *
 * No browser required — connects via WebSocket directly.
 *
 * Protocol:
 *   Python → Node (stdin):
 *     {"id": 1, "method": "send_message", "params": {"chatId": "...", "text": "..."}}
 *     {"id": 2, "method": "send_media",   "params": {"chatId": "...", "mediaData": "base64...", "filename": "...", "caption": "..."}}
 *
 *   Node → Python (stdout):
 *     {"type": "qr",           "qr": "..."}          — QR code for authentication
 *     {"type": "ready"}                               — Client authenticated
 *     {"type": "disconnected", "reason": "..."}       — Client disconnected
 *     {"type": "message",      "from": "...", "body": "...", ...}  — Inbound message
 *     {"type": "response",     "id": 1, "ok": true}  — Response to a command
 *     {"type": "error",        "error": "..."}        — Error
 */

import {
  makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  downloadMediaMessage,
} from "@whiskeysockets/baileys";
import { Boom } from "@hapi/boom";
import pino from "pino";
import { createInterface } from "readline";
import path from "path";
import { existsSync, mkdirSync, writeFileSync, rmSync } from "fs";
import { randomBytes } from "crypto";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// ── Configuration ───────────────────────────────────────────────────
const SESSION_DIR =
  process.env.WA_SESSION_DIR ||
  path.join(process.env.HOME || process.env.USERPROFILE || "~", ".thoth", "whatsapp_session");

// Reply prefix for agent messages (self-chat echo detection)
const REPLY_PREFIX = "\u{1F9FF} *Thoth*\n\u2500\u2500\u2500\u2500\u2500\u2500\n";

mkdirSync(SESSION_DIR, { recursive: true });

// Suppress Baileys' verbose output — only show warnings+errors
const logger = pino({ level: "warn" });

// ── Helpers ─────────────────────────────────────────────────────────
function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function logErr(msg) {
  process.stderr.write(`[wa-bridge] ${msg}\n`);
}

// ── YouTube link preview helper ──────────────────────────────────────
const _YT_RE = /(?:youtube\.com\/(?:watch\?v=|shorts\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})/;

async function fetchYouTubePreview(url) {
  try {
    const match = url.match(_YT_RE);
    if (!match) return null;
    const videoId = match[1];

    const oembedUrl = `https://www.youtube.com/oembed?url=${encodeURIComponent(url)}&format=json`;
    const [oembedResp, thumbResp] = await Promise.all([
      fetch(oembedUrl),
      fetch(`https://img.youtube.com/vi/${videoId}/hqdefault.jpg`),
    ]);
    const oembed = await oembedResp.json();
    const thumbBuffer = Buffer.from(await thumbResp.arrayBuffer());

    return {
      "canonical-url": url,
      "matched-text": url,
      title: oembed.title || "YouTube",
      description: oembed.author_name ? `By ${oembed.author_name}` : "",
      jpegThumbnail: thumbBuffer,
    };
  } catch {
    return null;
  }
}

// ── Echo prevention ─────────────────────────────────────────────────
const recentlySentIds = new Set();
const MAX_RECENT_IDS = 50;

function trackSentId(id) {
  if (!id) return;
  recentlySentIds.add(id);
  if (recentlySentIds.size > MAX_RECENT_IDS) {
    recentlySentIds.delete(recentlySentIds.values().next().value);
  }
}

// ── MIME helpers ────────────────────────────────────────────────────
function mimeToExtension(mime) {
  const map = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/gif": ".gif", "audio/ogg": ".ogg", "audio/ogg; codecs=opus": ".ogg",
    "audio/mpeg": ".mp3", "video/mp4": ".mp4",
  };
  return map[mime] || "";
}

// ── Socket state ────────────────────────────────────────────────────
let sock = null;
let connectionState = "disconnected";

// ── Connect ─────────────────────────────────────────────────────────
async function startSocket() {
  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    logger,
    printQRInTerminal: false,
    browser: ["Thoth", "Chrome", "120.0"],
    syncFullHistory: false,
    markOnlineOnConnect: false,
    getMessage: async () => ({ conversation: "" }),
  });

  // Persist credentials on update
  sock.ev.on("creds.update", saveCreds);

  // ── Connection lifecycle ────────────────────────────────────────
  sock.ev.on("connection.update", (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      emit({ type: "qr", qr });
      logErr("QR code generated — scan with phone");
    }

    if (connection === "close") {
      connectionState = "disconnected";
      const reason = new Boom(lastDisconnect?.error)?.output?.statusCode;

      if (reason === DisconnectReason.loggedOut) {
        logErr("Logged out — clearing session");
        try {
          rmSync(SESSION_DIR, { recursive: true, force: true });
          mkdirSync(SESSION_DIR, { recursive: true });
          logErr("Session cleared");
        } catch (e) {
          logErr("Failed to clear session: " + e.message);
        }
        emit({ type: "auth_failure", error: "Logged out by WhatsApp" });
        emit({ type: "disconnected", reason: "logged_out" });
        // Restart to show fresh QR
        setTimeout(startSocket, 2000);
      } else {
        const delay = reason === 515 ? 1000 : 3000;
        logErr(`Connection closed (reason: ${reason}). Reconnecting in ${delay}ms...`);
        emit({ type: "disconnected", reason: String(reason || "unknown") });
        setTimeout(startSocket, delay);
      }
    } else if (connection === "open") {
      connectionState = "connected";
      logErr(`WhatsApp connected — id=${sock.user?.id}, lid=${sock.user?.lid || "none"}`);
      emit({ type: "ready" });
    }
  });

  // ── Incoming messages ───────────────────────────────────────────
  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    // Accept both 'notify' (incoming) and 'append' (self-chat)
    if (type !== "notify" && type !== "append") return;

    for (const msg of messages) {
      if (!msg.message) continue;

      const chatId = msg.key.remoteJid;
      if (!chatId) continue;

      // Skip status broadcasts
      if (chatId === "status@broadcast") continue;

      const isGroup = chatId.endsWith("@g.us");
      const senderId = msg.key.participant || chatId;

      // ── Self-chat filtering ─────────────────────────────────────
      let isSelfChat = false;
      if (msg.key.fromMe) {
        // In groups, always skip own messages
        if (isGroup) continue;
        // In DMs, only accept self-chat (message to own number)
        // Normalise: strip device suffix (:0, :1) and domain (@s.whatsapp.net, @lid)
        const myJid = (sock.user?.id || "");
        const myLidJid = (sock.user?.lid || "");
        const stripToNumber = (jid) => jid.replace(/:.*@/, "@").replace(/@.*/, "");
        const myNumber = stripToNumber(myJid);
        const myLid = stripToNumber(myLidJid);
        const chatNumber = stripToNumber(chatId);
        // Also compare full normalised JIDs (strip device suffix only)
        const normJid = (jid) => jid.replace(/:.*@/, "@");
        isSelfChat = 
          (myNumber && chatNumber === myNumber) ||
          (myLid && chatNumber === myLid) ||
          normJid(chatId) === normJid(myJid) ||
          (myLidJid && normJid(chatId) === normJid(myLidJid));
        if (!isSelfChat) continue;
      }

      // ── Extract message content ─────────────────────────────────
      const messageContent = getMessageContent(msg);
      let body = "";
      let hasMedia = false;
      let mediaType = "";
      let mediaData = "";
      let filename = "file";

      if (messageContent.conversation) {
        body = messageContent.conversation;
      } else if (messageContent.extendedTextMessage?.text) {
        body = messageContent.extendedTextMessage.text;
      } else if (messageContent.imageMessage) {
        body = messageContent.imageMessage.caption || "";
        hasMedia = true;
        mediaType = messageContent.imageMessage.mimetype || "image/jpeg";
        filename = "image" + mimeToExtension(mediaType);
      } else if (messageContent.videoMessage) {
        body = messageContent.videoMessage.caption || "";
        hasMedia = true;
        mediaType = messageContent.videoMessage.mimetype || "video/mp4";
        filename = "video" + mimeToExtension(mediaType);
      } else if (messageContent.audioMessage || messageContent.pttMessage) {
        hasMedia = true;
        const audioMsg = messageContent.pttMessage || messageContent.audioMessage;
        mediaType = audioMsg?.mimetype || "audio/ogg";
        filename = "audio" + mimeToExtension(mediaType);
      } else if (messageContent.documentMessage) {
        body = messageContent.documentMessage.caption || "";
        hasMedia = true;
        mediaType = messageContent.documentMessage.mimetype || "application/octet-stream";
        filename = messageContent.documentMessage.fileName || "document";
      }

      // ── Echo prevention ─────────────────────────────────────────
      if (msg.key.fromMe) {
        if (recentlySentIds.has(msg.key.id)) continue;
        if (REPLY_PREFIX && body.startsWith(REPLY_PREFIX)) continue;
      }

      // Download media if present
      if (hasMedia) {
        try {
          const buffer = await downloadMediaMessage(msg, "buffer", {}, {
            logger,
            reuploadRequest: sock.updateMediaMessage,
          });
          mediaData = buffer.toString("base64");
        } catch (e) {
          logErr("Failed to download media: " + e.message);
        }
      }

      // Skip empty messages
      if (!body && !hasMedia) continue;

      const payload = {
        type: "message",
        from: msg.key.fromMe ? chatId : senderId,
        to: chatId,
        body: body || "",
        pushName: msg.pushName || "",
        isSelfChat: !!(msg.key.fromMe && isSelfChat),
        hasMedia,
        mediaType,
        mediaData,
        filename,
        timestamp: msg.messageTimestamp,
        isGroupMsg: isGroup,
        msgKey: msg.key,
      };

      emit(payload);
    }
  });
}

// ── Extract message content (unwrap ephemeral/viewOnce wrappers) ──
function getMessageContent(msg) {
  const content = msg?.message || {};
  if (content.ephemeralMessage?.message) return content.ephemeralMessage.message;
  if (content.viewOnceMessage?.message) return content.viewOnceMessage.message;
  if (content.viewOnceMessageV2?.message) return content.viewOnceMessageV2.message;
  if (content.documentWithCaptionMessage?.message) return content.documentWithCaptionMessage.message;
  return content;
}

// ── Commands from Python ────────────────────────────────────────────
const rl = createInterface({
  input: process.stdin,
  terminal: false,
});

rl.on("line", async (line) => {
  let msg;
  try {
    msg = JSON.parse(line);
  } catch (e) {
    logErr("Invalid JSON from Python: " + line);
    return;
  }

  const { id, method, params } = msg;

  try {
    switch (method) {
      case "send_message": {
        const { chatId, text, raw } = params;
        const msgText = (raw ? "" : REPLY_PREFIX) + text;
        const msgPayload = { text: msgText };
        if (raw && _YT_RE.test(text)) {
          const preview = await fetchYouTubePreview(text.trim());
          if (preview) msgPayload.linkPreview = preview;
        }
        const sent = await sock.sendMessage(chatId, msgPayload);
        trackSentId(sent?.key?.id);
        emit({ type: "response", id, ok: true, msgKey: sent?.key || null });
        break;
      }

      case "send_media": {
        const { chatId, mediaData, filePath, filename: fname, caption } = params;
        const buffer = mediaData ? Buffer.from(mediaData, "base64") : undefined;
        const ext = (fname || "").split(".").pop().toLowerCase();
        const videoMime = {
          mp4: "video/mp4",
          mov: "video/quicktime",
          avi: "video/x-msvideo",
          mkv: "video/x-matroska",
        }[ext] || "video/mp4";

        let msgPayload;
        if (["jpg", "jpeg", "png", "webp", "gif"].includes(ext)) {
          msgPayload = { image: buffer, caption: caption || undefined };
        } else if (["mp4", "mov", "avi", "mkv"].includes(ext)) {
          msgPayload = {
            video: filePath ? { url: filePath } : buffer,
            caption: caption || undefined,
            mimetype: videoMime,
            fileName: fname || undefined,
            ptv: false,
          };
        } else if (["ogg", "opus", "mp3", "wav", "m4a"].includes(ext)) {
          const audioMime = (ext === "ogg" || ext === "opus")
            ? "audio/ogg; codecs=opus" : "audio/mpeg";
          msgPayload = { audio: buffer, mimetype: audioMime, ptt: ext === "ogg" || ext === "opus" };
        } else {
          msgPayload = {
            document: buffer,
            fileName: fname || "file",
            caption: caption || undefined,
            mimetype: "application/octet-stream",
          };
        }

        const sent = await sock.sendMessage(chatId, msgPayload);
        trackSentId(sent?.key?.id);
        emit({ type: "response", id, ok: true });
        break;
      }

      case "send_reaction": {
        const { chatId, msgKey, emoji } = params;
        await sock.sendMessage(chatId, { react: { text: emoji, key: msgKey } });
        emit({ type: "response", id, ok: true });
        break;
      }

      case "send_presence": {
        const { chatId, presence } = params;
        await sock.sendPresenceUpdate(presence || "composing", chatId);
        emit({ type: "response", id, ok: true });
        break;
      }

      case "edit_message": {
        const { chatId, msgKey, text } = params;
        await sock.sendMessage(chatId, { text: REPLY_PREFIX + text, edit: msgKey });
        emit({ type: "response", id, ok: true });
        break;
      }

      case "get_status": {
        emit({
          type: "response",
          id,
          ok: true,
          state: connectionState,
        });
        break;
      }

      case "logout": {
        await sock.logout();
        emit({ type: "response", id, ok: true });
        break;
      }

      case "shutdown": {
        logErr("Shutting down via stdin command");
        try { sock.ws.close(); } catch {}
        process.exit(0);
        return;
      }

      default:
        emit({
          type: "response",
          id,
          ok: false,
          error: `Unknown method: ${method}`,
        });
    }
  } catch (e) {
    emit({ type: "response", id, ok: false, error: e.message });
    logErr(`Error handling ${method}: ${e.message}`);
  }
});

rl.on("close", () => {
  logErr("stdin closed, shutting down");
  try { sock.ws.close(); } catch {}
  process.exit(0);
});

// ── Graceful shutdown ───────────────────────────────────────────────
process.on("SIGTERM", () => {
  logErr("SIGTERM received");
  try { sock.ws.close(); } catch {}
  process.exit(0);
});

process.on("SIGINT", () => {
  logErr("SIGINT received");
  try { sock.ws.close(); } catch {}
  process.exit(0);
});

// ── Start ───────────────────────────────────────────────────────────
logErr("Starting WhatsApp bridge (Baileys)...");
startSocket().catch((e) => {
  emit({ type: "error", error: e.message });
  logErr("Failed to start: " + e.message);
  process.exit(1);
});
