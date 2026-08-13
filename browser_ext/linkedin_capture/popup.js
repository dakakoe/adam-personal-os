// Popup: scrape → let you correct it → POST /api/capture/linkedin.
//
// Nothing is sent until you press Save. The scrape itself is read-only except
// for "Pull contact info", which clicks LinkedIn's own Contact-info link and
// closes the overlay again — that one is behind its own button for exactly
// that reason.

const $ = (id) => document.getElementById(id);
const show = (id) => $(id).classList.remove("hidden");
const hide = (id) => $(id).classList.add("hidden");

const listValue = (raw) =>
  (raw || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

let scraped = null;
let tabId = null;

function blocked(message, action) {
  hide("loading");
  hide("form");
  $("blocked-msg").textContent = message;
  if (action) {
    $("blocked-action").textContent = action.label;
    $("blocked-action").onclick = action.run;
    show("blocked-action");
  }
  show("blocked");
}

async function runScraper(opts) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: (o) => globalThis.__adamScrapeLinkedIn(o),
    args: [opts || {}],
  });
  return result;
}

function fill(profile) {
  $("f-name").value = profile.full_name || "";
  $("f-title").value = profile.current_title || "";
  $("f-company").value = profile.current_company || "";
  $("f-location").value = profile.location || "";
  $("f-headline").value = profile.headline || "";
  $("f-emails").value = (profile.emails || []).join(", ");
  $("f-phones").value = (profile.phones || []).join(", ");
}

async function lookup(vanity) {
  const res = await chrome.runtime.sendMessage({ type: "lookup", vanity });
  if (!res.ok) {
    if (res.needsSetup) {
      blocked(`${res.error} — open Settings and paste your merge_api token.`, {
        label: "Open settings",
        run: () => chrome.runtime.openOptionsPage(),
      });
    }
    return null;
  }
  return res.data;
}

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/^https:\/\/([a-z]+\.)?linkedin\.com\/in\//i.test(tab.url || "")) {
    return blocked("Open a LinkedIn profile (linkedin.com/in/…) and try again.");
  }
  tabId = tab.id;

  try {
    scraped = await runScraper({ contactInfo: false });
  } catch (e) {
    return blocked(`Couldn't read the page: ${e.message}`);
  }
  if (!scraped || scraped.error) {
    return blocked(scraped ? scraped.error : "Nothing came back from the page.");
  }

  fill(scraped);
  hide("loading");
  show("form");

  // Company and title come from Experience, so an empty history is the
  // difference between a complete capture and a half-blank one. Say so rather
  // than leaving you to notice two empty boxes and guess why.
  if (!scraped.experience || !scraped.experience.length) {
    $("status").textContent =
      "No work history found on this page — scroll the Experience section into view and press Rescan.";
  } else {
    const roles = scraped.experience.length;
    $("status").textContent = `${roles} role${roles > 1 ? "s" : ""} of history captured.`;
  }

  const known = await lookup(scraped.vanity);
  if (known && known.known) {
    $("known").textContent = `Already in ADAM as “${known.display_name}” — saving updates them.`;
    show("known");
    $("save").textContent = "Update in ADAM";
  }
}

$("contact-info").addEventListener("click", async () => {
  $("contact-info").disabled = true;
  $("status").textContent = "Opening LinkedIn's contact info…";
  try {
    const fresh = await runScraper({ contactInfo: true });
    if (fresh && !fresh.error) {
      scraped = { ...scraped, ...fresh };
      const emails = listValue($("f-emails").value);
      const phones = listValue($("f-phones").value);
      $("f-emails").value = [...new Set([...emails, ...(fresh.emails || [])])].join(", ");
      $("f-phones").value = [...new Set([...phones, ...(fresh.phones || [])])].join(", ");
      $("status").textContent =
        (fresh.emails || []).length || (fresh.phones || []).length
          ? "Contact info added."
          : "LinkedIn shows no contact details for this profile.";
    } else {
      $("status").textContent = "Couldn't open the contact-info panel.";
    }
  } catch (e) {
    $("status").textContent = `Contact info failed: ${e.message}`;
  }
  $("contact-info").disabled = false;
});

$("rescan").addEventListener("click", async () => {
  $("status").textContent = "Rescanning…";
  const fresh = await runScraper({ contactInfo: false });
  if (fresh && !fresh.error) {
    scraped = fresh;
    fill(fresh);
    $("status").textContent = "Rescanned.";
  }
});

$("save").addEventListener("click", async () => {
  $("save").disabled = true;
  $("status").textContent = "Saving…";

  const withDetail = $("f-about").checked;
  const payload = {
    vanity: scraped.vanity,
    profile_url: scraped.profile_url,
    full_name: $("f-name").value.trim() || null,
    headline: $("f-headline").value.trim() || null,
    location: $("f-location").value.trim() || null,
    current_title: $("f-title").value.trim() || null,
    current_company: $("f-company").value.trim() || null,
    company_url: scraped.company_url || null,
    avatar_url: scraped.avatar_url || null,
    connection_degree: scraped.connection_degree || null,
    emails: listValue($("f-emails").value),
    phones: listValue($("f-phones").value),
    websites: scraped.websites || [],
    twitter: scraped.twitter || null,
    birthday: scraped.birthday || null,
    about: withDetail ? scraped.about || null : null,
    experience: withDetail ? scraped.experience || [] : [],
    education: withDetail ? scraped.education || [] : [],
    note: $("f-note").value.trim() || null,
  };

  const res = await chrome.runtime.sendMessage({ type: "save", profile: payload });
  $("save").disabled = false;
  if (!res.ok) {
    $("status").textContent = res.error;
    $("status").className = "status error";
    if (res.needsSetup) chrome.runtime.openOptionsPage();
    return;
  }

  const r = res.data;
  const { apiBase } = (await chrome.runtime.sendMessage({ type: "settings" })).data;
  hide("form");
  $("done-msg").textContent = r.person_created
    ? `Added ${r.display_name}.`
    : `Updated ${r.display_name}.`;

  const detail = [];
  if (r.identity_created) detail.push("LinkedIn profile linked");
  if (r.name_upgraded) detail.push("name filled in over a placeholder");
  if (r.emails_linked.length) detail.push(`${r.emails_linked.length} email(s) linked`);
  if (r.emails_conflicted.length)
    detail.push(`${r.emails_conflicted.join(", ")} belongs to another contact — left alone`);
  if (r.phones_recorded) detail.push(`${r.phones_recorded} phone(s) recorded`);
  if (r.note_added) detail.push("note added");
  $("done-detail").innerHTML = "";
  for (const line of detail) {
    const li = document.createElement("li");
    li.textContent = line;
    $("done-detail").appendChild(li);
  }

  $("done-link").href = `${apiBase}/persons/${r.person_id}`;
  show("done");
});

$("options-link").addEventListener("click", (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});

init();
