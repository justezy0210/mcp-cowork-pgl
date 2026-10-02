"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { createHandler, SshRelay, safePath } = require("../relay");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");

const claims = { email_verified: true, firebase: { sign_in_provider: "google.com" } };
const jsonReply = (value, status = 200) => ({ status, body: Buffer.from(JSON.stringify(value)).toString("base64") });
async function call(overrides = {}, deps = {}) {
  let forwarded = 0;
  const headers = { authorization: "Bearer test.token.signature", "content-type": "application/json", ...overrides.headers };
  const req = { method: "GET", originalUrl: "/v1/web/profile", rawBody: Buffer.alloc(0), ...overrides, get: (name) => headers[name] };
  const res = { headers: {}, statusCode: 200, set(k, v) { this.headers[k] = v; return this; }, status(s) { this.statusCode = s; return this; }, type() { return this; }, json(v) { this.body = v; return this; }, send(v) { this.body = JSON.parse(v); return this; } };
  await createHandler({ verify: async () => claims, forward: async () => { forwarded++; return jsonReply({ user_id: "alice" }); }, ...deps })(req, res);
  return { res, forwarded };
}

test("private web response and forbidden API/path/method boundaries", async () => {
  const { res } = await call();
  assert.deepEqual(res.body, { user_id: "alice" });
  assert.equal(res.headers["Cache-Control"], "private, no-store");
  for (const originalUrl of ["/healthz", "/v1/admin/users", "/v1/jobs", "/v1/web/../jobs", "/v1/web/%2e%2e/jobs", "//example.org/", "/v1/web//profile", "/v1/web/profile\r\nX: foo"]) {
    const result = await call({ originalUrl });
    assert.equal(result.res.statusCode, 404);
    assert.equal(result.forwarded, 0);
  }
  assert.equal((await call({ method: "PUT" })).res.statusCode, 405);
  assert.equal(safePath("/v1/web/jobs?limit=20&cursor=abc%3D"), true);
  assert.equal(safePath("/v1/web/admin/users/alice.lab/grants"), true);
});

test("no SSH connection for missing/invalid/non-Google authentication", async () => {
  const missing = await call({ headers: { authorization: "" } });
  assert.equal(missing.res.statusCode, 401);
  assert.equal(missing.forwarded, 0);
  for (const result of [
    await call({}, { verify: async () => { throw Object.assign(new Error(), { code: "auth/invalid-id-token" }); } }),
    await call({}, { verify: async () => ({ ...claims, email_verified: false }) }),
  ]) {
    assert.equal(result.res.statusCode, 401);
    assert.equal(result.forwarded, 0);
  }
  assert.equal((await call({}, { verify: async () => { throw new Error("network"); } })).res.statusCode, 503);
});

test("bounded bodies; original mutation preserved with hub permission error", async () => {
  assert.equal((await call({ method: "POST", rawBody: Buffer.alloc(65537) })).res.statusCode, 413);
  assert.equal((await call({ method: "POST", rawBody: Buffer.from("x"), headers: { "content-type": "text/plain" } })).res.statusCode, 400);
  let received;
  const rawBody = Buffer.from('{"name":"my-MCP"}');
  const { res } = await call({ method: "POST", originalUrl: "/v1/web/tokens", rawBody }, {
    forward: async (request) => { received = request; return jsonReply({ error: { code: "FORBIDDEN" } }, 403); },
  });
  assert.equal(Buffer.from(received.body, "base64").toString(), rawBody.toString());
  assert.equal(received.authorization, "Bearer test.token.signature");
  assert.equal(res.statusCode, 403);
});

test("SSH failure is sanitized and never retries a mutation", async () => {
  let calls = 0;
  const { res } = await call({ method: "POST", rawBody: Buffer.from("{}") }, {
    forward: async () => { calls++; throw new Error("private diagnostic"); },
  });
  assert.equal(calls, 1);
  assert.equal(res.statusCode, 502);
  assert.equal(JSON.stringify(res.body).includes("private diagnostic"), false);
});

test("concurrent requests share SSH connection, pin host key and do not run user commands", async () => {
  let connections = 0;
  const commands = [];
  class FakeClient extends EventEmitter {
    connect(options) {
      connections++;
      assert.equal(options.hostVerifier("a".repeat(64)), true);
      assert.equal(options.hostVerifier("b".repeat(64)), false);
      process.nextTick(() => this.emit("ready"));
    }
    exec(command, callback) {
      commands.push(command);
      const stream = new EventEmitter();
      stream.stderr = new PassThrough();
      stream.destroy = () => {};
      stream.end = () => process.nextTick(() => {
        stream.emit("data", Buffer.from(JSON.stringify(jsonReply({ ok: true }))));
        stream.emit("close", 0);
      });
      callback(null, stream);
    }
  }
  const relay = new SshRelay({ host: "test", port: 22, username: "relay", privateKey: "synthetic", hostSha256: "a".repeat(64) }, FakeClient);
  const results = await Promise.all([relay.request({}), relay.request({})]);
  assert.equal(connections, 1);
  assert.deepEqual(commands, ["cowork-web-relay", "cowork-web-relay"]);
  assert.equal(results.length, 2);
});
