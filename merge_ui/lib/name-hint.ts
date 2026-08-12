/**
 * Turning a handle into a plausible human name.
 *
 * A LinkedIn vanity is often the only clue to someone's real name, but it's
 * usually run together: "richardrbudel". Title-casing alone gives the useless
 * "Richardrbudel". Separator-delimited handles are easy ("john.doe" →
 * "John Doe"); for run-together ones we match a known first name as a prefix
 * and treat the remainder as middle-initial + surname.
 *
 * This is a HINT that pre-fills an editable box — never applied automatically.
 * When nothing matches confidently we fall back to plain title-casing rather
 * than inventing a split.
 */

// Common given names, longest-first at match time. Deliberately compact: it
// only needs to cover the "firstnamelastname" vanity convention, and a miss
// costs nothing (we fall back to title-case).
const FIRST_NAMES = [
  "alexander", "alexandra", "christopher", "konstantin", "aleksandr", "vladimir",
  "elizabeth", "catherine", "sebastian", "nicholas", "stephanie", "alejandro",
  "francisco", "margarita", "anastasia", "ekaterina", "aleksandra", "cristina",
  "guillermo", "madeleine", "jonathan", "benjamin", "nathaniel", "gabriel",
  "patricia", "veronica", "michelle", "victoria", "samantha", "danielle",
  "kimberly", "jennifer", "isabella", "vincenzo", "svetlana", "yekaterina",
  "dmitriy", "mikhail", "nikolai", "stanislav", "vyacheslav", "yaroslav",
  "richard", "michael", "william", "charles", "matthew", "anthony", "andrew",
  "joshua", "daniel", "george", "kenneth", "steven", "edward", "brian",
  "ronald", "timothy", "jason", "jeffrey", "gary", "nicolas", "eric",
  "stephen", "jacob", "walter", "patrick", "peter", "harold", "douglas",
  "henry", "carl", "arthur", "roger", "keith", "jeremy", "lawrence",
  "sean", "albert", "joe", "willie", "gerald", "ralph", "roy", "eugene",
  "louis", "philip", "johnny", "robert", "james", "john", "david", "paul",
  "mark", "donald", "kevin", "brandon", "justin", "scott", "frank", "aaron",
  "adam", "nathan", "zachary", "kyle", "noah", "ethan", "logan", "lucas",
  "mason", "oliver", "elijah", "liam", "caleb", "ryan", "dylan", "tyler",
  "jordan", "cameron", "hunter", "connor", "austin", "evan", "ian", "cole",
  "mary", "linda", "barbara", "susan", "jessica", "sarah", "karen", "nancy",
  "lisa", "betty", "sandra", "ashley", "dorothy", "emily", "donna", "carol",
  "amanda", "melissa", "deborah", "rebecca", "laura", "sharon", "cynthia",
  "kathleen", "amy", "angela", "shirley", "brenda", "pamela", "nicole",
  "christine", "rachel", "carolyn", "janet", "maria", "heather", "diane",
  "julie", "olivia", "sophia", "emma", "ava", "mia", "abigail", "charlotte",
  "amelia", "harper", "evelyn", "anna", "elena", "irina", "olga", "natalia",
  "tatiana", "marina", "julia", "daria", "sergei", "sergey", "andrei", "ivan",
  "pavel", "roman", "denis", "artem", "maxim", "egor", "kirill", "anton",
  "ahmed", "mohamed", "muhammad", "ali", "omar", "hassan", "yusuf", "ibrahim",
  "wei", "ming", "jing", "hiroshi", "kenji", "yuki", "takeshi", "sakura",
  "raj", "amit", "sunil", "priya", "ananya", "arjun", "rahul", "vikram",
  "carlos", "juan", "jose", "luis", "miguel", "pedro", "diego", "javier",
  "sofia", "lucia", "carmen", "pierre", "jean", "luc", "marc", "antoine",
  "hans", "klaus", "stefan", "lukas", "felix", "jonas", "matthias", "tobias",
];

function cap(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

/**
 * Title-cases a handle, splitting on separators.
 * "raskalov" → "Raskalov", "john.doe" → "John Doe", "jane_smith" → "Jane Smith".
 */
export function titleCaseHandle(raw: string): string {
  return raw
    .split(/[._\-\s]+/)
    .filter(Boolean)
    .map(cap)
    .join(" ");
}

/**
 * Best-effort human name from a handle.
 *
 * Separators win when present. Otherwise, if the handle starts with a known
 * given name, split it off and read the remainder as [middle initial] + surname:
 *   "richardrbudel" → "Richard R Budel"
 *   "johnsmith"     → "John Smith"
 *   "raskalov"      → "Raskalov"        (no confident split — left alone)
 *
 * Digits are stripped first ("john_doe92" → "John Doe"); a handle that is
 * mostly digits yields null, since "User 553769" is not a name.
 */
export function humanizeHandle(raw: string): string | null {
  const cleaned = (raw || "").trim().replace(/\d+/g, "");
  if (!cleaned || cleaned.replace(/[^a-z]/gi, "").length < 3) return null;

  if (/[._\-\s]/.test(cleaned)) return titleCaseHandle(cleaned);

  const lower = cleaned.toLowerCase();
  // Longest match first so "alexandra" beats "alex".
  const first = [...FIRST_NAMES]
    .sort((a, b) => b.length - a.length)
    .find((n) => lower.startsWith(n) && lower.length > n.length + 1);

  if (first) {
    const rest = lower.slice(first.length);
    // Deliberately NO middle-initial guessing: "firstnamelastname" is by far
    // the commonest vanity shape, and treating the surname's first letter as an
    // initial mangles it ("johnsmith" → "John S Mith"). An embedded initial
    // ("richardrbudel" → "Richard Rbudel") is left for the user to trim — the
    // hint is editable, and being right on the common case matters more.
    if (rest.length >= 2) return `${cap(first)} ${cap(rest)}`;
  }
  return titleCaseHandle(cleaned);
}
