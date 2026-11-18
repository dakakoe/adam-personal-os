"use client";

/**
 * Rich blocks for the notes editor beyond StarterKit: images stored on the
 * droplet, link preview cards, and video players. Each node also says how
 * it looks in the markdown mirror — images as `![alt](url)`, cards and
 * videos as plain links — so the assistant (and search) see a faithful page
 * and a page rebuilt from markdown still has working links.
 */

import { mergeAttributes, Node, nodePasteRule } from "@tiptap/core";
import { NodeViewWrapper, ReactNodeViewRenderer, type NodeViewProps } from "@tiptap/react";
import Image from "@tiptap/extension-image";
import Youtube from "@tiptap/extension-youtube";
import { ExternalLink, Link2, Loader2 } from "lucide-react";
import { api } from "@/lib/api";

type MdState = { write: (s: string) => void; closeBlock: (n: unknown) => void; esc?: (s: string) => string };

const escMd = (s: string) => s.replace(/([\[\]()\\])/g, "\\$1");

// --- images -------------------------------------------------------------------

/** StarterKit-compatible image with a width (percent) the toolbar can set. */
export const NoteImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      width: {
        default: null,
        parseHTML: (el) => el.getAttribute("data-width"),
        renderHTML: (attrs) => (attrs.width ? { "data-width": attrs.width, style: `width:${attrs.width}%` } : {}),
      },
    };
  },
}).configure({ inline: false, allowBase64: false, HTMLAttributes: { class: "note-image" } });

export const VIDEO_RX: { provider: "youtube" | "vimeo" | "loom"; rx: RegExp; src: (id: string) => string }[] = [
  { provider: "youtube", rx: /^https?:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?(?:.*&)?v=|shorts\/|embed\/)|youtu\.be\/)([A-Za-z0-9_-]{11})/, src: (id) => `https://www.youtube-nocookie.com/embed/${id}` },
  { provider: "vimeo", rx: /^https?:\/\/(?:www\.)?vimeo\.com\/(?:video\/)?(\d+)/, src: (id) => `https://player.vimeo.com/video/${id}` },
  { provider: "loom", rx: /^https?:\/\/(?:www\.)?loom\.com\/(?:share|embed)\/([A-Za-z0-9]+)/, src: (id) => `https://www.loom.com/embed/${id}` },
];

export function videoFor(url: string): { provider: string; src: string } | null {
  for (const v of VIDEO_RX) {
    const m = v.rx.exec(url.trim());
    if (m) return { provider: v.provider, src: v.src(m[1]) };
  }
  return null;
}

export function isBareUrl(text: string): boolean {
  const t = text.trim();
  return /^https?:\/\/\S+$/i.test(t) && !/\s/.test(t);
}

// --- YouTube (official extension, with a markdown form) -------------------------

export const NoteYoutube = Youtube.extend({
  addStorage() {
    return {
      markdown: {
        serialize(state: MdState, node: { attrs: { src: string } }) {
          state.write(`[▶ YouTube](${node.attrs.src})`);
          state.closeBlock(node);
        },
        parse: {},
      },
    };
  },
}).configure({ nocookie: true, controls: true, width: 640, height: 360, HTMLAttributes: { class: "note-video" } });

// --- Vimeo / Loom ---------------------------------------------------------------

export const VideoEmbed = Node.create({
  name: "videoEmbed",
  group: "block",
  atom: true,
  draggable: true,
  addAttributes() {
    return { src: { default: null }, provider: { default: null }, url: { default: null } };
  },
  parseHTML() {
    return [{ tag: "div[data-video-embed]", getAttrs: (el) => ({
      src: (el as HTMLElement).getAttribute("data-src"),
      provider: (el as HTMLElement).getAttribute("data-provider"),
      url: (el as HTMLElement).getAttribute("data-url"),
    }) }];
  },
  renderHTML({ HTMLAttributes }) {
    const { src, provider, url } = HTMLAttributes as { src: string; provider: string; url: string };
    return ["div", mergeAttributes({ "data-video-embed": "", "data-src": src, "data-provider": provider, "data-url": url, class: "note-video" }),
      ["iframe", { src, allow: "autoplay; fullscreen; picture-in-picture", allowfullscreen: "true", frameborder: "0", loading: "lazy" }]];
  },
  addPasteRules() {
    return VIDEO_RX.filter((v) => v.provider !== "youtube").map((v) =>
      nodePasteRule({
        find: new RegExp(v.rx.source, "g"),
        type: this.type,
        getAttributes: (match) => ({ src: v.src(match[1]), provider: v.provider, url: match[0] }),
      }));
  },
  addStorage() {
    return {
      markdown: {
        serialize(state: MdState, node: { attrs: { url: string; provider: string; src: string } }) {
          state.write(`[▶ ${node.attrs.provider}](${node.attrs.url || node.attrs.src})`);
          state.closeBlock(node);
        },
        parse: {},
      },
    };
  },
});

