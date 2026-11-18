import { cookies } from "next/headers";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronLeft } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { PersonCard } from "@/components/person-card";
import { PersonDrafts } from "@/components/person-drafts";
import { PendingMerges } from "@/components/pending-merges";
import { SimilarPersons } from "@/components/similar-persons";
import { MergeWithSearch } from "@/components/merge-with-search";
import { NameSuggestionBanner } from "@/components/name-suggestion-banner";
import { ContactSharingControl } from "@/components/contact-sharing-control";
import { ContactSensitivityControl } from "@/components/contact-sensitivity-control";
import { api, type PersonDetail } from "@/lib/api";

async function fetchPerson(id: string): Promise<PersonDetail | null> {
  const cookie = (await cookies()).toString();
  try {
    return await api.getPerson(id, { cookieHeader: cookie });
  } catch {
    return null;
  }
}

export default async function PersonDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const person = await fetchPerson(id);
  if (!person) notFound();

  return (
    <AppShell>
      <div className="p-4 sm:p-6 mx-auto w-full max-w-4xl">
        <Link
          href="/persons"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground mb-4 transition-colors"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
          All people
        </Link>

        <div className="space-y-4">
          <PendingMerges personId={person.person_id} />
          <SimilarPersons
            personId={person.person_id}
            thisDisplayName={person.display_name}
          />
          <MergeWithSearch
            personId={person.person_id}
            thisDisplayName={person.display_name}
          />
          <NameSuggestionBanner
            personId={person.person_id}
            initialDisplayName={person.display_name}
            identities={person.identities}
            telegramUsername={person.telegram_username}
          />
          <ContactSharingControl id={person.person_id} visibility={person.visibility} kind="person" />
          <ContactSensitivityControl id={person.person_id} sensitive={person.sensitive} />
          <PersonCard person={person} />
          <PersonDrafts personId={person.person_id} />
        </div>
      </div>
    </AppShell>
  );
}
