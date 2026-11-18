"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { api } from "@/lib/api";
import { KeyRound } from "lucide-react";

function LoginForm() {
  const router = useRouter();
  const search = useSearchParams();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const { role } = await api.login(token.trim());
      // budget-only users always land on the Budget section
      router.replace(role === "budget" ? "/budget" : (search.get("next") ?? "/today"));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Login failed");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="token">Bearer token</Label>
        <Input
          id="token"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          autoFocus
          autoComplete="off"
          className="font-mono"
          placeholder="paste token"
        />
      </div>
      {err && (
        <Alert variant="destructive">
          <AlertDescription>{err}</AlertDescription>
        </Alert>
      )}
      <Button type="submit" disabled={busy || token.length === 0} className="w-full">
        {busy ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className="min-h-dvh grid place-items-center p-6">
      <Card className="w-full max-w-md border-border bg-card">
        <CardHeader className="space-y-1">
          <div className="flex items-center gap-2 text-primary">
            <KeyRound className="h-5 w-5" />
            <CardTitle className="text-xl font-semibold">ADAM</CardTitle>
          </div>
          <CardDescription>
            Paste your bearer token. Stored as an httpOnly cookie for 30 days.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Suspense fallback={<div className="text-sm text-muted-foreground">Loading…</div>}>
            <LoginForm />
          </Suspense>
        </CardContent>
      </Card>
    </div>
  );
}
