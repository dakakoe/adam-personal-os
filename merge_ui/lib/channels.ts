import {
  Linkedin, Instagram, Twitter, Send, Phone, Mail, Github, Globe, MessageCircle,
  CalendarDays, Smartphone,
} from "lucide-react";

/**
 * One definition of how each identity source looks.
 *
 * These maps used to be copy-pasted into identity-chip, similar-persons and
 * persons-list, which meant adding a channel was three near-identical edits
 * and drift was inevitable. Consolidated when WhatsApp arrived, rather than
 * once there were four copies.
 *
 * The colour values live in app/globals.css as --color-channel-* so a chip and
 * a row glyph can never disagree about what "telegram blue" is.
 */
export type ChannelIcon = React.ComponentType<{ className?: string }>;

export const CHANNEL_ICONS: Record<string, ChannelIcon> = {
  linkedin: Linkedin,
  instagram: Instagram,
  x: Twitter,            // visually still bird-ish in lucide; OK shorthand
  twitter: Twitter,
  telegram: Send,
  telegram_handle: Send,
  // lucide ships no WhatsApp brand glyph. MessageCircle is deliberately a
  // different silhouette from Send, so a WhatsApp chip is never mistaken for
  // a Telegram one at a glance.
  whatsapp: MessageCircle,
  meeting: CalendarDays,
  phone: Phone,
  // A card from the user's own phone book, not a phone number identity.
  iphone_contact: Smartphone,
  email: Mail,
  github: Github,
  website: Globe,
};

/** How an identity source reads in a filter or a chip. Anything missing
 *  falls back to a capitalised key, so a new source needs no edit here. */
export const SOURCE_LABELS: Record<string, string> = {
  email: "Email",
  telegram: "Telegram",
  telegram_handle: "Telegram",
  whatsapp: "WhatsApp",
  linkedin: "LinkedIn",
  x: "X",
  twitter: "X",
  instagram: "Instagram",
  phone: "Phone",
  iphone_contact: "iPhone",
  iphone: "iPhone",
  github: "GitHub",
  website: "Website",
  meeting: "Meetings",
};

export function sourceDisplayName(source: string): string {
  return SOURCE_LABELS[source] ?? source.charAt(0).toUpperCase() + source.slice(1);
}

export const CHANNEL_COLORS: Record<string, string> = {
  telegram: "text-[var(--color-channel-telegram)]",
  telegram_handle: "text-[var(--color-channel-telegram)]",
  whatsapp: "text-[var(--color-channel-whatsapp)]",
  meeting: "text-[var(--color-channel-meeting)]",
  email: "text-[var(--color-channel-email)]",
  linkedin: "text-[var(--color-channel-linkedin)]",
  phone: "text-[var(--color-channel-phone)]",
  iphone_contact: "text-[var(--color-channel-phone)]",
  x: "text-[var(--color-channel-x)]",
  twitter: "text-[var(--color-channel-x)]",
  instagram: "text-[var(--color-channel-instagram)]",
  github: "text-[var(--color-channel-github)]",
  website: "text-[var(--color-channel-website)]",
};

/**
 * How a canonical.interaction channel reads to a human.
 *
 * Interaction channels are per-kind ('whatsapp_voice', 'telegram_text'), so
 * this matches on the prefix. Without it, a follow-up settled over WhatsApp
 * would say it was reached via "whatsapp text".
 */
export function channelDisplayName(channel: string | null | undefined): string {
  if (!channel) return "";
  if (channel.startsWith("whatsapp")) return "WhatsApp";
  if (channel.startsWith("telegram")) return "Telegram";
  if (channel === "gmail" || channel.includes("mail")) return "email";
  if (channel === "linkedin") return "LinkedIn";
  if (channel === "meeting") return "meeting";
  if (channel === "manual") return "manual";
  return channel.replace(/_/g, " ");
}

/** What a message with no text actually WAS, read off its channel.
 *
 *  A captioned photo shows its caption; an uncaptioned one would otherwise be
 *  a blank row, which reads as data loss rather than as a picture. */
const EMPTY_BODY_LABELS: Record<string, string> = {
  photo: "Photo", image: "Photo", video: "Video", sticker: "Sticker",
  voice: "Voice note", audio: "Audio", document: "Document",
  location: "Location", contact: "Contact card",
};

export function emptyBodyLabel(channel: string | null | undefined): string {
  const kind = (channel ?? "").split("_").slice(1).join("_");
  return EMPTY_BODY_LABELS[kind] ?? "No text";
}
