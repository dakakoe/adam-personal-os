// Talks to merge_api. Every network call lives here so the token is read in
// exactly one place and never reaches the LinkedIn page's world.
//
// Fetches from an MV3 service worker with a matching host permission are not
// subject to CORS, so merge_api needs no CORS change — and the Caddyfile's
// `@bearer` matcher sends any Authorization-bearing request straight to
// :9100, bypassing Authelia. Hence: token in the header, no cookies, no
// preflight.

const DEFAULT_API_BASE = "https://merge.example.com";

async function settings() {
  const { apiBase, token } = await chrome.storage.local.get(["apiBase", "token"]);
  return { apiBase: (apiBase || DEFAULT_API_BASE).replace(/\/+$/, ""), token: token || "" };
}

async function call(path, { method = "GET", body } = {}) {
  const { apiBase, token } = await settings();
  if (!token) {
    return { ok: false, status: 0, error: "no token configured", needsSetup: true };
  }
  let res;
  try {
    res = await fetch(`${apiBase}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      credentials: "omit",
    });
  } catch (e) {
    return { ok: false, status: 0, error: `can't reach ${apiBase} — ${e.message}` };
  }
  if (res.status === 401 || res.status === 403) {
    return { ok: false, status: res.status, error: "token rejected", needsSetup: true };
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty or non-JSON body — handled by !res.ok below */
  }
  if (!res.ok) {
    const detail = data && data.detail ? JSON.stringify(data.detail) : `HTTP ${res.status}`;
    return { ok: false, status: res.status, error: detail };
  }
  return { ok: true, status: res.status, data };
}

const HANDLERS = {
  lookup: ({ vanity }) => call(`/api/capture/linkedin/${encodeURIComponent(vanity)}`),
  save: ({ profile }) => call("/api/capture/linkedin", { method: "POST", body: profile }),
  settings: async () => ({ ok: true, data: await settings() }),
};

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const handler = HANDLERS[msg && msg.type];
  if (!handler) return false;
  handler(msg).then(sendResponse, (e) => sendResponse({ ok: false, error: String(e) }));
  return true; // async response
});
