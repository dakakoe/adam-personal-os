import { NextRequest, NextResponse } from "next/server";

/**
 * Gate page routes. A human is "authenticated" via EITHER:
 *   - the Authelia identity Caddy injects after a successful password+2FA login
 *     (the `Remote-Groups` header — present only after forward_auth passes), OR
 *   - the legacy `merge_session` bearer cookie (machines / token login).
 * Either way the user skips the token-paste /login page. This is UX routing
 * only — the API re-validates every /api/* request (it trusts the Authelia
 * identity only with the matching X-Proxy-Secret, else a bearer/cookie token),
 * so a forged header gets you an empty shell and 401s on every call.
 */
const COOKIE_NAME = process.env.MERGE_API_COOKIE ?? "merge_session";

function authContext(req: NextRequest): { authed: boolean; role: string | undefined } {
  const cookie = req.cookies.get(COOKIE_NAME)?.value;
  const groups = req.headers.get("remote-groups"); // Authelia → Caddy → here
  const authed = Boolean(cookie || groups);
  // Role for nav routing: the readable cookie (legacy) or the Authelia groups.
  let role = req.cookies.get("merge_role")?.value;
  if (!role && groups) {
    const g = groups.toLowerCase();
    role = g.includes("admin") ? "admin" : g.includes("budget") ? "budget" : undefined;
  }
  return { authed, role };
}

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (
    pathname.startsWith("/api/") ||
    pathname.startsWith("/_next/") ||
    pathname === "/favicon.ico"
  ) {
    return NextResponse.next();
  }

  const { authed, role } = authContext(req);

  // The token-paste page. If already authenticated (e.g. via Authelia), don't
  // make the user paste a token — bounce them into the app instead.
  if (pathname.startsWith("/login")) {
    if (authed) {
      const raw = req.nextUrl.searchParams.get("next") || "/";
      const dest = raw.startsWith("/") && !raw.startsWith("//") ? raw : "/"; // no open redirect
      const url = req.nextUrl.clone();
      url.pathname = dest;
      url.search = "";
      return NextResponse.redirect(url);
    }
    return NextResponse.next();
  }

  if (!authed) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }

  // Budget-only role (the member): confine the UI to the Budget section. UX routing
  // only — the API enforces the real boundary (a budget identity 403s on every
  // non-/api/finance route regardless of what the browser navigates to).
  if (
    role === "budget" &&
    !pathname.startsWith("/budget") &&
    !pathname.startsWith("/projects") &&  // she may see the projects she's a member of
    !pathname.startsWith("/persons") &&   // …and contacts shared with her (read-only, scoped)
    !pathname.startsWith("/companies")    // …and shared companies (read-only, scoped list)
  ) {
    const url = req.nextUrl.clone();
    url.pathname = "/budget";
    url.search = "";
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
