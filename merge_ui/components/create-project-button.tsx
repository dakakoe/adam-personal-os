"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { toast } from "sonner";

export function CreateProjectButton() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [color, setColor] = useState("#7C3AED");
  const [busy, setBusy] = useState(false);

  function autoSlug(n: string) {
    return n.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 48);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !slug.trim()) return;
    setBusy(true);
    try {
      const p = await api.createProject({
        slug: slug.trim(),
        name: name.trim(),
        description: description.trim() || undefined,
        color: color || undefined,
      });
      toast.success(`Created ${p.name}`);
      setOpen(false);
      setName(""); setSlug(""); setDescription("");
      router.refresh();
      router.push(`/projects/${p.slug}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Create failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        <Plus className="h-3.5 w-3.5 mr-1" />
        New project
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New project</DialogTitle>
            <DialogDescription>
              A bucket for related tasks + opportunities + members.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={submit} className="space-y-3">
            <div>
              <Label htmlFor="name">Name</Label>
              <Input
                id="name" value={name} autoFocus
                onChange={(e) => {
                  setName(e.target.value);
                  if (!slug || slug === autoSlug(name)) setSlug(autoSlug(e.target.value));
                }}
                placeholder="My Project"
              />
            </div>
            <div>
              <Label htmlFor="slug">Slug (URL fragment)</Label>
              <Input
                id="slug" value={slug}
                onChange={(e) => setSlug(autoSlug(e.target.value))}
                placeholder="my-project"
                className="font-mono text-sm"
              />
            </div>
            <div>
              <Label htmlFor="description">Description (optional)</Label>
              <Input
                id="description" value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="One-line summary"
              />
            </div>
            <div>
              <Label htmlFor="color">Color</Label>
              <input
                id="color" type="color" value={color}
                onChange={(e) => setColor(e.target.value)}
                className="block h-8 w-16 rounded border border-border bg-background cursor-pointer"
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setOpen(false)} disabled={busy}>Cancel</Button>
              <Button type="submit" disabled={busy || !name.trim() || !slug.trim()}>
                {busy ? "Creating…" : "Create"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
