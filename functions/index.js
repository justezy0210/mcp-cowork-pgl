"use strict";

const { onRequest } = require("firebase-functions/v2/https");
const { defineSecret, defineString } = require("firebase-functions/params");
const { initializeApp } = require("firebase-admin/app");
const { getAuth } = require("firebase-admin/auth");
const { createHandler, SshRelay } = require("./relay");

initializeApp();
const sshConfig = defineSecret("COWORK_WEB_SSH");
const runtimeAccount = defineString("RELAY_SERVICE_ACCOUNT");
let relay;

exports.coworkWeb = onRequest({
  region: "asia-east1",
  cpu: 1,
  memory: "512MiB",
  minInstances: 0,
  maxInstances: 1,
  concurrency: 8,
  timeoutSeconds: 20,
  invoker: "public",
  serviceAccount: runtimeAccount,
  secrets: [sshConfig],
}, createHandler({
  verify: (token) => getAuth().verifyIdToken(token),
  forward: (request) => {
    relay ||= new SshRelay(JSON.parse(sshConfig.value()));
    return relay.request(request);
  },
}));
