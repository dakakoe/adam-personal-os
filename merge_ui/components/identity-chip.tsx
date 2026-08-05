"use client";

import {
  Linkedin, Instagram, Twitter, Send, Phone, Mail, Github, Globe, X as XIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useState } from "react";
import { RemoveIdentityConfirm } from "./remove-identity-confirm";

const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  linkedin: Linkedin,
  instagram: Instagram,
  x: Twitter,            // visually still bird-ish in lucide; OK shorthand
  twitter: Twitter,
  telegram: Send,
  telegram_handle: Send,
  phone: Phone,
  email: Mail,
  github: Github,
  website: Globe,
};

const CHANNEL_COLOR: Record<string, string> = {
  telegram: "text-[var(--color-channel-telegram)]",
  telegram_handle: "text-[var(--color-channel-telegram)]",
  email: "text-[var(--color-channel-email)]",
  linkedin: "text-[var(--color-channel-linkedin)]",
  phone: "text-[var(--color-channel-phone)]",
  x: "text-[var(--color-channel-x)]",
  twitter: "text-[var(--color-channel-x)]",
  instagram: "text-[var(--color-channel-instagram)]",
  github: "text-[var(--color-channel-github)]",
  website: "text-[var(--color-channel-website)]",
};

function valueDisplay(source: string, value: string): { label: string; href: string | null } {
  const v = value.trim();
  switch (source) {
    case "linkedin":
      return { label: v, href: `https://linkedin.com/in/${v.replace(/^https?:\/\/(www\.)?linkedin\.com\/in\//, "")}` };
    case "x":
    case "twitter":
      return { label: `@${v.replace(/^@/, "")}`, href: `https://x.com/${v.replace(/^@/, "")}` };
    case "instagram":
      return { label: `@${v.replace(/^@/, "")}`, href: `https://instagram.com/${v.replace(/^@/, "")}` };
    case "github":
      return { label: v, href: `https://github.com/${v}` };
    case "telegram":
      return { label: v, href: null };  // numeric id; no link
    case "telegram_handle":
      return { label: `@${v.replace(/^@/, "")}`, href: `https://t.me/${v.replace(/^@/, "")}` };
    case "phone":
      return { label: v.startsWith("+") ? v : `+${v}`, href: `tel:${v.startsWith("+") ? v : `+${v}`}` };
    case "email":
      return { label: v, href: `mailto:${v}` };
    case "website":
      return { label: v, href: v.startsWith("http") ? v : `https://${v}` };
    default:
      return { label: v, href: null };
  }
}

export function IdentityChip({
  personId,
  identityId,
  source,
  value,
  onRemoved,
  readOnly = false,
  compact = false,
}: {
  personId?: string;
  identityId?: number;
  source: string;
  value: string;
  onRemoved?: () => void;
  readOnly?: boolean;
  compact?: boolean;
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const Icon = ICONS[source] ?? Globe;
  const color = CHANNEL_COLOR[source] ?? "text-muted-foreground";
  const { label, href } = valueDisplay(source, value);
  const canRemove = !readOnly && personId !== undefined && identityId !== undefined;

  return (
    <>
      <div
        className={cn(
          "group inline-flex items-center gap-1.5 rounded-md",
          "border border-border bg-secondary/60 hover:bg-secondary",
          "transition-colors max-w-full",
          compact ? "pl-1.5 pr-1.5 py-0.5" : "pl-2 pr-1 py-1",
        )}
      >
        <Icon className={cn("shrink-0", color, compact ? "h-3 w-3" : "h-3.5 w-3.5")} />
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className={cn(
              "truncate hover:text-primary transition-colors min-w-0",
              compact ? "text-xs" : "text-sm",
            )}
          >
            {label}
          </a>
        ) : (
          <span className={cn("truncate min-w-0", compact ? "text-xs" : "text-sm")}>{label}</span>
        )}
        {canRemove && (
          <button
            onClick={(e) => { e.stopPropagation(); setConfirmOpen(true); }}
            aria-label={`Remove ${source} identity`}
            className={cn(
              "ml-1 grid place-items-center rounded shrink-0",
              compact ? "h-4 w-4" : "h-5 w-5",
              "opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
              "text-muted-foreground hover:bg-destructive/20 hover:text-destructive",
              "transition-opacity transition-colors",
            )}
          >
            <XIcon className={compact ? "h-2.5 w-2.5" : "h-3 w-3"} />
          </button>
        )}
      </div>

      {canRemove && (
        <RemoveIdentityConfirm
          open={confirmOpen}
          onOpenChange={setConfirmOpen}
          personId={personId!}
          identityId={identityId!}
          source={source}
          value={label}
          onRemoved={onRemoved ?? (() => {})}
        />
      )}
    </>
  );
}
