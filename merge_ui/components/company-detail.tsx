"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Globe, MapPin, Users, Target, UserPlus, X, Trash2, GitMerge } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CompanyLogo } from "@/components/company-logo";
import { PersonPicker } from "@/components/person-picker";
import { CompanyPicker } from "@/components/company-picker";
import { CountryCombobox } from "@/components/country-combobox";
import { StageBadge } from "@/components/stage-badge";
import { api, type CompanyDetail } from "@/lib/api";
import { countryFlag } from "@/lib/utils";
import { toast } from "sonner";

export function CompanyDetailView({ initial }: { initial: CompanyDetail }) {
  const router = useRouter();
  const [c, setC] = useState<CompanyDetail>(initial);
  const [name, setName] = useState(initial.name);
  const [country, setCountry] = useState(initial.country ?? "");
  const [website, setWebsite] = useState(initial.website ?? "");
  const [desc, setDesc] = useState(initial.description ?? "");

  async function reload() {
    const d = await api.getCompany(c.id);
    setC(d); setName(d.name); setCountry(d.country ?? ""); setWebsite(d.website ?? ""); setDesc(d.description ?? "");
  }
  async function patch(fields: Record<string, unknown>) {
    try { await api.patchCompany(c.id, fields); await reload(); router.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Save failed"); }
  }
  async function addPerson(personId: string) {
    try { await api.addCompanyPerson(c.id, personId); await reload(); router.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Add failed"); }
  }
  async function removePerson(personId: string) {
    try { await api.removeCompanyPerson(c.id, personId); await reload(); router.refresh(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Remove failed"); }
  }
  async function mergeInto(intoId: string, intoName: string) {
    if (!confirm(`Merge "${c.name}" into "${intoName}"? People and deals move over; "${c.name}" is removed.`)) return;
    try { await api.mergeCompany(c.id, intoId); toast.success("Merged"); router.push(`/companies/${intoId}`); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Merge failed"); }
  }
  async function del() {
    if (!confirm(`Delete "${c.name}"? (Soft delete — links are removed.)`)) return;
    try { await api.deleteCompany(c.id); toast.success("Deleted"); router.push("/companies"); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Delete failed"); }
  }

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-start gap-3">
        <CompanyLogo domain={c.domain} name={c.name} size={44} />
        <div className="flex-1 min-w-0">
          <input
            value={name} onChange={(e) => setName(e.target.value)}
            onBlur={() => { const t = name.trim(); if (t && t !== c.name) patch({ name: t }); }}
            className="w-full bg-transparent text-xl font-semibold outline-none focus:bg-accent/20 rounded px-1 -mx-1"
          />
          <div className="flex flex-wrap items-center gap-3 mt-1 text-sm text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              {countryFlag(c.country) ? <span>{countryFlag(c.country)}</span> : <MapPin className="h-3.5 w-3.5" />}
              <CountryCombobox
                value={country}
                onChange={setCountry}
                onCommit={(v) => { if (v !== (c.country ?? "")) patch({ country: v.trim() || null }); }}
                placeholder="country"
                className="bg-transparent outline-none w-32"
              />
            </span>
            <span className="inline-flex items-center gap-1 flex-1 min-w-0">
              <Globe className="h-3.5 w-3.5 shrink-0" />
              <input value={website} onChange={(e) => setWebsite(e.target.value)}
                onBlur={() => { if (website !== (c.website ?? "")) patch({ website: website.trim() || null }); }}
                placeholder="website" className="bg-transparent outline-none w-full" />
              {c.website && <a href={c.website} target="_blank" rel="noreferrer" className="text-sky-400 text-xs shrink-0">open ↗</a>}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <CompanyPicker excludeId={c.id} onPick={(t) => mergeInto(t.id, t.name)}
            trigger={<span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground border border-border rounded px-1.5 py-1"><GitMerge className="h-3 w-3" />merge</span>} align="right" />
          <button onClick={del} title="Delete" className="grid place-items-center h-7 w-7 rounded text-muted-foreground hover:text-destructive"><Trash2 className="h-3.5 w-3.5" /></button>
        </div>
      </div>

      {/* Description */}
      <div>
        <div className="text-xs text-muted-foreground mb-1">Description</div>
        <textarea value={desc} onChange={(e) => setDesc(e.target.value)}
          onBlur={() => { if (desc !== (c.description ?? "")) patch({ description: desc || null }); }}
          rows={3} placeholder="What this company does…"
          className="w-full text-sm rounded-md border border-border bg-background px-2 py-1.5 outline-none resize-y" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* People */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold flex items-center gap-1.5"><Users className="h-4 w-4 text-muted-foreground" />People <span className="font-normal text-muted-foreground">({c.people.length})</span></h2>
            <PersonPicker onPick={(p) => addPerson(p.person_id)}
              trigger={<span className="inline-flex items-center gap-0.5 text-[11px] text-muted-foreground hover:text-foreground border border-dashed border-border rounded px-1.5 py-0.5"><UserPlus className="h-3 w-3" />add</span>} align="right" />
          </div>
          {c.people.length === 0 ? (
            <p className="text-xs text-muted-foreground px-1 py-2">No people linked yet.</p>
          ) : (
            <ul className="rounded-md border border-border bg-card/40 divide-y divide-border overflow-hidden">
              {c.people.map((p) => (
                <li key={p.person_id} className="flex items-center gap-2 px-3 py-2 group">
                  <Link href={`/persons/${p.person_id}`} className="min-w-0 flex-1">
                    <span className="text-sm text-sky-400 hover:text-sky-300">{p.display_name}</span>
                    {p.role && <span className="text-xs text-muted-foreground"> · {p.role}</span>}
                    {!p.is_current && <span className="text-[10px] text-muted-foreground/60"> · past</span>}
                  </Link>
                  <button onClick={() => removePerson(p.person_id)} className="opacity-0 group-hover:opacity-100 grid place-items-center h-6 w-6 rounded text-muted-foreground hover:text-destructive"><X className="h-3.5 w-3.5" /></button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Opportunities */}
        <div>
          <h2 className="text-sm font-semibold flex items-center gap-1.5 mb-2"><Target className="h-4 w-4 text-muted-foreground" />Opportunities <span className="font-normal text-muted-foreground">({c.opportunities.length})</span></h2>
          {c.opportunities.length === 0 ? (
            <p className="text-xs text-muted-foreground px-1 py-2">No deals linked. Set a deal&apos;s company on its card.</p>
          ) : (
            <ul className="rounded-md border border-border bg-card/40 divide-y divide-border overflow-hidden">
              {c.opportunities.map((o) => (
                <li key={o.id}>
                  <Link href="/opportunities" className="flex items-center gap-2 px-3 py-2 hover:bg-accent/20">
                    <StageBadge stage={o.stage} />
                    <span className="flex-1 min-w-0 truncate text-sm">{o.title}</span>
                    {o.award_usd != null && <span className="text-xs text-foreground tabular shrink-0">${o.award_usd.toLocaleString()}</span>}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
