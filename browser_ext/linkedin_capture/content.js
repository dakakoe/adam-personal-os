// Scrapes the LinkedIn profile page in the active tab.
//
// Injected on demand by the popup (chrome.scripting.executeScript) — there is
// no persistent content script, so nothing of ours runs on LinkedIn until you
// click the extension.
//
// Two rules make this survive LinkedIn's markup churn:
//   1. Prefer *semantics* over class names. hrefs (mailto:, tel:), section
//      anchors (#experience) and aria labels outlive `.pv-text-details__…`,
//      which LinkedIn renames on a whim.
//   2. Every field is independently optional. A selector that stops matching
//      costs you one field in the popup form, where you can type it in — it
//      never fails the capture.
//
// LinkedIn renders each visible string twice: once in a
// <span aria-hidden="true"> and once in a .visually-hidden sibling for screen
// readers. Reading textContent naively gives you everything doubled, hence
// textOf() below.

(() => {
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

  function textOf(el) {
    if (!el) return "";
    const clone = el.cloneNode(true);
    clone.querySelectorAll(".visually-hidden").forEach((n) => n.remove());
    return clean(clone.textContent);
  }


  // Only elements the browser is actually painting. LinkedIn ships hidden
  // template copies of several strings — every profile carries BOTH "· 1st"
  // and "· 2nd" in the DOM, so reading the first match reports the wrong
  // connection degree on half of all profiles. getClientRects() settles it.
  const visible = (el) => el.getClientRects().length > 0;

  const headings = () => [
    ...document.querySelectorAll("h1, h2, h3, [role=heading]"),
  ];

  // Sections are found by their VISIBLE HEADING TEXT ("About", "Experience"),
  // not by id. LinkedIn dropped the #about / #experience anchor divs this
  // version was originally written against; heading text is what it can't
  // rename without changing what you see on screen.
  // "ExperienceExperience" → "experience". Headings carry a screen-reader
  // duplicate whose wrapper class isn't always .visually-hidden, so textOf()
  // can't always strip it and an equality check against "experience" misses.
  const normalizeHeading = (s) => {
    const t = clean(s).toLowerCase();
    const half = t.length / 2;
    return t.length % 2 === 0 && t.slice(0, half) === t.slice(half) ? t.slice(0, half) : t;
  };

  // Climb from the heading to the nearest ancestor that holds actual content
  // rather than the heading alone. Counting <li> here was the bug that made
  // this return nothing: the Experience section contains none — its entries
  // are nested div/a. Counting rendered lines works whatever the markup is.
  const containerWithItems = (el) => {
    for (let i = 0; el && i < 6; i++) {
      let lines = 0;
      for (const child of el.querySelectorAll("*")) {
        if (!child.children.length && visible(child) && textOf(child)) lines++;
        if (lines > 3) return el;
      }
      el = el.parentElement;
    }
    return null;
  };

  const sectionByHeading = (label) => {
    for (const h of headings()) {
      if (normalizeHeading(textOf(h)) !== label) continue;
      const scoped = h.closest("section") || h.parentElement;
      return containerWithItems(scoped) || scoped;
    }
    // Legacy anchor divs (#about, #experience). Gone from the current markup,
    // kept because it costs nothing and LinkedIn reshuffles constantly.
    const anchor = document.getElementById(label);
    if (anchor) {
      const scoped = anchor.closest("section") || anchor.parentElement;
      return containerWithItems(scoped) || scoped;
    }
    return null;
  };

  // The name is the one string on the page guaranteed to be somewhere stable:
  // document.title is "Name | LinkedIn" (with an optional "(3) " unread
  // prefix). There is no <h1> on a profile any more — the name renders as an
  // <h2> — so the title is the anchor and the heading only locates the card.
  function nameFromTitle() {
    const raw = (document.title || "").replace(/^\(\d+\)\s*/, "");
    const parts = raw.split("|");
    if (parts.length > 1) parts.pop(); // drop the trailing "LinkedIn"
    return clean(parts.join("|")) || null;
  }

  const topCardSection = (name) => {
    if (!name) return null;
    const h = headings().find((x) => textOf(x).startsWith(name.slice(0, 8)));
    return h ? h.closest("section") || h.parentElement : null;
  };

  // Leaf text nodes of the card, in render order, deduped. Verified order:
  //   name → "· 2nd" → headline → location → "·" → "Contact info"
  //   → company → school → "500+" → "connections" → …
  function visibleLeaves(root) {
    const seen = new Set();
    const out = [];
    if (!root) return out;
    for (const el of root.querySelectorAll("*")) {
      if (el.children.length || !visible(el)) continue;
      const t = textOf(el);
      if (t && !seen.has(t)) {
        seen.add(t);
        out.push(t);
      }
    }
    return out;
  }

  function vanityFromUrl(url) {
    const m = /linkedin\.com\/in\/([^/?#]+)/i.exec(url || "");
    return m ? decodeURIComponent(m[1]).toLowerCase() : null;
  }

  // --- JSON-LD ------------------------------------------------------------
  // Public profiles ship a ProfilePage blob that is far more stable than the
  // DOM. It's absent on many logged-in renders, so it seeds the result and the
  // DOM fills the gaps.
  function fromJsonLd() {
    const out = {};
    for (const tag of document.querySelectorAll('script[type="application/ld+json"]')) {
      let data;
      try {
        data = JSON.parse(tag.textContent);
      } catch {
        continue;
      }
      const graph = data["@graph"] || (Array.isArray(data) ? data : [data]);
      for (const node of graph) {
        if (!node || node["@type"] !== "Person") continue;
        if (node.name) out.full_name = clean(node.name);
        if (node.givenName) out.first_name = clean(node.givenName);
        if (node.familyName) out.last_name = clean(node.familyName);
        if (node.jobTitle) {
          out.current_title = clean(
            Array.isArray(node.jobTitle) ? node.jobTitle[0] : node.jobTitle
          );
        }
        if (node.image && node.image.contentUrl) out.avatar_url = node.image.contentUrl;
        const addr = node.address || {};
        const where = [addr.addressLocality, addr.addressRegion, addr.addressCountry]
          .filter(Boolean)
          .join(", ");
        if (where) out.location = clean(where);
        const works = Array.isArray(node.worksFor) ? node.worksFor[0] : node.worksFor;
        if (works && works.name) out.current_company = clean(works.name);
      }
    }
    return out;
  }

  // --- top card -----------------------------------------------------------

  // Read positionally off the card's visible leaves rather than by class name.
  // Verified against live profiles: the class names here churn constantly, but
  // this ordering is what the page actually reads like top to bottom, and
  // "Contact info" is a fixed landmark with location before it and the current
  // company after it.
  const DEGREE_RE = /^·?\s*(1st|2nd|3rd\+?)$/;
  const NOISE_RE =
    /^(followers?|connections?|Message|Follow|Connect|More|Contact info|Show all|Pending|\d[\d,+]*\s*(followers|connections)?)$/i;

  function topCard() {
    const out = {};
    const name = nameFromTitle();
    if (name) out.full_name = name;

    const card = topCardSection(name);
    if (!card) return out;

    const leaves = visibleLeaves(card);
    const iName = leaves.findIndex((t) => name && t.startsWith(name.slice(0, 8)));
    const iContact = leaves.indexOf("Contact info");

    const degree = leaves.find((t) => DEGREE_RE.test(t));
    if (degree) out.connection_degree = degree.replace(/[·\s]/g, "");

    // Headline: first substantive line after the name that isn't a degree
    // badge, a separator, or a call-to-action button.
    out.headline =
      leaves
        .slice(iName + 1)
        .find(
          (t) => t.length > 3 && t !== "·" && !DEGREE_RE.test(t) && !NOISE_RE.test(t)
        ) || null;

    // Location sits immediately before "Contact info" (separated by a bare
    // middot). Profiles without a location simply have the headline there, so
    // stop rather than mistake one for the other.
    if (iContact > 0) {
      for (let i = iContact - 1; i > iName; i--) {
        const t = leaves[i];
        if (t === "·" || DEGREE_RE.test(t)) continue;
        if (t === out.headline) break;
        out.location = t;
        break;
      }
    }

    // NOTE: there is deliberately no company guess here any more.
    //
    // The company, when the top card shows one at all, sits after
    // "Contact info" — but so do the names of mutual connections, and nothing
    // in the text distinguishes an employer from a person. Two attempts at
    // bounding that scan both shipped a human being as the employer ("Abasa",
    // then "Thierry"), because the stop conditions differ per profile: some
    // show "500+ connections" before the mutuals, some go straight to them.
    // There are no /company/ anchors in the card to key on either.
    //
    // Experience states the employer explicitly, so that's where it comes
    // from. A blank company beats a confidently wrong one.

    const companyLink = card.querySelector('a[href*="/company/"]');
    if (companyLink) out.company_url = companyLink.href.split("?")[0];

    // The first <img> in the card is the cover photo, not the person.
    const avatar = [...card.querySelectorAll("img")].find(
      (i) => (i.alt || "") !== "Cover photo" && i.width >= 100 && !i.src.startsWith("data:")
    );
    if (avatar) out.avatar_url = avatar.src;

    return out;
  }

  // --- list sections (experience / education) -----------------------------
  //
  // Read from the section's VISIBLE TEXT IN RENDER ORDER, not from the DOM
  // shape. There are no <li> elements here — entries are nested div/a with
  // hashed class names (_51373152, b71d7456) that change on every deploy.
  // The text order does not:
  //
  //   Co-Founder / Healthy Tec / Apr 2024 - Present · 2 yrs 5 mos / Singapore
  //   Director   / GW Investment Company / 2006 – 2015
  //
  // Each entry carries exactly one date line, and the two lines before it are
  // the title and the employer. That anchors everything.

  const DATE_RE =
    /(19|20)\d{2}\s*[-–]\s*(Present|(19|20)\d{2}|[A-Z][a-z]{2}\.?\s+(19|20)\d{2})|^\w{3}\.?\s+(19|20)\d{2}\s*[-–]/i;
  const EMPLOYMENT_RE =
    /(Full-time|Part-time|Self-employed|Freelance|Contract|Internship|Apprenticeship|Seasonal)/i;
  // Chrome for the section itself, never part of an entry.
  const CHROME_RE = /^(Show all.*|…|more|see more|Experience|Education)$/i;

  // Visible leaf lines of a section, in order. Deduped only against the line
  // BEFORE it — a section-wide dedupe drops a job title held twice ("Co-Founder"
  // at two companies), which silently left an entry with no title at all.
  function sectionLines(section) {
    const out = [];
    if (!section) return out;
    for (const el of section.querySelectorAll("*")) {
      if (el.children.length || !visible(el)) continue;
      const t = textOf(el);
      if (!t || CHROME_RE.test(t) || t === out[out.length - 1]) continue;
      out.push(t);
    }
    return out;
  }

  // Split the line list on its date lines. For the entry whose date sits at
  // index d, the two preceding lines are title and employer, and everything
  // from d+1 up to two lines before the NEXT date belongs to it (location,
  // then description) — those last two being the next entry's own header.
  // A duration with no date range — "4 yrs 8 mos", "3 yrs 11 mos". This is
  // the marker of a GROUPED employer: one company with several roles nested
  // under it, where the company is a header line above the roles instead of
  // sitting beside each title.
  const DURATION_ONLY_RE = /^\d+\s*(?:yrs?|years?)(?:\s+\d+\s*(?:mos?|months?))?$|^\d+\s*(?:mos?|months?)$/i;
  // "Gnosis · Full-time" — a company line in a SIMPLE entry carries the
  // employment type, which is how we know the group has ended.
  const COMPANY_LINE_RE = new RegExp("·\\s*(" + EMPLOYMENT_RE.source + ")", "i");

  function parseHistory(lines, limit) {
    // Grouped employers read differently from simple ones:
    //
    //   simple            grouped
    //   ------            -------
    //   Marketing Lead    Sleepagotchi        <- company
    //   Gnosis · Full-time  4 yrs 8 mos       <- duration, no date range
    //   Mar 2026 - Present  Founder           <- role
    //                       Mar 2026 - Present
    //
    // Reading two lines back from the date works for the first and is off by
    // one for the second — it returned title "4 yrs 8 mos", company "Founder".
    // So walk the lines and track which employer is currently in scope.
    const out = [];
    let groupCompany = null;

    for (let i = 0; i < lines.length && out.length < limit; i++) {
      const line = lines[i];

      if (DURATION_ONLY_RE.test(line)) {
        // The line above a bare duration is the employer these roles belong to.
        groupCompany = i > 0 ? lines[i - 1] : null;
        continue;
      }
      if (!DATE_RE.test(line)) continue;

      // A company line with an employment type means this entry carries its
      // own employer — the group (if any) has ended.
      const prevLine = lines[i - 1] || "";
      if (COMPANY_LINE_RE.test(prevLine)) groupCompany = null;

      let title = null;
      let company = null;
      if (groupCompany) {
        // Inside a group the only header line is the role itself; skip over
        // modifiers like "On-site" or "Full-time" that sit between the group
        // header and the first role.
        for (let j = i - 1; j >= 0 && j > i - 4; j--) {
          const c = lines[j];
          if (!c || DURATION_ONLY_RE.test(c) || EMPLOYMENT_RE.test(c)) continue;
          if (/^(on-site|remote|hybrid)$/i.test(c)) continue;
          if (c === groupCompany) break;
          title = c;
          break;
        }
        company = groupCompany;
      } else {
        title = lines[i - 2] || lines[i - 1] || null;
        company = lines[i - 2] ? lines[i - 1] : null;
      }
      if (!title) continue;

      // Everything up to two lines before the next date belongs to this entry
      // — those last two being the next entry's own header.
      let nextDate = lines.length;
      for (let j = i + 1; j < lines.length; j++) {
        if (DATE_RE.test(lines[j])) { nextDate = j; break; }
      }
      const tail = lines.slice(i + 1, Math.max(i + 1, groupCompany ? nextDate - 1 : nextDate - 2));
      const dateParts = line.split(" · ");
      const employment = EMPLOYMENT_RE.exec(company || prevLine || "");

      out.push({
        title,
        // "Gnosis · Full-time" → "Gnosis"; a bare "Healthy Tec" is left alone.
        company: company ? company.split(" · ")[0] : null,
        employment_type: employment ? employment[1] : null,
        date_range: dateParts[0] || null,
        duration: dateParts[1] || null,
        location: tail[0] && tail[0].length <= 60 && !/[.!?]/.test(tail[0]) ? tail[0] : null,
        description:
          tail.filter((t) => t.length > 60).join(" ").replace(/…\s*more$/i, "").trim() || null,
      });
    }
    return out;
  }

  function listEntries(label, limit) {
    return parseHistory(sectionLines(sectionByHeading(label)), limit);
  }

  function aboutText() {
    const section = sectionByHeading("about");
    if (!section) return null;
    const body = section.querySelector(
      ".display-flex.full-width, .inline-show-more-text, .pv-shared-text-with-see-more"
    );
    const text = textOf(body || section);
    // The section's own heading leads the text when we fall back to the whole
    // section; drop it so the note doesn't start with the word "About".
    return clean(text.replace(/^About\s*/i, "")) || null;
  }

  // --- contact info ---------------------------------------------------
  //
  // Verified against a live profile, which is nothing like what this
  // originally assumed:
  //   * clicking "Contact info" NAVIGATES to /in/<vanity>/overlay/contact-info/
  //     — it does not open a modal in place, so the page has to be put back
  //     afterwards with history.back() rather than a Dismiss click;
  //   * there is no .artdeco-modal. The content sits in a native <dialog>;
  //   * LinkedIn parks an EMPTY <dialog> on the plain profile page, so
  //     matching the element type reports "already open" and returns nothing.
  //     The container is identified by a VISIBLE "Contact info" heading;
  //   * labels and values are plain-text leaf pairs (Email → address,
  //     Birthday → "June 9"), not the section/h3 structure assumed before.
  //     Email and phone are also mailto:/tel: anchors when present, which is
  //     the more reliable read, so both paths run.

  const CONTACT_FIELD = {
    email: "emails", "email address": "emails",
    phone: "phones", "phone number": "phones",
    website: "websites", websites: "websites",
    birthday: "birthday", twitter: "twitter",
  };

  function contactContainer() {
    const h = headings().find(
      (x) => visible(x) && textOf(x).toLowerCase() === "contact info");
    if (!h) return null;
    const dialog = h.closest("dialog");
    if (dialog) return dialog;
    let el = h.parentElement;
    for (let i = 0; i < 5 && el; i++) {
      if (visibleLeaves(el).length > 3) return el;
      el = el.parentElement;
    }
    return null;
  }

  function parseContactInfo(root) {
    const out = { emails: [], phones: [], websites: [], twitter: null, birthday: null };
    const push = (arr, v) => { if (v && !arr.includes(v)) arr.push(v); };

    for (const a of root.querySelectorAll("a[href]")) {
      const href = a.href || "";
      if (href.startsWith("mailto:")) {
        push(out.emails, decodeURIComponent(href.slice(7)).trim().toLowerCase());
      } else if (href.startsWith("tel:")) {
        push(out.phones, decodeURIComponent(href.slice(4)).trim());
      } else if (/(?:twitter|x)\.com\/[^/]+$/i.test(href)) {
        out.twitter = out.twitter || href.split("/").filter(Boolean).pop();
      } else if (/^https?:/i.test(href) && !/linkedin\.com/i.test(href)) {
        push(out.websites, href);
      }
    }

    // Label → next line. Catches what isn't a link: phone numbers on some
    // profiles, and birthdays always.
    const lines = visibleLeaves(root);
    for (let i = 0; i < lines.length - 1; i++) {
      const key = lines[i].toLowerCase().replace(/[:\s]+$/, "");
      const field = CONTACT_FIELD[key];
      if (!field) continue;
      const value = lines[i + 1];
      // Guard against an empty field: the next line being another label.
      if (!value || CONTACT_FIELD[value.toLowerCase().replace(/[:\s]+$/, "")]) continue;
      if (Array.isArray(out[field])) push(out[field], value);
      else out[field] = out[field] || value;
    }
    return out;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Only runs when you ask for it (the popup's own button): it navigates the
  // tab and navigates back, which is too much to do behind your back.
  async function withContactInfo() {
    const before = location.href;
    let box = contactContainer();
    let clicked = false;

    if (!box) {
      const link = document.querySelector(
        'a#top-card-text-details-contact-info, a[href*="/overlay/contact-info/"]');
      // The id selector is dead on the current markup — the href match is what
      // actually finds it. Kept in case LinkedIn puts the id back.
      if (!link) return null;
      link.click();
      clicked = true;
      for (let i = 0; i < 40 && !box; i++) {
        await sleep(100);
        box = contactContainer();
      }
      if (!box) return null;
      await sleep(200);   // let the values paint
    }

    const parsed = parseContactInfo(box);

    if (clicked) {
      if (location.href !== before) {
        history.back();
        await sleep(1000);
      } else {
        const dismiss = box.querySelector(
          'button[aria-label="Dismiss"], button.artdeco-modal__dismiss');
        if (dismiss) dismiss.click();
        await sleep(300);
      }
    }
    return parsed;
  }

  // --- entry point --------------------------------------------------------

  async function scrape(opts) {
    const vanity = vanityFromUrl(location.href);
    if (!vanity) return { error: "not a LinkedIn profile page" };

    const profile = {
      vanity,
      profile_url: `https://www.linkedin.com/in/${vanity}`,
      emails: [],
      phones: [],
      websites: [],
    };
    Object.assign(profile, fromJsonLd());
    for (const [k, v] of Object.entries(topCard())) {
      if (v) profile[k] = v;
    }

    profile.about = aboutText();
    profile.experience = listEntries("experience", 10);
    profile.education = listEntries("education", 5);

    // Experience is the authority on the current role: the top card shows a
    // company only sometimes and never the title, whereas the entry marked
    // "Present" has both. Its company OVERRIDES the top card's — the top card's
    // is a positional guess, this one is labelled.
    const current =
      profile.experience.find((e) => /present/i.test(e.date_range || "")) ||
      profile.experience[0];
    if (current) {
      if (!profile.current_title) profile.current_title = current.title;
      if (current.company) profile.current_company = current.company;
    }
    // Headline as a last resort: "VP Engineering at Acme".
    if (!profile.current_title && profile.headline) {
      const m = /^(.+?)\s+(?:at|@)\s+(.+)$/i.exec(profile.headline);
      if (m) {
        profile.current_title = clean(m[1]);
        if (!profile.current_company) profile.current_company = clean(m[2]);
      }
    }

    if (opts && opts.contactInfo) {
      try {
        const contact = await withContactInfo();
        if (contact) {
          profile.emails = contact.emails;
          profile.phones = contact.phones;
          profile.websites = contact.websites;
          profile.twitter = contact.twitter;
          profile.birthday = contact.birthday;
        }
      } catch (e) {
        profile.contact_info_error = String(e && e.message ? e.message : e);
      }
    }

    return profile;
  }

  // This file only *installs* the scraper; the popup then calls it with its
  // options through a second executeScript. Injecting twice beats stashing
  // options on window first, and re-injection is harmless (same assignment).
  globalThis.__adamScrapeLinkedIn = scrape;
  return true;
})();
