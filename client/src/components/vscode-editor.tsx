import MonacoEditor, { type OnMount } from "@monaco-editor/react";
import { Braces, Check, Copy, FileCode2 } from "lucide-react";
import { useCallback, useMemo } from "react";
import { toast } from "sonner";

type Props = {
  value: string;
  onChange: (value: string) => void;
  path: string;
  sha?: string;
  editable: boolean;
  truncated?: boolean;
  /** "dark" uses VS Code Dark+; "light" uses VS Code Light+. Defaults to "dark". */
  ideTheme?: "dark" | "light";
};

const EXTENSION_LANGUAGES: Record<string, string> = {
  py: "python", pyw: "python", ts: "typescript", tsx: "typescript",
  js: "javascript", jsx: "javascript", json: "json", yml: "yaml",
  yaml: "yaml", md: "markdown", html: "html", css: "css", scss: "scss",
  go: "go", sh: "shell", bash: "shell", toml: "ini", dockerfile: "dockerfile",
};

function languageFor(path: string) {
  const filename = path.split("/").at(-1)?.toLowerCase() ?? "";
  if (filename === "dockerfile") return "dockerfile";
  return EXTENSION_LANGUAGES[filename.split(".").at(-1) ?? ""] ?? "plaintext";
}

function languageLabel(path: string) {
  const language = languageFor(path);
  if (language === "plaintext") return "Text";
  if (language === "typescript" && path.endsWith(".tsx")) return "TypeScript React";
  return language[0].toUpperCase() + language.slice(1);
}

/** Monaco is VS Code's editor core. Its browser draft remains proposal-only. */
export function VSCodeEditor({ value, onChange, path, sha, editable, truncated, ideTheme = "dark" }: Props) {
  const lineCount = useMemo(() => Math.max(1, value.split("\n").length), [value]);
  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success("File contents copied.");
    } catch {
      toast.error("Clipboard access is unavailable in this browser.");
    }
  }, [value]);
  const onMount: OnMount = useCallback((editor) => editor.focus(), []);

  return <section
    data-ide-theme={ideTheme}
    className="flex h-full flex-col overflow-hidden rounded-lg border shadow-[0_20px_60px_-30px_rgba(0,0,0,0.35)]"
    style={{ borderColor: "var(--ide-border2)", background: "var(--ide-bg)" }}
  >
    <header className="flex min-h-10 items-center gap-2 border-b px-3 text-xs" style={{ borderColor: "var(--ide-border2)", background: "var(--ide-bg2)", color: "var(--ide-fg)" }}>
      <FileCode2 className="size-3.5" style={{ color: "var(--ide-blue)" }} />
      <span className="min-w-0 flex-1 truncate font-mono">{path}</span>
      <span className="hidden rounded px-2 py-0.5 text-[10px] sm:inline" style={{ background: "var(--ide-bg3)", color: "var(--ide-blue)" }}>{languageLabel(path)}</span>
      {sha ? <span className="hidden font-mono text-[10px] md:inline" style={{ color: "var(--ide-fg-dim)" }}>{sha.slice(0, 12)}</span> : null}
      {editable ? <span className="inline-flex items-center gap-1 text-[10px]" style={{ color: "var(--ide-green)" }}><Check className="size-3" />Draft</span> : <span className="text-[10px]" style={{ color: "var(--ide-yellow)" }}>{truncated ? "Read-only: large file" : "Read-only: redacted"}</span>}
      <button type="button" onClick={() => void copy()} className="rounded p-1" style={{ color: "var(--ide-blue)" }} aria-label="Copy code"><Copy className="size-3.5" /></button>
    </header>
    <div className="h-full min-h-[22rem]" style={{ background: "var(--ide-bg)" }}>
      <MonacoEditor
        key={path}
        height="100%"
        language={languageFor(path)}
        theme={ideTheme === "light" ? "vs" : "vs-dark"}
        value={value}
        onChange={(next) => onChange(next ?? "")}
        onMount={onMount}
        options={{
          readOnly: !editable,
          automaticLayout: true,
          minimap: { enabled: true, maxColumn: 110, renderCharacters: false },
          fontSize: 13,
          lineHeight: 21,
          fontFamily: "Cascadia Code, Consolas, 'Courier New', monospace",
          fontLigatures: true,
          renderLineHighlight: "all",
          scrollBeyondLastLine: false,
          smoothScrolling: true,
          cursorSmoothCaretAnimation: "on",
          folding: true,
          glyphMargin: true,
          bracketPairColorization: { enabled: true },
          guides: { bracketPairs: true, indentation: true },
          wordWrap: "off",
          padding: { top: 10, bottom: 10 },
          overviewRulerBorder: false,
          stickyScroll: { enabled: true },
        }}
      />
    </div>
    <footer className="flex items-center justify-between border-t px-3 py-1 text-[10px] text-white" style={{ borderColor: "var(--ide-border2)", background: "var(--ide-accent)" }}>
      <span className="inline-flex items-center gap-1"><Braces className="size-3" />{languageLabel(path)}</span>
      <span>{lineCount} lines · UTF-8</span>
    </footer>
  </section>;
}
