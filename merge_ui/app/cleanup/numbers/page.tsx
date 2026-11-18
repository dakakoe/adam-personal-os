import { cookies } from "next/headers";
import Link from "next/link";
import { ChevronLeft, PhoneOff } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { PhoneNumberFixer } from "@/components/phone-number-fixer";
import { api, type UnplacedNumber } from "@/lib/api";

export default async function UnplacedNumbersPage() {
  let numbers: UnplacedNumber[] = [];
  try {
    numbers = (await api.listUnplacedNumbers({ cookieHeader: (await cookies()).toString() })).numbers;
  } catch { /* the fixer shows its empty state */ }

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-5xl">
        <Link href="/cleanup" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mb-3">
          <ChevronLeft className="h-3.5 w-3.5" /> Cleanup
        </Link>
        <header className="mb-5">
          <h1 className="text-xl sm:text-2xl font-semibold tracking-tight flex items-center gap-2">
            <PhoneOff className="h-5 w-5 text-muted-foreground" />
            Phone numbers without a country code
          </h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
            These numbers from your phone book can&apos;t be matched to anyone, because the country is
            unknown. Fix a number and its contact joins the person who already has it on WhatsApp or
            Telegram; delete it and it&apos;s never listed or matched again. Your phone keeps every number either way.
          </p>
        </header>
        <PhoneNumberFixer initial={numbers} />
      </div>
    </AppShell>
  );
}
