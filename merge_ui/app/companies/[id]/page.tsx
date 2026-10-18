import { cookies } from "next/headers";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronLeft } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { CompanyDetailView } from "@/components/company-detail";
import { ContactSharingControl } from "@/components/contact-sharing-control";
import { api, type CompanyDetail } from "@/lib/api";

async function fetchCompany(id: string): Promise<CompanyDetail | null> {
  const cookie = (await cookies()).toString();
  try {
    return await api.getCompany(id, { cookieHeader: cookie });
  } catch {
    return null;
  }
}

export default async function CompanyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const company = await fetchCompany(id);
  if (!company) notFound();

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-4xl">
        <Link href="/companies" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mb-4 transition-colors">
          <ChevronLeft className="h-3.5 w-3.5" /> All companies
        </Link>
        <div className="mb-4">
          <ContactSharingControl id={company.id} visibility={company.visibility} kind="company" />
        </div>
        <CompanyDetailView initial={company} />
      </div>
    </AppShell>
  );
}
