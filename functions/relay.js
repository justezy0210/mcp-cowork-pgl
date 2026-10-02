"use strict";

const { Client } = require("ssh2");
const { timingSafeEqual } = require("node:crypto");

const MAX_BODY = 64 * 1024;
const MAX_REPLY = 6 * 1024 * 1024;

function safePath(value) {
  if (typeof value !== "string" || value.length > 4096) return false;
  // Identifiers may contain dots; only actual traversal segments are forbidden.
  const path = value.split("?")[0];
  return /^\/v1\/web\/[A-Za-z0-9_./-]+$/.test(path)
    && !path.split("/").some((part) => part === "." || part === "..")
    && !/[\x00-\x20\x7f#\\]/.test(value)
    && !path.includes("//");
}

function createHandler({ verify, forward }) {
  return async (req, res) => {
    res.set("Cache-Control", "private, no-store");
    res.set("X-Content-Type-Options", "nosniff");
    const fail = (status, code) => res.status(status).json({ error: { code } });
    const path = req.originalUrl || req.url;
    if (!safePath(path)) return fail(404, "NOT_FOUND");
    if (!["GET", "POST", "DELETE"].includes(req.method)) return fail(405, "INVALID_REQUEST");
    const authorization = req.get("authorization") || "";
    if (!/^Bearer [A-Za-z0-9_.-]+$/.test(authorization) || authorization.length > 16384) {
      return fail(401, "UNAUTHENTICATED");
    }
    const body = req.rawBody || Buffer.alloc(0);
    if (body.length > MAX_BODY) return fail(413, "INVALID_REQUEST");
    if (body.length && (req.method !== "POST" || !/^application\/json(?:;|$)/i.test(req.get("content-type") || ""))) {
      return fail(400, "INVALID_REQUEST");
    }
    try {
      const claims = await verify(authorization.slice(7));
      if (!claims.email_verified || claims.firebase?.sign_in_provider !== "google.com") {
        return fail(401, "UNAUTHENTICATED");
      }
    } catch (error) {
      // Network/certificate outages must not sign users out of the web app.
      const rejected = new Set(["auth/argument-error", "auth/invalid-id-token", "auth/id-token-expired", "auth/id-token-revoked", "auth/user-disabled"]);
      return fail(rejected.has(error.code) ? 401 : 503,
        rejected.has(error.code) ? "UNAUTHENTICATED" : "WEB_AUTH_UNAVAILABLE");
    }
    try {
      // The hub independently checks revocation, account approval and each permission.
      const reply = await forward({ method: req.method, path, authorization, body: body.toString("base64") });
      if (!Number.isInteger(reply.status) || reply.status < 200 || reply.status >= 600
          || typeof reply.body !== "string" || reply.body.length > MAX_REPLY) throw new Error("BAD_REPLY");
      res.status(reply.status).type("application/json").send(Buffer.from(reply.body, "base64"));
    } catch {
      // Never log request bodies, headers, tokens, SSH configuration or upstream responses.
      return fail(502, "HUB_UNAVAILABLE");
    }
  };
}

class SshRelay {
  constructor(config, ClientType = Client) {
    if (!config.host || !Number.isInteger(config.port) || !config.username || !config.privateKey
        || !/^[a-f0-9]{64}$/.test(config.hostSha256 || "")) throw new Error("INVALID_SSH_CONFIG");
    this.config = config;
    this.ClientType = ClientType;
    this.client = null;
    this.connecting = null;
  }

  async connect() {
    if (this.client) return this.client;
    if (this.connecting) return this.connecting;
    this.connecting = new Promise((resolve, reject) => {
      const client = new this.ClientType();
      const clear = () => { if (this.client === client) this.client = null; };
      client.on("error", (error) => { clear(); reject(error); });
      client.on("close", () => { clear(); reject(new Error("SSH_CLOSED")); });
      client.once("ready", () => { this.client = client; resolve(client); });
      client.connect({
        host: this.config.host, port: this.config.port, username: this.config.username,
        privateKey: this.config.privateKey, readyTimeout: 5000,
        hostHash: "sha256",
        hostVerifier: (hash) => typeof hash === "string" && hash.length === 64
          && timingSafeEqual(Buffer.from(hash), Buffer.from(this.config.hostSha256)),
        algorithms: { serverHostKey: ["ssh-ed25519"] },
        // Reuse a live connection; no background heartbeat needs a warm instance.
        keepaliveInterval: 0,
      });
    });
    try { return await this.connecting; }
    finally { this.connecting = null; }
  }

  async request(payload) {
    const client = await this.connect();
    return new Promise((resolve, reject) => {
      let channel, done = false;
      const finish = (error, result) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        client.removeListener("close", disconnected);
        channel?.destroy();
        if (error) reject(error); else resolve(result);
      };
      const disconnected = () => finish(new Error("SSH_CLOSED"));
      const timer = setTimeout(() => finish(new Error("HUB_TIMEOUT")), 13000);
      client.once("close", disconnected);
      client.exec("cowork-web-relay", (error, stream) => {
        if (error) return finish(error);
        if (done) { stream.destroy(); return; }
        channel = stream;
        const chunks = [];
        let size = 0;
        stream.on("data", (chunk) => {
          size += chunk.length;
          if (size > MAX_REPLY) return finish(new Error("REPLY_TOO_LARGE"));
          chunks.push(chunk);
        });
        stream.stderr.resume();
        stream.once("error", finish);
        stream.once("close", (code) => {
          if (code !== 0) return finish(new Error("RELAY_FAILED"));
          try { finish(null, JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
          catch { finish(new Error("BAD_REPLY")); }
        });
        // Never retry: a failed connection may have already committed a POST.
        stream.end(JSON.stringify(payload));
      });
    });
  }
}

module.exports = { createHandler, SshRelay, safePath };
