"use client";

import type { ReactNode } from "react";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import LinkExt from "@tiptap/extension-link";
import { Markdown } from "tiptap-markdown";
import {
  Bold,
  Heading2,
  Heading3,
  Italic,
  Link as LinkIcon,
  List,
  ListOrdered,
  Quote,
  Redo,
  Undo,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

function Tb({
  onClick,
  active,
  icon: Icon,
  label,
}: {
  onClick: () => void;
  active?: boolean;
  icon: LucideIcon;
  label: string;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cn(
        "flex size-8 items-center justify-center rounded transition-colors",
        active
          ? "bg-[rgb(var(--ember))] text-white"
          : "text-muted-foreground hover:bg-secondary hover:text-foreground"
      )}
    >
      <Icon className="size-4" />
    </button>
  );
}

function Toolbar({ editor, actions }: { editor: Editor; actions?: ReactNode }) {
  const setLink = () => {
    const prev = editor.getAttributes("link").href as string | undefined;
    const url = window.prompt("Link URL", prev ?? "https://");
    if (url === null) return;
    if (url === "") {
      editor.chain().focus().unsetLink().run();
      return;
    }
    editor.chain().focus().extendMarkRange("link").setLink({ href: url }).run();
  };
  return (
    <div className="sticky top-0 z-10 flex flex-wrap items-center gap-0.5 rounded-t-md border-b border-border bg-card px-2 py-1.5">
      <Tb label="Bold" icon={Bold} active={editor.isActive("bold")} onClick={() => editor.chain().focus().toggleBold().run()} />
      <Tb label="Italic" icon={Italic} active={editor.isActive("italic")} onClick={() => editor.chain().focus().toggleItalic().run()} />
      <span className="mx-1 h-5 w-px bg-border" />
      <Tb label="Heading 2" icon={Heading2} active={editor.isActive("heading", { level: 2 })} onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()} />
      <Tb label="Heading 3" icon={Heading3} active={editor.isActive("heading", { level: 3 })} onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()} />
      <span className="mx-1 h-5 w-px bg-border" />
      <Tb label="Bullet list" icon={List} active={editor.isActive("bulletList")} onClick={() => editor.chain().focus().toggleBulletList().run()} />
      <Tb label="Numbered list" icon={ListOrdered} active={editor.isActive("orderedList")} onClick={() => editor.chain().focus().toggleOrderedList().run()} />
      <Tb label="Quote" icon={Quote} active={editor.isActive("blockquote")} onClick={() => editor.chain().focus().toggleBlockquote().run()} />
      <Tb label="Link" icon={LinkIcon} active={editor.isActive("link")} onClick={setLink} />
      <span className="mx-1 h-5 w-px bg-border" />
      <Tb label="Undo" icon={Undo} onClick={() => editor.chain().focus().undo().run()} />
      <Tb label="Redo" icon={Redo} onClick={() => editor.chain().focus().redo().run()} />
      {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function ArticleEditor({
  value,
  onChange,
  actions,
}: {
  value: string;
  onChange: (md: string) => void;
  actions?: ReactNode;
}) {
  const editor = useEditor({
    immediatelyRender: false, // Next.js SSR
    extensions: [
      StarterKit,
      LinkExt.configure({ openOnClick: false, autolink: true }),
      Markdown.configure({ html: false, linkify: true }),
    ],
    content: value,
    editorProps: {
      attributes: {
        class:
          "prose prose-sm prose-neutral max-w-none min-h-[420px] px-4 py-3 focus:outline-none",
      },
    },
    onUpdate: ({ editor }) => onChange(editor.storage.markdown.getMarkdown()),
  });

  if (!editor) return null;

  return (
    <div className="rounded-md border border-border bg-card">
      <Toolbar editor={editor} actions={actions} />
      <EditorContent editor={editor} />
    </div>
  );
}
