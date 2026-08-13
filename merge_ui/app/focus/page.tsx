import { redirect } from "next/navigation";

// Focus was renamed to Plan (by-project board). Keep the old route working.
export default function FocusPage() {
  redirect("/plan");
}
