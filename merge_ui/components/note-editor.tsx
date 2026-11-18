"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useEditor, EditorContent, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import Link from "@tiptap/extension-link";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import { Markdown } from "tiptap-markdown";
import {
  Bold, Italic, Strikethrough, Code, Heading1, Heading2, Heading3, List, ListOrdered,
  ListChecks, Quote, SquareCode, Minus, Link2, Pin, PinOff, Trash2, RotateCw, ImagePlus,
} from "lucide-react";
import { Bookmark, NoteImage, NoteYoutube, VideoEmbed, hydrateBookmark, isBareUrl, videoFor } from "@/components/note-extensions";
import { api, type NoteRow } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

const AUTOSAVE_MS = 800;

type SaveState = "saved" | "dirty" | "saving" | "conflict" | "error";

/** The page's content as the editor expects it: the stored document when
 *  there is one, otherwise the markdown mirror (the assistant writes that). */
function initialContent(note: NoteRow): Record<string, unknown> | string {
  return note.doc ?? note.body_md ?? "";
}

type Props = {
  note: NoteRow;
  onSaved: (note: NoteRow) => void;
  onDeleted: (note: NoteRow) => void;
  onReload: (id: string) => Promise<void>;
};

export function NoteEditor({ note, onSaved, onDeleted, onReload }: Props) {
  const [title, setTitle] = useState(note.title);
  const [state, setState] = useState<SaveState>("saved");
  // The last version the server confirmed — carries the updated_at the next
  // save must match, so two writers never silently overwrite each other.
  const latest = useRef<NoteRow>(note);
  const titleRef = useRef(note.title);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const titleInput = useRef<HTMLInputElement>(null);

  const editor = useEditor({
    immediatelyRender: false,
    extensions: [
      StarterKit.configure({ heading: { levels: [1, 2, 3] } }),
      Placeholder.configure({
        placeholder: "Write. Markdown shortcuts work: # heading, - list, [ ] task, > quote, ``` code",
      }),
      Link.configure({ openOnClick: false, autolink: true, defaultProtocol: "https" }),
      TaskList,
      TaskItem.configure({ nested: true }),
      Markdown.configure({ html: false, transformPastedText: true, transformCopiedText: true }),
      NoteImage,
      NoteYoutube,
      VideoEmbed,
      Bookmark,
    ],
    content: initialContent(note),
    editorProps: {
      attributes: { class: "tiptap-body" },
      // Images from the clipboard or a drop go to the server, then in as a
      // block. A bare URL on its own becomes a preview card (video links are
      // claimed first by the video nodes' paste rules).
      handlePaste: (view, event) => {
        const files = Array.from(event.clipboardData?.files ?? []).filter((f) => f.type.startsWith("image/"));
        if (files.length) { void uploadImages(files); return true; }
        const text = event.clipboardData?.getData("text/plain") ?? "";
        if (isBareUrl(text) && !videoFor(text) && view.state.selection.empty) {
          const url = text.trim();
          const { tr, schema } = view.state;
          const node = schema.nodes.bookmark.create({ url, state: "loading" });
          view.dispatch(tr.replaceSelectionWith(node).scrollIntoView());
          void hydrateBookmark(editorRef.current as never, url);
          return true;
        }
        return false;
      },
      handleDrop: (_view, event) => {
        const files = Array.from(event.dataTransfer?.files ?? []).filter((f) => f.type.startsWith("image/"));
        if (!files.length) return false;
        event.preventDefault();
        void uploadImages(files);
        return true;
      },
    },
    onUpdate: () => schedule(),
  });

  async function uploadImages(files: File[]) {
    for (const f of files) {
      try {
        const m = await api.uploadNoteMedia(f, latest.current.id);
        editorRef.current?.chain().focus().setImage({ src: m.url, alt: f.name }).run();
      } catch (e) {
        toast.error(e instanceof Error ? `Image not stored: ${e.message}` : "Image not stored");
      }
    }
  }

  const saveNow = useCallback(async (ed: Editor | null) => {
    if (!ed || ed.isDestroyed) return;
    if (timer.current) { clearTimeout(timer.current); timer.current = null; }
    const target = latest.current;
    setState("saving");
    try {
      const saved = await api.patchNote(target.id, {
        title: titleRef.current,
        doc: ed.getJSON() as Record<string, unknown>,
        body_md: (ed.storage as { markdown: { getMarkdown: () => string } }).markdown.getMarkdown(),
        expect_updated_at: target.updated_at,
      });
      latest.current = saved;
      onSaved(saved);
      setState("saved");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (/API 409/.test(msg)) {
        setState("conflict");
      } else {
        setState("error");
        toast.error(msg);
      }
    }
  }, [onSaved]);

  const schedule = useCallback(() => {
    setState("dirty");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => { void saveNow(editorRef.current); }, AUTOSAVE_MS);
  }, [saveNow]);

  // The editor instance for callbacks created before it existed.
  const editorRef = useRef<Editor | null>(null);
  useEffect(() => { editorRef.current = editor; }, [editor]);

  // Switching pages: flush what is pending for the old one (the cleanup runs
  // before the new content is set, so the editor still holds the old text),
  // then load the new one.
  useEffect(() => {
    if (!editor) return;
    latest.current = note;
    titleRef.current = note.title;
    setTitle(note.title);
    editor.commands.setContent(initialContent(note), false);
    setState("saved");
    if (!note.title && !note.body_md) titleInput.current?.focus();
    return () => {
      if (timer.current) {
        clearTimeout(timer.current);
        timer.current = null;
        void saveNow(editor);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [note.id, editor]);

  // A save elsewhere (the assistant appended) comes in as a new updated_at
  // on the same id: adopt it as the base without disturbing the editor.
  useEffect(() => {
    if (note.id === latest.current.id && note.updated_at !== latest.current.updated_at && state === "saved") {
      latest.current = note;
      editor?.commands.setContent(initialContent(note), false);
      setTitle(note.title);
      titleRef.current = note.title;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [note.updated_at]);

  useEffect(() => {
    const flush = () => { if (timer.current) void saveNow(editorRef.current); };
    window.addEventListener("beforeunload", flush);
    return () => window.removeEventListener("beforeunload", flush);
  }, [saveNow]);

  async function togglePin() {
    try {
      const saved = await api.patchNote(latest.current.id, { pinned: !latest.current.pinned });
      latest.current = saved; onSaved(saved);
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed"); }
  }

  async function remove() {
    const n = latest.current;
    if (!confirm(`Delete "${n.title || "Untitled"}"?\n\nSubpages move up one level.`)) return;
    try { await api.deleteNote(n.id); onDeleted(n); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Delete failed"); }
  }

  function setLink() {
    if (!editor) return;
    const prev = editor.getAttributes("link").href as string | undefined;
    const url = window.prompt("Link URL", prev ?? "https://");
    if (url === null) return;
    if (!url.trim()) { editor.chain().focus().extendMarkRange("link").unsetLink().run(); return; }
    editor.chain().focus().extendMarkRange("link").setLink({ href: url.trim() }).run();
  }

  const fileInput = useRef<HTMLInputElement>(null);

  return (
    <div className="flex flex-col h-full min-h-0">
      <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple className="hidden"
        onChange={(e) => { const fs = Array.from(e.target.files ?? []); e.target.value = ""; if (fs.length) void uploadImages(fs); }} />
      <div className="flex items-center gap-1 px-3 sm:px-6 py-1.5 border-b border-border text-muted-foreground overflow-x-auto">
        <Toolbar editor={editor} onLink={setLink} onImage={() => fileInput.current?.click()} />
        <div className="ml-auto flex items-center gap-1 shrink-0">
          <SaveBadge state={state} onReload={() => onReload(latest.current.id)} />
          <button type="button" onClick={togglePin} title={latest.current.pinned ? "Unpin" : "Pin to top"}
            className="grid place-items-center h-7 w-7 rounded hover:bg-accent hover:text-foreground">
            {latest.current.pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
          </button>
          <button type="button" onClick={remove} title="Delete page"
            className="grid place-items-center h-7 w-7 rounded hover:bg-destructive hover:text-destructive-foreground">
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="max-w-3xl mx-auto w-full px-4 sm:px-8 py-6 sm:py-10">
          <input
            ref={titleInput}
            value={title}
            onChange={(e) => { setTitle(e.target.value); titleRef.current = e.target.value; schedule(); }}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); editor?.commands.focus("start"); } }}
            placeholder="Untitled"
            className="w-full bg-transparent outline-none text-3xl sm:text-4xl font-semibold tracking-tight placeholder:text-muted-foreground/40 mb-4"
          />
          <EditorContent editor={editor} />
        </div>
      </div>
    </div>
  );
}

function SaveBadge({ state, onReload }: { state: SaveState; onReload: () => void }) {
  if (state === "conflict") {
    return (
      <button type="button" onClick={onReload}
        className="inline-flex items-center gap-1 text-[11px] text-amber-400 hover:text-amber-300 px-1.5">
        <RotateCw className="h-3 w-3" /> changed elsewhere — reload
      </button>
    );
  }
  const label = { saved: "Saved", dirty: "Editing…", saving: "Saving…", error: "Not saved" }[state];
  return (
    <span className={cn("text-[11px] px-1.5 tabular", state === "error" ? "text-destructive" : "text-muted-foreground/70")}>
      {label}
    </span>
  );
}

function Toolbar({ editor, onLink, onImage }: { editor: Editor | null; onLink: () => void; onImage: () => void }) {
  if (!editor) return null;
  const imageSelected = editor.isActive("image");
  const Btn = ({ on, active, title, children }: { on: () => void; active?: boolean; title: string; children: React.ReactNode }) => (
    <button type="button" title={title} onMouseDown={(e) => { e.preventDefault(); on(); }}
      className={cn("grid place-items-center h-7 w-7 rounded hover:bg-accent hover:text-foreground shrink-0",
        active && "bg-accent text-foreground")}>
      {children}
    </button>
  );
  const c = () => editor.chain().focus();
  return (
    <>
      <Btn title="Bold (⌘B)" active={editor.isActive("bold")} on={() => c().toggleBold().run()}><Bold className="h-3.5 w-3.5" /></Btn>
      <Btn title="Italic (⌘I)" active={editor.isActive("italic")} on={() => c().toggleItalic().run()}><Italic className="h-3.5 w-3.5" /></Btn>
      <Btn title="Strikethrough" active={editor.isActive("strike")} on={() => c().toggleStrike().run()}><Strikethrough className="h-3.5 w-3.5" /></Btn>
      <Btn title="Inline code" active={editor.isActive("code")} on={() => c().toggleCode().run()}><Code className="h-3.5 w-3.5" /></Btn>
      <Btn title="Link" active={editor.isActive("link")} on={onLink}><Link2 className="h-3.5 w-3.5" /></Btn>
      <span className="w-px h-4 bg-border mx-1 shrink-0" />
      <Btn title="Heading 1" active={editor.isActive("heading", { level: 1 })} on={() => c().toggleHeading({ level: 1 }).run()}><Heading1 className="h-3.5 w-3.5" /></Btn>
      <Btn title="Heading 2" active={editor.isActive("heading", { level: 2 })} on={() => c().toggleHeading({ level: 2 }).run()}><Heading2 className="h-3.5 w-3.5" /></Btn>
      <Btn title="Heading 3" active={editor.isActive("heading", { level: 3 })} on={() => c().toggleHeading({ level: 3 }).run()}><Heading3 className="h-3.5 w-3.5" /></Btn>
      <span className="w-px h-4 bg-border mx-1 shrink-0" />
      <Btn title="Bullet list" active={editor.isActive("bulletList")} on={() => c().toggleBulletList().run()}><List className="h-3.5 w-3.5" /></Btn>
      <Btn title="Numbered list" active={editor.isActive("orderedList")} on={() => c().toggleOrderedList().run()}><ListOrdered className="h-3.5 w-3.5" /></Btn>
      <Btn title="Task list" active={editor.isActive("taskList")} on={() => c().toggleTaskList().run()}><ListChecks className="h-3.5 w-3.5" /></Btn>
      <Btn title="Quote" active={editor.isActive("blockquote")} on={() => c().toggleBlockquote().run()}><Quote className="h-3.5 w-3.5" /></Btn>
      <Btn title="Code block" active={editor.isActive("codeBlock")} on={() => c().toggleCodeBlock().run()}><SquareCode className="h-3.5 w-3.5" /></Btn>
      <Btn title="Divider" on={() => c().setHorizontalRule().run()}><Minus className="h-3.5 w-3.5" /></Btn>
      <span className="w-px h-4 bg-border mx-1 shrink-0" />
      <Btn title="Image (or paste / drop one)" active={imageSelected} on={onImage}><ImagePlus className="h-3.5 w-3.5" /></Btn>
      {imageSelected && (
        <span className="inline-flex items-center gap-0.5 text-[11px] shrink-0">
          {[33, 50, 75, 100].map((w) => (
            <button key={w} type="button" title={`Width ${w}%`}
              onMouseDown={(e) => { e.preventDefault(); c().updateAttributes("image", { width: w === 100 ? null : w }).run(); }}
              className={cn("px-1.5 h-6 rounded hover:bg-accent hover:text-foreground",
                (editor.getAttributes("image").width ?? 100) === w && "bg-accent text-foreground")}>{w}%</button>
          ))}
        </span>
      )}
    </>
  );
}
