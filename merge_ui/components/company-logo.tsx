"use client";

import { useState } from "react";

/** Favicon-based company logo (Google's favicon service) with an initials
 *  fallback when there's no domain or the fetch fails. */
export function CompanyLogo({ domain, name, size = 24 }: { domain: string | null; name: string; size?: number }) {
  const [err, setErr] = useState(false);
  const initials = name.trim().split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "").join("") || "?";
  if (domain && !err) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`}
        alt="" width={size} height={size} onError={() => setErr(true)}
        className="rounded shrink-0 bg-secondary/40"
        referrerPolicy="no-referrer"
      />
    );
  }
  return (
    <span className="rounded bg-secondary grid place-items-center text-[10px] font-semibold text-muted-foreground shrink-0"
          style={{ width: size, height: size }}>
      {initials}
    </span>
  );
}
