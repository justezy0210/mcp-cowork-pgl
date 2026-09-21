import { clearTokens, message, showTokens } from "./tokens.js";

const $ = (id) => document.getElementById(id);
const errors = {
  UNAUTHENTICATED: "로그인이 만료됐거나 유효하지 않습니다. 다시 로그인하세요.",
  WEB_NOT_CONFIGURED: "관리자가 웹 로그인 설정을 준비하고 있습니다.",
  WEB_AUTH_UNAVAILABLE: "로그인을 확인할 수 없습니다. 잠시 후 다시 시도하거나 관리자에게 문의하세요.",
  WEB_ACCOUNT_NOT_LINKED: "아직 허브 계정과 연결되지 않았습니다. 아래 계정 식별자를 관리자에게 전달하세요.",
  INVALID_REQUEST: "입력한 내용을 확인하세요.",
  NOT_FOUND: "해당 토큰을 찾을 수 없습니다. 목록을 새로고침하세요.",
};
let current = null, auth, sdk, generation = 0;

async function start() {
  const response = await fetch("./config.json", { cache: "no-store", redirect: "error" });
  if (!response.ok) throw new Error("CONFIG_UNAVAILABLE");
  const config = await response.json();
  if (!config.enabled) {
    $("login").textContent = "로그인 설정 대기 중";
    $("login-help").textContent = "관리자가 Firebase 로그인과 허브 계정 연결을 준비하면 이용할 수 있습니다.";
    return;
  }
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
  if (location.protocol !== "https:" && !local) throw new Error("HTTPS_REQUIRED");
  const base = new URL(config.api_base_url || location.origin);
  if (base.protocol !== "https:" && !(base.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(base.hostname))) {
    throw new Error("HTTPS_REQUIRED");
  }
  const { initializeApp } = await import("https://www.gstatic.com/firebasejs/12.19.0/firebase-app.js");
  sdk = await import("https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js");
  auth = sdk.getAuth(initializeApp(config.firebase));
  await sdk.setPersistence(auth, sdk.inMemoryPersistence);

  async function request(path, options = {}) {
    if (!current) throw new Error(errors.UNAUTHENTICATED);
    const user = current;
    let token;
    try { token = await user.getIdToken(); }
    catch { throw new Error(errors.UNAUTHENTICATED); }
    if (current !== user) throw new Error(errors.UNAUTHENTICATED);
    let result;
    try {
      result = await fetch(base.href.replace(/\/$/, "") + "/v1/web" + path, {
        ...options, cache: "no-store", credentials: "omit", redirect: "error",
        headers: { Authorization: "Bearer " + token, ...(options.body ? { "Content-Type": "application/json" } : {}) },
      });
    } catch { throw new Error("허브에 연결할 수 없습니다. 네트워크와 허브 주소를 확인하세요."); }
    let body;
    try { body = await result.json(); }
    catch { throw new Error("허브 응답을 확인할 수 없습니다."); }
    if (!result.ok) {
      const error = new Error(errors[body?.error?.code] || "요청을 처리할 수 없습니다. 잠시 후 다시 시도하세요.");
      error.code = body?.error?.code;
      throw error;
    }
    return body;
  }

  sdk.onAuthStateChanged(auth, async (user) => {
    const revision = ++generation;
    current = user;
    clearTokens();
    message();
    $("login-panel").hidden = false;
    $("login-help").textContent = "관리자가 허용한 Google 계정으로 로그인하세요.";
    if (!user) return;
    $("account").hidden = false;
    $("user-name").textContent = user.email || "로그인됨";
    try {
      const profile = await request("/me");
      if (revision === generation) await showTokens(request, profile);
    } catch (error) {
      if (revision !== generation) return;
      message(error.message);
      if (error.code === "WEB_ACCOUNT_NOT_LINKED") {
        $("login-help").textContent = "Firebase 계정 식별자: " + user.uid;
      }
    }
  });

  $("login").disabled = false;
  $("login").textContent = "Google로 로그인";
  $("login").onclick = async () => {
    $("login").disabled = true;
    message();
    try {
      const provider = new sdk.GoogleAuthProvider();
      provider.setCustomParameters({ prompt: "select_account" });
      await sdk.signInWithPopup(auth, provider);
    } catch (error) {
      if (error.code !== "auth/popup-closed-by-user") message("Google 로그인을 완료하지 못했습니다. 팝업 허용과 로그인 설정을 확인하세요.");
    } finally { $("login").disabled = false; }
  };
  $("logout").onclick = async () => {
    generation += 1;
    current = null;
    clearTokens();
    $("login-panel").hidden = false;
    try { await sdk.signOut(auth); }
    catch { message("로그아웃을 마치려면 이 창을 닫아 주세요."); }
  };
}

window.addEventListener("pagehide", clearTokens);
start().catch((error) => {
  $("login").textContent = "로그인 준비 필요";
  $("login").disabled = true;
  $("login-help").textContent = error.message === "HTTPS_REQUIRED"
    ? "개인 토큰을 받으려면 관리자가 제공한 HTTPS 주소로 접속하세요."
    : "로그인 설정을 불러오지 못했습니다. 네트워크와 관리자 설정을 확인하세요.";
});
