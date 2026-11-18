const DEFAULT_API_BASE = "https://merge.example.com";

const $ = (id) => document.getElementById(id);
const status = (text, kind = "") => {
  $("status").textContent = text;
  $("status").className = `status ${kind}`;
};

// Report what's actually PERSISTED, not just what's in the box. A password
// field full of dots looks identical whether it came from storage or from
// something you typed and never saved.
chrome.storage.local.get(["apiBase", "token"]).then(({ apiBase, token }) => {
  $("apiBase").value = apiBase || DEFAULT_API_BASE;
  $("token").value = token || "";
  status(token ? "A token is saved." : "No token saved yet — paste one and press Save.");
});

// A non-default API base isn't covered by the manifest's host_permissions, so
// ask for it explicitly rather than letting fetch fail with a blank error.
//
// Called with NO preceding await: chrome.permissions.request() is only valid
// during a user gesture, and any await before it spends the gesture — which is
// how the first version of this file threw here and silently skipped the save
// below. request() resolves true without prompting when the origin is already
// granted, so the contains() precheck it used to do bought nothing.
function requestHostPermission(base) {
  const origin = `${new URL(base).origin}/*`;
  return chrome.permissions.request({ origins: [origin] });
}

$("save").addEventListener("click", (event) => {
  const apiBase = ($("apiBase").value || DEFAULT_API_BASE).trim().replace(/\/+$/, "");
  const token = $("token").value.trim();
  let url;
  try {
    url = new URL(apiBase);
  } catch {
    return status("That API base isn't a valid URL.", "error");
  }
  if (url.protocol !== "https:" && url.hostname !== "localhost") {
    return status("Use https — the token travels in the header.", "error");
  }
  if (!token) return status("Paste the bearer token first.", "error");

  // Permission first, while the click's gesture is still live — but the token
  // is stored regardless of how it goes. A storage write must never be gated
  // on a permission prompt; that's what made Save fail silently before.
  let granted;
  try {
    granted = requestHostPermission(apiBase);
  } catch (e) {
    granted = Promise.resolve(false);
  }
  chrome.storage.local
    .set({ apiBase, token })
    .then(() => granted.catch(() => false))
    .then((ok) => {
      status(
        ok
          ? "Saved."
          : `Saved, but this browser hasn't granted access to ${url.host} — ` +
            "if Test connection fails, allow the site in chrome://extensions.",
        ok ? "ok" : "error"
      );
    })
    .catch((e) => status(`Couldn't save: ${e.message}`, "error"));
  event.preventDefault();
});

$("test").addEventListener("click", async () => {
  status("Testing…");
  const res = await chrome.runtime.sendMessage({
    type: "lookup",
    vanity: "adam-connection-test",
  });
  if (res.ok) status("Connected — the API answered.", "ok");
  else status(`Failed: ${res.error}`, "error");
});
