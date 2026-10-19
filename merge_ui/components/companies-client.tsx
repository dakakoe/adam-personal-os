"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Plus, Search, Users, Target } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { CompanyLogo } from "@/components/company-logo";
import { CountryCombobox } from "@/components/country-combobox";
import { api, type CompanyRow } from "@/lib/api";
import { countryFlag } from "@/lib/utils";
import { toast } from "sonner";

type SortKey = "people" | "pipeline" | "name";

function fmtUsd(n: number): string {
  if (!n) return "";
  if (n >= 1_000_000) return `$${(n / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 2 })}M`;
  if (n >= 1_000) return `$${(n / 1_000).toLocaleString(undefined, { maximumFractionDigits: 1 })}k`;
  return `$${n.toLocaleString()}`;
}

export function CompaniesClient({ initial, isAdmin = true }: { initial: CompanyRow[]; isAdmin?: boolean }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortKey>("people");
  const [country, setCountry] = useState("");
  const [createOpen, setCreateOpen] = useState(false);

  // Countries present in the data (for the filter dropdown).
  const countries = Array.from(new Set(initial.map((c) => c.country).filter((x): x is string => !!x))).sort();

  let filtered = initial;
  if (q.trim()) filtered = filtered.filter((c) => c.name.toLowerCase().includes(q.trim().toLowerCase()));
  if (country) filtered = filtered.filter((c) => c.country === country);
  filtered = [...filtered].sort((a, b) =>
    sort === "name" ? a.name.localeCompare(b.name)
      : sort === "pipeline" ? (b.pipeline_usd - a.pipeline_usd) || (b.people_count - a.people_count)
      : (b.people_count - a.people_count) || a.name.localeCompare(b.name),
  );

  const selectCls = "h-9 text-xs px-2 rounded-md border border-border bg-background text-muted-foreground";

  return (
    <>
      <div className="mb-3 flex items-center gap-2 flex-wrap">
        <div className="flex items-center gap-1.5 px-2 h-9 rounded-md border border-border bg-background flex-1 min-w-[12rem] max-w-sm">
          <Search className="h-4 w-4 text-muted-foreground" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search companies…" className="bg-transparent outline-none text-sm w-full" />
        </div>
        <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} className={selectCls} title="Sort">
          <option value="people">Sort: most people</option>
          <option value="pipeline">Sort: pipeline $</option>
          <option value="name">Sort: name</option>
        </select>
        {countries.length > 0 && (
          <select value={country} onChange={(e) => setCountry(e.target.value)} className={selectCls} title="Country">
            <option value="">All countries</option>
            {countries.map((c) => <option key={c} value={c}>{countryFlag(c)} {c}</option>)}
          </select>
        )}
        {isAdmin && <Button size="sm" onClick={() => setCreateOpen(true)}><Plus className="h-3.5 w-3.5 mr-1" />New</Button>}
      </div>

      {filtered.length === 0 ? (
        <div className="rounded-md border border-border bg-card/40 p-10 text-center text-sm text-muted-foreground">No companies match.</div>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border overflow-hidden bg-card/40">
          {filtered.map((c) => {
            const inner = (
              <>
                <CompanyLogo domain={c.domain} name={c.name} size={28} />
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{c.name}</div>
                  <div className="text-xs text-muted-foreground flex flex-wrap items-center gap-x-2.5">
                    {c.country && <span>{countryFlag(c.country)} {c.country}</span>}
                    {c.domain && <span>{c.domain}</span>}
                  </div>
                </div>
                <div className="flex items-center gap-3 text-xs text-muted-foreground shrink-0 tabular">
                  <span className="inline-flex items-center gap-1"><Users className="h-3.5 w-3.5" />{c.people_count}</span>
                  {c.live_opp_count > 0 && <span className="inline-flex items-center gap-1"><Target className="h-3.5 w-3.5" />{c.live_opp_count}</span>}
                  {c.pipeline_usd > 0 && <span className="text-foreground">{fmtUsd(c.pipeline_usd)}</span>}
                </div>
              </>
            );
            return (
              <li key={c.id}>
                {isAdmin ? (
                  <Link href={`/companies/${c.id}`} className="flex items-center gap-3 px-3 sm:px-4 py-2.5 hover:bg-accent/20 transition-colors">
                    {inner}
                  </Link>
                ) : (
                  <div className="flex items-center gap-3 px-3 sm:px-4 py-2.5">{inner}</div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <CreateCompanyDialog open={createOpen} onOpenChange={setCreateOpen} onCreated={(id) => router.push(`/companies/${id}`)} />
    </>
  );
}

function CreateCompanyDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (id: string) => void }) {
  const [name, setName] = useState("");
  const [country, setCountry] = useState("");
  const [website, setWebsite] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const c = await api.createCompany({ name: name.trim(), country: country.trim() || undefined, website: website.trim() || undefined });
      toast.success("Company created");
      onOpenChange(false); setName(""); setCountry(""); setWebsite("");
      onCreated(c.id);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Create failed");
    } finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>New company</DialogTitle></DialogHeader>
        <form onSubmit={submit} className="space-y-3">
          <div><Label htmlFor="c_name">Name</Label><Input id="c_name" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Acme Corp" /></div>
          <div>
            <Label htmlFor="c_country">Country</Label>
            <CountryCombobox
              id="c_country"
              value={country}
              onChange={setCountry}
              placeholder="Cayman Islands"
              wrapperClassName="relative block"
              className="h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 dark:bg-input/30"
            />
          </div>
          <div><Label htmlFor="c_web">Website</Label><Input id="c_web" value={website} onChange={(e) => setWebsite(e.target.value)} placeholder="https://acme.com" /></div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
            <Button type="submit" disabled={busy || !name.trim()}>{busy ? "Creating…" : "Create"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