// --- link preview card -----------------------------------------------------------

function BookmarkView({ node, updateAttributes, selected }: NodeViewProps) {
  const { url, title, description, image, site, state } = node.attrs as {
    url: string; title: string | null; description: string | null; image: string | null; site: string | null; state: "loading" | "ready" | "plain";
  };
  const host = (() => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; } })();
  return (
    <NodeViewWrapper className={`note-bookmark${selected ? " is-selected" : ""}`} data-drag-handle>
      <a href={url} target="_blank" rel="noreferrer noopener" className="note-bookmark-link" contentEditable={false}>
        <div className="note-bookmark-body">
          <div className="note-bookmark-title">
            {state === "loading" ? <Loader2 className="h-3.5 w-3.5 animate-spin inline mr-1" /> : <Link2 className="h-3.5 w-3.5 inline mr-1 opacity-60" />}
            {title || url}
          </div>
          {description && <div className="note-bookmark-desc">{description}</div>}
          <div className="note-bookmark-site"><ExternalLink className="h-3 w-3 inline mr-1" />{site || host}</div>
        </div>
        {image && <img src={image} alt="" className="note-bookmark-img" loading="lazy" onError={() => updateAttributes({ image: null })} />}
      </a>
    </NodeViewWrapper>
  );
}

export const Bookmark = Node.create({
  name: "bookmark",
  group: "block",
  atom: true,
  draggable: true,
  addAttributes() {
    return {
      url: { default: null }, title: { default: null }, description: { default: null },
      image: { default: null }, site: { default: null }, state: { default: "loading" },
    };
  },
  parseHTML() {
    return [{ tag: "div[data-bookmark]", getAttrs: (el) => {
      const e = el as HTMLElement;
      return { url: e.getAttribute("data-url"), title: e.getAttribute("data-title"), description: e.getAttribute("data-description"),
               image: e.getAttribute("data-image"), site: e.getAttribute("data-site"), state: "ready" };
    } }];
  },
  renderHTML({ HTMLAttributes }) {
    const a = HTMLAttributes as Record<string, string | null>;
    return ["div", mergeAttributes({ "data-bookmark": "", "data-url": a.url, "data-title": a.title, "data-description": a.description,
      "data-image": a.image, "data-site": a.site, class: "note-bookmark" }), ["a", { href: a.url, target: "_blank", rel: "noreferrer" }, a.title || a.url]];
  },
  addNodeView() {
    return ReactNodeViewRenderer(BookmarkView);
  },
  addStorage() {
    return {
      markdown: {
        serialize(state: MdState, node: { attrs: { url: string; title: string | null } }) {
          const t = node.attrs.title ? escMd(node.attrs.title) : node.attrs.url;
          state.write(`[${t}](${node.attrs.url})`);
          state.closeBlock(node);
        },
        parse: {},
      },
    };
  },
});

/** Fill a freshly inserted bookmark from the server-side preview. Finds the
 *  node by url so it still works if the user kept typing meanwhile. */
export async function hydrateBookmark(editor: { state: { doc: { descendants: (f: (n: { type: { name: string }; attrs: Record<string, unknown> }, pos: number) => void) => void } }; view: { dispatch: (tr: unknown) => void; state: { tr: { setNodeMarkup: (pos: number, type: null, attrs: Record<string, unknown>) => unknown } } } }, url: string) {
  let preview: Awaited<ReturnType<typeof api.linkPreview>> | null = null;
  try { preview = await api.linkPreview(url); } catch { preview = null; }
  const attrs = preview && preview.title
    ? { url, title: preview.title, description: preview.description, image: preview.image_url, site: preview.site_name, state: "ready" }
    : { url, title: null, description: null, image: null, site: null, state: "plain" };
  editor.state.doc.descendants((n, pos) => {
    if (n.type.name === "bookmark" && n.attrs.url === url && n.attrs.state === "loading") {
      editor.view.dispatch(editor.view.state.tr.setNodeMarkup(pos, null, attrs));
    }
  });
}
