'use strict';
// Use the real Firebase SDK and an actual browser profile across process restarts.
// Only the Google credential exchange and application API use synthetic responses.
const { chromium } = require(process.env.COWORK_PLAYWRIGHT_PACKAGE || 'playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fixtures = require('./web_react_fixtures.cjs');
const root = path.resolve(__dirname, '..');
const source = path.join(root, 'src/cowork_hub/web/dist');
const output = process.env.COWORK_BROWSER_OUTPUT || path.join(root, '.local/verification/auth-persistence');
const authModule = 'https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js';
const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url');

(async () => {
  await fs.mkdir(output, { recursive: true });
  const profile = await fs.mkdtemp(path.join(output, 'browser-profile-'));
  const headers = JSON.parse(spawnSync(path.join(root, '.venv/bin/python'), ['-c',
    'import json; from cowork_hub.web_api import security_headers; print(json.dumps(security_headers()))'],
  { cwd: root, encoding: 'utf8' }).stdout);
  const server = http.createServer(async (request, response) => {
    try {
      const name = new URL(request.url, 'http://localhost').pathname.slice(1) || 'index.html';
      for (const [key, value] of Object.entries(headers)) response.setHeader(key, value);
      if (name === 'config.json') {
        response.setHeader('Content-Type', 'application/json');
        response.end(JSON.stringify({ enabled: true, api_base_url: '', firebase: {
          apiKey: 'synthetic-api-key', projectId: 'fixture-project', authDomain: 'localhost',
        } }));
        return;
      }
      if (!['index.html', 'favicon.svg'].includes(name) && !/^assets\/[\w.-]+\.(js|css|png)$/.test(name)) {
        response.writeHead(404); response.end(); return;
      }
      response.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript' :
        name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' :
          name.endsWith('.svg') ? 'image/svg+xml' : 'text/html');
      response.end(await fs.readFile(path.join(source, name)));
    } catch { response.writeHead(500); response.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const errors = [];
  let exchanges = 0, context, holdProfile = false;
  const pendingProfiles = [];
  const now = Math.floor(Date.now() / 1000);
  const credential = encode({ alg: 'RS256', typ: 'JWT' }) + '.' + encode({
    sub: 'google-alice', user_id: 'google-alice', aud: 'fixture-project',
    iss: 'https://securetoken.google.com/fixture-project', iat: now, exp: now + 3600,
    auth_time: now, email: 'alice@example.test', email_verified: true,
    firebase: { sign_in_provider: 'google.com' },
  }) + '.synthetic-signature';
  async function launch() {
    const browser = await chromium.launchPersistentContext(profile, { headless: true, args: ['--no-sandbox'] });
    browser.setDefaultTimeout(30000);
    browser.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    for (const page of browser.pages()) page.on('pageerror', error => errors.push(error.message));
    await browser.route(authModule, route => route.fulfill({ contentType: 'text/javascript', body: `
      export * from '${authModule}?cowork-persistence-test=1';
      import {signInWithCredential, GoogleAuthProvider} from '${authModule}?cowork-persistence-test=1';
      export function signInWithPopup(auth) {
        return signInWithCredential(auth, GoogleAuthProvider.credential('synthetic-google-credential'));
      }
    ` }));
    await browser.route('https://identitytoolkit.googleapis.com/**', route => {
      const endpoint = new URL(route.request().url()).pathname;
      let body;
      if (endpoint.endsWith('/accounts:signInWithIdp')) {
        exchanges++;
        body = { localId: 'google-alice', email: 'alice@example.test', emailVerified: true,
          providerId: 'google.com', idToken: credential, refreshToken: 'synthetic-refresh', expiresIn: '3600' };
      } else if (endpoint.endsWith('/accounts:lookup')) {
        body = { users: [{ localId: 'google-alice', email: 'alice@example.test', emailVerified: true,
          providerUserInfo: [{ providerId: 'google.com', rawId: 'google-alice', email: 'alice@example.test' }] }] };
      } else {
        errors.push('Unexpected Firebase endpoint: ' + endpoint);
        return route.abort();
      }
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    });
    await browser.route('**/v1/web/**', route => {
      const endpoint = new URL(route.request().url()).pathname;
      assert.equal(route.request().headers().authorization, 'Bearer ' + credential);
      const body = endpoint === '/v1/web/profile' ? fixtures.profile :
        endpoint === '/v1/web/servers' ? fixtures.nodes : null;
      assert(body, 'Unexpected application endpoint: ' + endpoint);
      if (endpoint === '/v1/web/profile' && holdProfile) { pendingProfiles.push(route); return; }
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    });
    return browser;
  }
  async function open() {
    const page = await context.newPage();
    await page.goto(origin + '/#servers');
    return page;
  }
  try {
    context = await launch();
    let page = await open();
    await page.locator('#login:enabled').click();
    await page.locator('#portal').waitFor();
    assert.equal(exchanges, 1);
    await page.reload();
    await page.locator('#portal').waitFor();
    assert.equal(exchanges, 1, 'reload must not require another Google sign-in');
    await page.close();
    page = await open();
    await page.locator('#portal').waitFor();
    assert.equal(exchanges, 1, 'a new tab must restore authentication');
    await context.close();
    holdProfile = true;
    context = await launch();
    page = await open();
    await page.locator('#portal').waitFor();
    assert.equal(pendingProfiles.length, 1, 'cached profile must restore the UI before the network check completes');
    holdProfile = false;
    await pendingProfiles.shift().fulfill({ contentType: 'application/json', body: JSON.stringify(fixtures.profile) });
    assert.equal(exchanges, 1, 'a new browser process must restore authentication');
    const other = await open();
    await other.locator('#portal').waitFor();
    await page.locator('#logout').click();
    await page.locator('#login:enabled').waitFor();
    await other.locator('#login:enabled').waitFor();
    assert.equal(await other.locator('#portal').count(), 0);
    await context.close();
    context = await launch();
    page = await open();
    await page.locator('#login:enabled').waitFor();
    assert.equal(await page.locator('#portal').count(), 0, 'explicit sign-out must survive a browser restart');
    assert.equal(exchanges, 1);
    assert.deepEqual(errors, []);
    const report = { real_firebase_sdk: '12.19.0', synthetic_credentials: true,
      reload_preserves_login: true, new_tab_preserves_login: true, browser_restart_preserves_login: true,
      browser_restart_renders_before_profile_network_response: true,
      logout_syncs_across_tabs: true, logout_survives_browser_restart: true,
      google_exchanges: exchanges, page_errors: errors, production_mutations: 0 };
    await fs.writeFile(path.join(output, 'persistence-report.json'), JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify(report));
  } catch (error) {
    const pages = [];
    for (const page of context?.pages() || []) {
      if (!page.url().startsWith(origin)) continue;
      pages.push(await page.evaluate(async module => ({
        text: document.body.innerText, keys: Object.keys(localStorage),
        authUid: (await import(module)).getAuth().currentUser?.uid || null,
      }), authModule));
    }
    await fs.writeFile(path.join(output, 'failure.json'), JSON.stringify({ errors, pages }, null, 2));
    throw error;
  } finally {
    await context?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
