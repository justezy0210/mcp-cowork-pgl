const $ = (id) => document.getElementById(id);
let api, epoch = 0, pending = null, items = [], more = false, revoking = null;

export function message(text = "") {
  $("message").textContent = text;
  $("message").hidden = !text;
}

export function clearTokens() {
  epoch += 1;
  api = null;
  pending = null;
  items = [];
  revoking = null;
  $("token-list").replaceChildren();
  $("download-panel").hidden = true;
  $("workspace").hidden = true;
  $("account").hidden = true;
  $("revoke-dialog").close();
  $("create-form").reset();
}

function render() {
  $("token-list").replaceChildren();
  $("empty").hidden = items.length !== 0;
  $("load-more").hidden = !more;
  for (const token of items) {
    const row = document.createElement("li");
    row.className = "token-row";
    const info = document.createElement("div");
    info.className = "token-info";
    const name = document.createElement("span");
    name.className = "token-name";
    name.textContent = token.name;
    const detail = document.createElement("span");
    detail.className = "token-detail";
    detail.textContent = new Date(token.created_at * 1000).toLocaleString("ko-KR") + " 발급";
    const state = document.createElement("span");
    const revoked = token.revoked_at !== null;
    state.className = "token-status" + (revoked ? " revoked" : "");
    state.textContent = revoked ? "폐기됨" : "사용 가능";
    detail.append(state);
    info.append(name, detail);
    row.append(info);
    if (!revoked) {
      const button = document.createElement("button");
      button.className = "quiet";
      button.textContent = "폐기";
      button.setAttribute("aria-label", token.name + " 토큰 폐기");
      button.onclick = () => {
        revoking = token;
        $("revoke-name").textContent = token.name;
        $("revoke-confirm").disabled = false;
        $("revoke-dialog").showModal();
      };
      row.append(button);
    }
    $("token-list").append(row);
  }
}

async function list(append = false) {
  const generation = epoch;
  $("refresh").disabled = $("load-more").disabled = true;
  try {
    const after = append && items.length ? "&after=" + encodeURIComponent(items.at(-1).id) : "";
    const result = await api("/tokens?limit=100" + after);
    if (generation !== epoch) return;
    items = append ? [...items, ...result] : result;
    more = result.length === 100;
    render();
  } catch (error) {
    if (generation === epoch) message(error.message);
  } finally {
    if (generation === epoch) $("refresh").disabled = $("load-more").disabled = false;
  }
}

export async function showTokens(request, user) {
  clearTokens();
  api = request;
  $("login-panel").hidden = true;
  $("account").hidden = $("workspace").hidden = false;
  $("user-name").textContent = user.user_id;
  $("create").disabled = false;
  await list();
}

$("refresh").onclick = () => { message(); list(); };
$("load-more").onclick = () => list(true);
$("create-form").onsubmit = async (event) => {
  event.preventDefault();
  const name = $("token-name").value.trim();
  if (!name || !api || pending || $("create").disabled) return;
  const generation = epoch;
  $("create").disabled = true;
  message();
  try {
    const token = await api("/tokens", { method: "POST", body: JSON.stringify({ name }) });
    if (generation !== epoch) return;
    pending = token;
    $("issued-name").textContent = token.name;
    $("download-panel").hidden = false;
    $("download").focus();
    await list();
  } catch (error) {
    if (generation === epoch) message(error.message + " 발급 결과가 불확실하면 목록을 확인한 뒤 다시 시도하세요.");
  } finally {
    if (generation === epoch) $("create").disabled = pending !== null;
  }
};

$("download").onclick = () => {
  if (!pending) return;
  const url = URL.createObjectURL(new Blob([pending.token + "\n"], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "user.token";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
$("dismiss-download").onclick = () => {
  pending = null;
  $("download-panel").hidden = true;
  $("create").disabled = false;
  $("create-form").reset();
  $("token-name").focus();
};
$("revoke-cancel").onclick = () => $("revoke-dialog").close();
$("revoke-confirm").onclick = async () => {
  if (!revoking || !api || $("revoke-confirm").disabled) return;
  const generation = epoch, token = revoking;
  $("revoke-confirm").disabled = true;
  try {
    await api("/tokens/" + encodeURIComponent(token.id), { method: "DELETE" });
    if (generation !== epoch) return;
    if (pending?.id === token.id) {
      pending = null;
      $("download-panel").hidden = true;
      $("create").disabled = false;
    }
    $("revoke-dialog").close();
    message();
    await list();
  } catch (error) {
    if (generation === epoch) message(error.message);
  } finally {
    if (generation === epoch) $("revoke-confirm").disabled = false;
  }
};
