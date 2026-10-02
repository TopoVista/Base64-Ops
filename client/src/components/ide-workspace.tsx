/**
 * IDEWorkspace — VS Code-style IDE for Base64-Ops.
 *
 * Panels (left → right):
 *   ActivityBar 40px  |  Explorer 260px (toggleable)
 *   |  EditorArea flex-1 + optional bottom panel
 *   |  CommitsRail 260px (toggleable)
 *
 * The wrapper carries `class="dark"` so this chrome is always dark
 * regardless of the page theme (VS Code is never inverted by OS light mode).
 */

import { DiffViewer } from "@/components/diff-viewer";
import { VSCodeEditor } from "@/components/vscode-editor";
import { getSessionGitDiff, getSessionGitStatus } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTheme } from "@/components/theme-provider";
import type {
  ApprovalRequest,
  CIInvestigationSummary,
  DeliveryPlanSummary,
} from "@/types/agent.type";
import type { SingleSessionResponse } from "@/types/session.type";
import { useQuery } from "@tanstack/react-query";
import { applyPatch } from "diff";
import {
  AlertTriangle,
  CheckCircle,
  ChevronDown,
  ChevronRight,
  Code2,
  FileCode2,
  Folder,
  FolderOpen,
  GitBranch,
  GitCommitHorizontal,
  GitPullRequest,
  LoaderCircle,
  Maximize2,
  Minimize2,
  Pencil,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Terminal,
  X,
  Zap,
} from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

// ─── IDE theme context (propagates resolved theme to sub-components) ──────────

const IDEThemeContext = createContext<"dark" | "light">("dark");
const useIDETheme = () => useContext(IDEThemeContext);

// ─── Types ────────────────────────────────────────────────────────────────────

type FileEntry = { path: string; bytes: number };

type CommitCI = {
  status?: string | null;
  conclusion?: string | null;
  workflow?: string | null;
  url?: string | null;
};

type CommitItem = {
  sha: string;
  shortSha: string;
  author: string;
  date: string;
  subject: string;
  ci?: CommitCI;
};

type CodeFile = {
  path: string;
  headSha?: string;
  contentHash: string;
  content: string;
  truncated: boolean;
  containsRedactions: boolean;
  editable: boolean;
};

type BottomPanel = "terminal" | "source-control" | null;

// ─── Language info ────────────────────────────────────────────────────────────

const LANG: Record<string, { name: string; color: string }> = {
  py:         { name: "Python",     color: "#3572A5" },
  ts:         { name: "TypeScript", color: "#2b7489" },
  tsx:        { name: "TSX",        color: "#2b7489" },
  js:         { name: "JavaScript", color: "#f1e05a" },
  jsx:        { name: "JSX",        color: "#f1e05a" },
  json:       { name: "JSON",       color: "#cbcb41" },
  yml:        { name: "YAML",       color: "#cb171e" },
  yaml:       { name: "YAML",       color: "#cb171e" },
  md:         { name: "Markdown",   color: "#083fa1" },
  css:        { name: "CSS",        color: "#563d7c" },
  scss:       { name: "SCSS",       color: "#c6538c" },
  html:       { name: "HTML",       color: "#e34c26" },
  go:         { name: "Go",         color: "#00ADD8" },
  sh:         { name: "Shell",      color: "#89e051" },
  bash:       { name: "Shell",      color: "#89e051" },
  rs:         { name: "Rust",       color: "#dea584" },
  toml:       { name: "TOML",       color: "#9c4221" },
  java:       { name: "Java",       color: "#b07219" },
  kt:         { name: "Kotlin",     color: "#A97BFF" },
  sql:        { name: "SQL",        color: "#e38c00" },
  dockerfile: { name: "Dockerfile", color: "#384d54" },
  txt:        { name: "Text",       color: "#6e7681" },
  env:        { name: "Env",        color: "#e4a010" },
  rb:         { name: "Ruby",       color: "#701516" },
  php:        { name: "PHP",        color: "#4F5D95" },
  cs:         { name: "C#",         color: "#178600" },
  cpp:        { name: "C++",        color: "#f34b7d" },
  c:          { name: "C",          color: "#555555" },
  swift:      { name: "Swift",      color: "#ffac45" },
  dart:       { name: "Dart",       color: "#00B4AB" },
  lua:        { name: "Lua",        color: "#000080" },
  r:          { name: "R",          color: "#198CE7" },
  proto:      { name: "Proto",      color: "#e97d28" },
  graphql:    { name: "GraphQL",    color: "#e10098" },
  vue:        { name: "Vue",        color: "#2c3e50" },
  svelte:     { name: "Svelte",     color: "#ff3e00" },
  mdx:        { name: "MDX",        color: "#083fa1" },
  xml:        { name: "XML",        color: "#0060ac" },
  ini:        { name: "INI",        color: "#6e7681" },
  cfg:        { name: "Config",     color: "#6e7681" },
  lock:       { name: "Lock",       color: "#6e7681" },
  log:        { name: "Log",        color: "#6e7681" },
  png:        { name: "PNG",        color: "#a074c4" },
  svg:        { name: "SVG",        color: "#FF9900" },
  gif:        { name: "GIF",        color: "#a074c4" },
  jpg:        { name: "JPEG",       color: "#a074c4" },
  jpeg:       { name: "JPEG",       color: "#a074c4" },
  webp:       { name: "WebP",       color: "#a074c4" },
};

function langInfo(path: string): { name: string; color: string } {
  const base = path.split("/").at(-1)?.toLowerCase() ?? "";
  if (base === "dockerfile") return LANG.dockerfile;
  const ext = base.split(".").at(-1) ?? "";
  return LANG[ext] ?? { name: "Plain text", color: "#6e7681" };
}

// ─── File icons (inline SVG, no downloads) ────────────────────────────────────
// Paths sourced from the Seti/Material icon theme used in VS Code.
// Each icon is a 16×16 SVG rendered at 14×14px in the explorer.

const ICON_SVG: Record<string, { path: string; fill: string }> = {
  // Python
  py:  { fill: "#3572A5", path: "M10.5 2C8.6 2 7.5 2.9 7.5 4.5v1h3V6H5.5C3.6 6 2.5 7.1 2.5 9c0 1.9 1 3 2.5 3.5l1.5.4V14h-1v1h4v-1H8.5v-1.4l2-.6c1.3-.4 2-1.4 2-2.5V8h-1v1.5c0 .8-.5 1.3-1.2 1.5L8 11.5V10H5.5C5 10 4.5 9.6 4.5 9s.5-1 1-1h5C11.4 8 12 7.4 12 6.5v-2C12 2.9 11.4 2 10.5 2zm0 1c.5 0 1 .4 1 1v1h-4V4c0-.6.5-1 1-1h2zm-5 7c-.5 0-1-.4-1-1s.5-1 1-1h1v2H5.5z" },
  // TypeScript / TSX
  ts:  { fill: "#2b7489", path: "M2 2h12v12H2V2zm7 9.5v1h1v-1H9zm-1-5V8h4v-.5H8zm0 2V9h2.5c.3 0 .5.2.5.5s-.2.5-.5.5H8v.5c0 .3.2.5.5.5h2v1h-2c-.8 0-1.5-.7-1.5-1.5V8c0-.8.7-1.5 1.5-1.5h2.5V8H9c-.3 0-.5.2-.5.5H8v1z" },
  tsx: { fill: "#2b7489", path: "M2 2h12v12H2V2zm7 9.5v1h1v-1H9zm-1-5V8h4v-.5H8zm0 2V9h2.5c.3 0 .5.2.5.5s-.2.5-.5.5H8v.5c0 .3.2.5.5.5h2v1h-2c-.8 0-1.5-.7-1.5-1.5V8c0-.8.7-1.5 1.5-1.5h2.5V8H9c-.3 0-.5.2-.5.5H8v1z" },
  // JavaScript / JSX
  js:  { fill: "#cbcb41", path: "M2 2h12v12H2V2zm4 9c0 .5.3 1 1 1s1-.4 1-1V7H7v4zm4-4h1v4.5c0 1-.5 1.5-1.5 1.5H9v-1h.5c.3 0 .5-.2.5-.5V7z" },
  jsx: { fill: "#cbcb41", path: "M2 2h12v12H2V2zm4 9c0 .5.3 1 1 1s1-.4 1-1V7H7v4zm4-4h1v4.5c0 1-.5 1.5-1.5 1.5H9v-1h.5c.3 0 .5-.2.5-.5V7z" },
  // JSON
  json:{ fill: "#cbcb41", path: "M3 3h10v10H3V3zm4 7.5c-.8 0-1.5-.7-1.5-1.5v-2c0-.8.7-1.5 1.5-1.5h2c.8 0 1.5.7 1.5 1.5v2c0 .8-.7 1.5-1.5 1.5H7zm0-1h2c.3 0 .5-.2.5-.5V7c0-.3-.2-.5-.5-.5H7c-.3 0-.5.2-.5.5v2c0 .3.2.5.5.5z" },
  // YAML
  yml: { fill: "#cc3e44", path: "M3 3h10v10H3V3zm5 6.2L5.5 6H7l1.5 2.5L10 6h1.5L9 9.2V12H8V9.2z" },
  yaml:{ fill: "#cc3e44", path: "M3 3h10v10H3V3zm5 6.2L5.5 6H7l1.5 2.5L10 6h1.5L9 9.2V12H8V9.2z" },
  // Markdown
  md:  { fill: "#519aba", path: "M14 3H2v10h12V3zM4 11V5h1.5l2 3 2-3H11v6H9.5V8l-2 2.5L5.5 8V11H4z" },
  mdx: { fill: "#519aba", path: "M14 3H2v10h12V3zM4 11V5h1.5l2 3 2-3H11v6H9.5V8l-2 2.5L5.5 8V11H4z" },
  // CSS / SCSS
  css: { fill: "#563d7c", path: "M2 2l1.5 11L8 14.5 12.5 13 14 2H2zm8.9 3H5.1l.2 2h7.4l-.4 4L8 12l-4.3-1-.3-2.9h2l.1 1.5L8 10l2.4-.5.3-2.5H5.4l-.5-5h6.2l-.2 2z" },
  scss:{ fill: "#c6538c", path: "M2 2l1.5 11L8 14.5 12.5 13 14 2H2zm8.9 3H5.1l.2 2h7.4l-.4 4L8 12l-4.3-1-.3-2.9h2l.1 1.5L8 10l2.4-.5.3-2.5H5.4l-.5-5h6.2l-.2 2z" },
  // HTML
  html:{ fill: "#e37933", path: "M2 2l1.5 11L8 14.5 12.5 13 14 2H2zm9.2 3H4.8l.2 2h7l-.4 4L8 12l-3.6-.9-.2-2.1H6l.1 1 1.9.5 1.9-.5.2-2.5H4.6l-.4-4H11.4z" },
  // Go
  go:  { fill: "#00ADD8", path: "M3 5.5C3 4.7 3.7 4 4.5 4h7c.8 0 1.5.7 1.5 1.5v5c0 .8-.7 1.5-1.5 1.5h-7C3.7 12 3 11.3 3 10.5v-5zm6 .5v4h1V6H9zm-2 0v1.5H5.5c-.3 0-.5.2-.5.5s.2.5.5.5H7V10h1V6H7z" },
  // Shell
  sh:  { fill: "#89e051", path: "M2 3h12v10H2V3zm2 2v6h1V5H4zm4 0v1H7c-.3 0-.5.2-.5.5s.2.5.5.5h1c.8 0 1.5.7 1.5 1.5S8.8 10 8 10H6V9h2c.3 0 .5-.2.5-.5S8.3 8 8 8H7c-.8 0-1.5-.7-1.5-1.5S6.2 5 7 5h1z" },
  bash:{ fill: "#89e051", path: "M2 3h12v10H2V3zm2 2v6h1V5H4zm4 0v1H7c-.3 0-.5.2-.5.5s.2.5.5.5h1c.8 0 1.5.7 1.5 1.5S8.8 10 8 10H6V9h2c.3 0 .5-.2.5-.5S8.3 8 8 8H7c-.8 0-1.5-.7-1.5-1.5S6.2 5 7 5h1z" },
  // Rust
  rs:  { fill: "#dea584", path: "M8 2l1 2.2 2.4.3-1.7 1.7.4 2.4L8 7.5 5.9 8.6l.4-2.4L4.6 4.5l2.4-.3L8 2zm-5 8h10l-1 2H4l-1-2zm2 3h6v1H5v-1z" },
  // Docker
  dockerfile:{ fill: "#2496ed", path: "M14 7.5H9v1h5v-1zm-6-4H5v1h3v-1zM5 7h3V6H5v1zm4 0h3V6H9v1zm0-2h3V4H9v1zM5 9h3V8H5v1zm4 0h3V8H9v1zm5.8.7C14.3 9.3 13.5 9 12.5 9H9.5c-.3-.8-.9-1-1.5-1H2v3h.4c.2 1 1 1.7 2 1.7s1.8-.7 2-1.7h3.2c.2 1 1 1.7 2 1.7s1.8-.7 2-1.7h1l.2-1v-.3l-.8-.7h1.8l-.8-.3zM4.4 13c-.6 0-1-.4-1-1s.4-1 1-1 1 .4 1 1-.4 1-1 1zm7.2 0c-.6 0-1-.4-1-1s.4-1 1-1 1 .4 1 1-.4 1-1 1z" },
  // Java
  java:{ fill: "#b07219", path: "M8.7 11.6c.2-.1 2.2-1.1 2.2-2.8 0-1.6-1.4-2.1-2.6-2.1-1.1 0-2.3.5-2.3 1.5h1.2c0-.4.5-.6 1.1-.6.7 0 1.3.3 1.3 1 0 .8-1.5 1.5-2.5 2.1C6 11.2 5 12 5 13.5h6V12.5H6.5c.1-.5.7-.8 2.2-1zm.8-6.7c.2-.2.5-.4.7-.7.5-.5 1.2-1.4.3-2.2l-.5-.5c.5.8-.3 1.5-.8 2C8.7 4 8 4.5 8 5.5s.9 1.5 1 2c.1.3.1.6 0 .8-.5-.4-.5-1.1.5-2.4z" },
  // Kotlin
  kt:  { fill: "#A97BFF", path: "M3 3h10v10H3V3zm1 9l4.5-4.5L13 12l-4.5-4.5L13 3h-1.5L8 6.5 4.5 3H3v1l3 3L3 11l1 1z" },
  // SQL
  sql: { fill: "#e38c00", path: "M8 2C5.2 2 3 3.1 3 4.5v7C3 12.9 5.2 14 8 14s5-1.1 5-2.5v-7C13 3.1 10.8 2 8 2zm3.5 2.5c0 .8-1.6 1.5-3.5 1.5S4.5 5.3 4.5 4.5 6.1 3 8 3s3.5.7 3.5 1.5zM8 13c-1.9 0-3.5-.7-3.5-1.5V10c.9.6 2.1.9 3.5.9s2.6-.3 3.5-.9v1.5c0 .8-1.6 1.5-3.5 1.5z" },
  // TOML / INI / Config
  toml:{ fill: "#9c4221", path: "M3 4h10v1H3V4zm2 4h6v1H5V8zm-2 4h10v1H3v-1zm2-6h6v1H5V6z" },
  ini: { fill: "#6e7681", path: "M3 4h10v1H3V4zm2 4h6v1H5V8zm-2 4h10v1H3v-1zm2-6h6v1H5V6z" },
  cfg: { fill: "#6e7681", path: "M3 4h10v1H3V4zm2 4h6v1H5V8zm-2 4h10v1H3v-1zm2-6h6v1H5V6z" },
  env: { fill: "#e4a010", path: "M3 4h10v1H3V4zm0 3h10v1H3V7zm0 3h7v1H3v-1zm0 3h5v1H3v-1z" },
  // C / C++
  c:   { fill: "#555555", path: "M8 3c-2.8 0-5 2.2-5 5s2.2 5 5 5 5-2.2 5-5-2.2-5-5-5zm-.5 7.5v-1H9c.3 0 .5-.2.5-.5V7c0-.3-.2-.5-.5-.5H7.5v-1H9c.8 0 1.5.7 1.5 1.5v2C10.5 9.8 9.8 10.5 9 10.5H7.5z" },
  cpp: { fill: "#f34b7d", path: "M8 3c-2.8 0-5 2.2-5 5s2.2 5 5 5 5-2.2 5-5-2.2-5-5-5zM6.5 9H5v1H4V9H3V8h1V7h1v1h1.5V9zm5-1v1H9.5v1h-1V9H7V8h1.5V7h1v1H11.5z" },
  // Protobuf
  proto:{ fill: "#e97d28", path: "M8 2C4.7 2 2 4.7 2 8s2.7 6 6 6 6-2.7 6-6-2.7-6-6-6zm0 1c2.8 0 5 2.2 5 5s-2.2 5-5 5-5-2.2-5-5 2.2-5 5-5zM6 6v4h1.5V8.5H9c.8 0 1.5-.7 1.5-1.5S9.8 5.5 9 5.5H7.5L6 6zm1.5 1h1.2c.2 0 .3.1.3.3v.4c0 .2-.1.3-.3.3H7.5V7z" },
  // GraphQL
  graphql:{ fill: "#e10098", path: "M8 2L2 5.5v5L8 14l6-3.5v-5L8 2zm0 1.1l4.8 2.8-4.8 2.8-4.8-2.8L8 3.1zm-5 7V6.3l4.8 2.8-.2 5.2L3 10.1zm6 2.8L8.8 9.1l4.2-2.4V10l-4 2.9z" },
  // Swift
  swift:{ fill: "#ffac45", path: "M13 5.5C11.5 3.6 9.3 2.5 7 3c.6.6 1.2 1.4 1.4 2.2-1.8-1.1-3.9-1.3-5.4-.2C5.8 7.3 8.3 9.2 9 11c.2.5.2 1 0 1.5-.5.6-1.3.8-2 .6C9.4 14 12 13 13 10.5c.5-1.3.3-2.6-.5-3.5.4-.5.6-1.1.5-1.5z" },
  // Dart
  dart:{ fill: "#00B4AB", path: "M6 2L2 6v4l4 4h4l4-4V6L10 2H6zm0 1h4l3 3v3.5L10 13H6l-3-3.5V6l3-3z" },
  // Vue / Svelte
  vue: { fill: "#2c3e50", path: "M2 3h2.5L8 9.5 11.5 3H14L8 13 2 3z" },
  svelte:{ fill: "#ff3e00", path: "M12 4.7C10.9 3 9 2.2 7 2.6 5.8 2.9 5 3.7 4.5 4.7c-1.2.2-2.2.9-2.7 1.9-.8 1.5-.4 3.4.8 4.5-.2.5-.3 1.1-.2 1.6.3 1.6 1.7 2.8 3.4 2.8.3 0 .6 0 .9-.1.7.8 1.7 1.2 2.8 1.1 1.2-.1 2.2-.9 2.7-1.9 1.2-.2 2.2-.9 2.7-1.9.8-1.5.4-3.4-.8-4.5.2-.5.3-1.1.2-1.6-.3-1.6-1.7-2.8-3.3-2.9z" },
  // Ruby
  rb:  { fill: "#701516", path: "M8 2C4.7 2 2 4.7 2 8s2.7 6 6 6 6-2.7 6-6-2.7-6-6-6zm-.5 9L5 8.5 7 7l1 1.5L9 5l2 .5-3.5 5.5z" },
  // PHP
  php: { fill: "#4F5D95", path: "M2 5h12v6H2V5zm2 1v4h1.5V8.5H7c.8 0 1.5-.7 1.5-1.5S7.8 5.5 7 5.5H4zm5 0v4h1.5V8.5H12c.8 0 1.5-.7 1.5-1.5S12.8 5.5 12 5.5H9zm-3.5 1h1.2c.2 0 .3.1.3.3v.4c0 .2-.1.3-.3.3H5.5V7zm5 0h1.2c.2 0 .3.1.3.3v.4c0 .2-.1.3-.3.3H10.5V7z" },
  // R
  r:   { fill: "#198CE7", path: "M8 2C4.7 2 2 4.7 2 8s2.7 6 6 6 6-2.7 6-6-2.7-6-6-6zM6 5h2c1.1 0 2 .9 2 2s-.9 2-2 2H7v3H6V5zm1 1v2h1c.6 0 1-.4 1-1s-.4-1-1-1H7zm3 2l1.5 4H10L8.5 8H10z" },
  // Lua
  lua: { fill: "#000080", path: "M5 3a4 4 0 100 8A4 4 0 005 3zm6 0a2 2 0 100 4A2 2 0 0011 3zm0 5a2 2 0 100 4A2 2 0 0011 8zM7 7a2 2 0 100 4A2 2 0 007 7z" },
  // XML
  xml: { fill: "#0060ac", path: "M2 4h12v8H2V4zm2 1.5v5h1l1-2 1 2h1v-5H7V8L6.3 6H6l-.7 2V5.5H4zm5.5 2L11 9.5H9.5L8 7.5l1.5-2H11L9.5 7.5z" },
  // SVG
  svg: { fill: "#FF9900", path: "M8 1L1 5v6l7 4 7-4V5L8 1zm0 1.4l5.8 3.3-5.8 3.3L2.2 5.7 8 2.4zM2 6.6l5.5 3.2v6.2L2 12.8V6.6zm7 9.4v-6.2l5.5-3.2v6.2L9 16z" },
  // Image types
  png: { fill: "#a074c4", path: "M2 3h12v10H2V3zm1 1v8l3-3 2 2 2-3 3 4V4H3z" },
  gif: { fill: "#a074c4", path: "M2 3h12v10H2V3zm1 1v8l3-3 2 2 2-3 3 4V4H3z" },
  jpg: { fill: "#a074c4", path: "M2 3h12v10H2V3zm1 1v8l3-3 2 2 2-3 3 4V4H3z" },
  jpeg:{ fill: "#a074c4", path: "M2 3h12v10H2V3zm1 1v8l3-3 2 2 2-3 3 4V4H3z" },
  webp:{ fill: "#a074c4", path: "M2 3h12v10H2V3zm1 1v8l3-3 2 2 2-3 3 4V4H3z" },
  // Lock / key files
  lock:{ fill: "#6e7681", path: "M8 2C6.3 2 5 3.3 5 5v1H4v7h8V6h-1V5c0-1.7-1.3-3-3-3zm0 1c1.1 0 2 .9 2 2v1H6V5c0-1.1.9-2 2-2zm0 6a1 1 0 110 2 1 1 0 010-2z" },
  pem: { fill: "#6e7681", path: "M8 2C6.3 2 5 3.3 5 5v1H4v7h8V6h-1V5c0-1.7-1.3-3-3-3zm0 1c1.1 0 2 .9 2 2v1H6V5c0-1.1.9-2 2-2zm0 6a1 1 0 110 2 1 1 0 010-2z" },
  // Text / log
  txt: { fill: "#6e7681", path: "M3 4h10v1H3V4zm0 3h10v1H3V7zm0 3h7v1H3v-1z" },
  log: { fill: "#6e7681", path: "M3 4h10v1H3V4zm0 3h10v1H3V7zm0 3h7v1H3v-1z" },
};

function FileIcon({ path: filePath, size = 14 }: { path: string; size?: number }) {
  const base = filePath.split("/").at(-1)?.toLowerCase() ?? "";
  const isDotfile = base.startsWith(".");
  let ext = isDotfile ? base.slice(1) : (base.split(".").at(-1) ?? "");
  if (base === "dockerfile") ext = "dockerfile";
  if (base === "makefile") ext = "sh";

  const icon = ICON_SVG[ext];
  if (!icon) {
    // Generic document icon
    return (
      <svg width={size} height={size} viewBox="0 0 16 16" className="shrink-0">
        <path d="M4 2h6l3 3v9H3V2h1zm5 0v3h3" fill="none" stroke="#6e7681" strokeWidth="1.2" strokeLinejoin="round" />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" className="shrink-0" aria-hidden>
      <path d={icon.path} fill={icon.fill} />
    </svg>
  );
}

// ─── Explorer tree ────────────────────────────────────────────────────────────

type ExplorerNode = {
  dirs: Record<string, ExplorerNode>;
  files: FileEntry[];
};

function buildTree(files: FileEntry[]): ExplorerNode {
  const root: ExplorerNode = { dirs: {}, files: [] };
  for (const f of files) {
    const parts = f.path.split("/").filter(Boolean);
    const name = parts.pop();
    if (!name) continue;
    let node = root;
    for (const seg of parts) {
      node = node.dirs[seg] ??= { dirs: {}, files: [] };
    }
    node.files.push(f);
  }
  return root;
}

function DirNode({
  name, node, depth, selected, onSelect, defaultOpen,
}: {
  name: string; node: ExplorerNode; depth: number;
  selected: string | null; onSelect: (p: string) => void; defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (Object.keys(node.dirs).length === 0 && node.files.length === 0) return null;
  return (
    <li>
      <button type="button" onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1 rounded py-[3px] pr-2 text-left text-[12px] transition-colors"
        style={{ color: "var(--ide-fg)", paddingLeft: `${6 + depth * 14}px` }}
        onMouseEnter={(e) => (e.currentTarget.style.background = "var(--ide-hover-bg)")}
        onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
        {open ? <ChevronDown className="size-3 shrink-0" style={{ color: "var(--ide-fg-dim)" }} /> : <ChevronRight className="size-3 shrink-0" style={{ color: "var(--ide-fg-dim)" }} />}
        {open ? <FolderOpen className="size-3.5 shrink-0 text-[#dcb67a]" /> : <Folder className="size-3.5 shrink-0 text-[#dcb67a]" />}
        <span className="min-w-0 truncate">{name}</span>
      </button>
      {open && (
        <ul>
          <div className="border-l" style={{ borderColor: "var(--ide-border2)", marginLeft: `${6 + depth * 14 + 8}px` }}>
            {Object.entries(node.dirs).sort(([a], [b]) => a.localeCompare(b)).map(([dn, child]) => (
              <DirNode key={dn} name={dn} node={child} depth={depth + 1}
                selected={selected} onSelect={onSelect} defaultOpen={depth < 1} />
            ))}
            {[...node.files].sort((a, b) => a.path.localeCompare(b.path)).map((f) => (
              <FileLeaf key={f.path} file={f} depth={depth + 1} selected={selected} onSelect={onSelect} />
            ))}
          </div>
        </ul>
      )}
    </li>
  );
}

function FileLeaf({ file, depth, selected, onSelect }: {
  file: FileEntry; depth: number; selected: string | null; onSelect: (p: string) => void;
}) {
  const isActive = selected === file.path;
  const name = file.path.split("/").at(-1) ?? file.path;
  return (
    <li>
      <button type="button" onClick={() => onSelect(file.path)} title={file.path}
        className="flex w-full items-center gap-1.5 rounded py-[3px] pr-2 text-left text-[12px] transition-colors"
        style={{
          paddingLeft: `${6 + depth * 14}px`,
          background: isActive ? "var(--ide-active-file-bg)" : "transparent",
          color: isActive ? "var(--ide-accent)" : "var(--ide-fg)",
          borderLeft: isActive ? "2px solid var(--ide-accent)" : "2px solid transparent",
        }}
        onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = "var(--ide-hover-bg)"; }}
        onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = "transparent"; }}>
        <FileIcon path={file.path} size={14} />
        <span className="min-w-0 flex-1 truncate font-mono">{name}</span>
        {(file.bytes ?? 0) > 200_000 && <span className="shrink-0 text-[9px]" style={{ color: "var(--ide-fg-dim)" }}>large</span>}
      </button>
    </li>
  );
}

function ExplorerPanel({ repoName, files, selected, onSelect }: {
  repoName?: string; files: FileEntry[];
  selected: string | null; onSelect: (path: string) => void;
}) {
  const [search, setSearch] = useState("");
  const tree = useMemo(() => buildTree(files), [files]);
  const filtered = useMemo(
    () => search.trim() ? files.filter((f) => f.path.toLowerCase().includes(search.toLowerCase())) : null,
    [files, search],
  );
  return (
    <aside className="flex h-full min-h-0 w-[260px] shrink-0 flex-col border-r"
      style={{ borderColor: "var(--ide-border)", background: "var(--ide-bg)" }}>
      <div className="shrink-0 border-b px-3 py-2.5" style={{ borderColor: "var(--ide-border)" }}>
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: "var(--ide-fg-dim)" }}>Explorer</p>
        {repoName && <p className="mt-0.5 truncate text-[11px] font-medium" style={{ color: "var(--ide-fg)" }}>{repoName}</p>}
      </div>
      <div className="shrink-0 px-2 py-1.5">
        <div className="flex items-center gap-1.5 rounded border px-2 py-1"
          style={{ borderColor: "var(--ide-border2)", background: "var(--ide-bg3)" }}>
          <Search className="size-3 shrink-0" style={{ color: "var(--ide-fg-dim)" }} />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Filter files…"
            className="flex-1 bg-transparent font-mono text-[11px] outline-none"
            style={{ color: "var(--ide-fg)" }} />
          {search && <button type="button" onClick={() => setSearch("")} style={{ color: "var(--ide-fg-dim)" }}><X className="size-3" /></button>}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto py-1">
        <ul className="space-y-px px-1">
          {filtered ? (
            filtered.map((f) => <FileLeaf key={f.path} file={f} depth={0} selected={selected} onSelect={onSelect} />)
          ) : (
            <>
              {Object.entries(tree.dirs).sort(([a], [b]) => a.localeCompare(b)).map(([name, node]) => (
                <DirNode key={name} name={name} node={node} depth={0} selected={selected} onSelect={onSelect} defaultOpen />
              ))}
              {tree.files.map((f) => <FileLeaf key={f.path} file={f} depth={0} selected={selected} onSelect={onSelect} />)}
            </>
          )}
        </ul>
      </div>
    </aside>
  );
}

// ─── Activity bar ─────────────────────────────────────────────────────────────

function ActivityBar({ explorerOpen, commitsOpen, bottomPanel, onToggleExplorer, onToggleCommits, onToggleBottom }: {
  explorerOpen: boolean; commitsOpen: boolean; bottomPanel: BottomPanel;
  onToggleExplorer: () => void; onToggleCommits: () => void;
  onToggleBottom: (p: "terminal" | "source-control") => void;
}) {
  const btn = (active: boolean) => ({
    borderLeft: active ? "2px solid var(--ide-accent)" : "2px solid transparent",
    color: active ? "#ffffff" : "var(--ide-fg-dim)",
  });
  return (
    <div className="flex w-10 shrink-0 flex-col border-r" style={{ borderColor: "var(--ide-border)", background: "var(--ide-act-bg)" }}>
      <div className="flex flex-1 flex-col items-center gap-1 py-2">
        <button type="button" title="Explorer (E)" onClick={onToggleExplorer}
          className="flex size-10 items-center justify-center transition-colors"
          style={btn(explorerOpen)}>
          <Code2 className="size-[18px]" />
        </button>
        <button type="button" title="Source Control (G)" onClick={() => onToggleBottom("source-control")}
          className="flex size-10 items-center justify-center transition-colors"
          style={btn(bottomPanel === "source-control")}>
          <GitBranch className="size-[18px]" />
        </button>
        <button type="button" title="Commits & CI" onClick={onToggleCommits}
          className="flex size-10 items-center justify-center transition-colors"
          style={btn(commitsOpen)}>
          <GitCommitHorizontal className="size-[18px]" />
        </button>
        <button type="button" title="Terminal (Ctrl+`)" onClick={() => onToggleBottom("terminal")}
          className="flex size-10 items-center justify-center transition-colors"
          style={btn(bottomPanel === "terminal")}>
          <Terminal className="size-[18px]" />
        </button>
      </div>
    </div>
  );
}

// ─── CI badge ─────────────────────────────────────────────────────────────────

function CIBadge({ ci }: { ci?: CommitCI }) {
  if (!ci) return <span className="text-[10px]" style={{ color: "var(--ide-fg-dimmer)" }}>—</span>;
  const { conclusion, status } = ci;
  if (conclusion === "success")
    return <span className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[9px] font-semibold" style={{ background: "var(--ide-success-bg)", color: "var(--ide-success-fg)" }}>✓ passed</span>;
  if (conclusion === "failure")
    return <span className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[9px] font-semibold" style={{ background: "var(--ide-danger-bg)", color: "var(--ide-danger-fg)" }}>✗ failed</span>;
  if (status && !["completed", "not_observed", "unavailable"].includes(status))
    return <span className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[9px] font-semibold" style={{ background: "var(--ide-warning-bg)", color: "var(--ide-warning-fg)" }}><LoaderCircle className="size-2.5 animate-spin" /> running</span>;
  return <span className="rounded-full px-1.5 py-0.5 text-[9px]" style={{ background: "var(--ide-bg3)", color: "var(--ide-fg-dimmer)" }}>no run</span>;
}

// ─── Tab bar ──────────────────────────────────────────────────────────────────

function TabBar({ tabs, active, dirty, onActivate, onClose }: {
  tabs: string[]; active: string | null; dirty: boolean;
  onActivate: (p: string) => void; onClose: (p: string) => void;
}) {
  if (!tabs.length) return null;
  return (
    <div className="flex shrink-0 items-stretch overflow-x-auto border-b"
      style={{ borderColor: "var(--ide-border)", background: "var(--ide-bg2)", scrollbarWidth: "none" }}>
      {tabs.map((p) => {
        const name = p.split("/").at(-1) ?? p;
        const isActive = p === active;
        const { color } = langInfo(p);
        return (
          <div key={p}
            className="group flex min-w-[90px] max-w-[200px] shrink-0 cursor-pointer items-center gap-2 border-r px-3 py-2 text-[12px] transition-colors"
            style={{
              borderRightColor: "var(--ide-border)",
              borderTop: isActive ? "2px solid var(--ide-accent)" : "2px solid transparent",
              background: isActive ? "var(--ide-bg)" : "var(--ide-bg2)",
              color: isActive ? "var(--ide-fg-bright)" : "var(--ide-fg-dimmer)",
            }}
            onClick={() => onActivate(p)}>
            <span className="inline-block h-2 w-2 shrink-0 rounded-sm" style={{ background: color }} />
            <span className="min-w-0 flex-1 truncate font-mono">
              {name}{isActive && dirty ? <span className="ml-0.5" style={{ color: "var(--ide-yellow)" }}>●</span> : null}
            </span>
            <button type="button"
              className="shrink-0 rounded p-0.5 opacity-0 transition-opacity group-hover:opacity-60 hover:!opacity-100"
              onClick={(e) => { e.stopPropagation(); onClose(p); }} aria-label={`Close ${name}`}>
              <X className="size-3" />
            </button>
          </div>
        );
      })}
    </div>
  );
}

// ─── Breadcrumb ───────────────────────────────────────────────────────────────

function Breadcrumb({ path, changed, sha }: { path: string; changed?: boolean; sha?: string }) {
  const { name: langName, color: langColor } = langInfo(path);
  const parts = path.split("/");
  return (
    <div className="flex shrink-0 items-center gap-1 border-b px-3 py-1 text-[11px]"
      style={{ borderColor: "var(--ide-border)", background: "var(--ide-bg)", color: "var(--ide-fg-dim)" }}>
      <div className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden">
        {parts.map((seg, i) => (
          <span key={i} className="flex shrink-0 items-center gap-1">
            {i > 0 && <ChevronRight className="size-2.5" style={{ color: "var(--ide-fg-dimmest)" }} />}
            <span style={i === parts.length - 1 ? { color: "var(--ide-fg)" } : {}}>{seg}</span>
          </span>
        ))}
        {changed && <span className="ml-1 shrink-0" style={{ color: "var(--ide-yellow)" }}>●</span>}
      </div>
      <div className="ml-2 flex shrink-0 items-center gap-2">
        {sha && <span className="font-mono text-[10px]" style={{ color: "var(--ide-fg-dimmer)" }}>{sha.slice(0, 7)}</span>}
        <span className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px]"
          style={{ background: "var(--ide-bg3)", color: "var(--ide-fg)" }}>
          <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: langColor }} />
          {langName}
        </span>
      </div>
    </div>
  );
}

// ─── Source Control panel ─────────────────────────────────────────────────────

function SourceControlPanel({ slugId }: { slugId: string }) {
  const { data, isFetching, refetch } = useQuery({
    queryKey: ["session-git-diff", slugId],
    queryFn: () => getSessionGitDiff(slugId),
    retry: false,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  return (
    <div className="flex h-full min-h-0 flex-col" style={{ background: "var(--ide-bg)" }}>
      <div className="flex shrink-0 items-center gap-2 border-b px-3 py-2" style={{ borderColor: "var(--ide-border)", background: "var(--ide-bg2)" }}>
        <GitBranch className="size-3.5" style={{ color: "var(--ide-teal)" }} />
        <span className="flex-1 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: "var(--ide-fg-dim)" }}>Source Control</span>
        {data?.branch && <span className="font-mono text-[10px]" style={{ color: "var(--ide-fg-dimmer)" }}>{data.branch}</span>}
        <button type="button" onClick={() => refetch()} disabled={isFetching} title="Refresh diff"
          className="disabled:opacity-40" style={{ color: "var(--ide-fg-dim)" }}>
          <RefreshCw className={cn("size-3.5", isFetching && "animate-spin")} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {isFetching && !data ? (
          <div className="flex h-full items-center justify-center gap-2 text-[12px]" style={{ color: "var(--ide-fg-dimmer)" }}>
            <LoaderCircle className="size-4 animate-spin" /> Loading diff…
          </div>
        ) : !data?.success || !data?.diff?.trim() ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <CheckCircle className="size-8" style={{ color: "var(--ide-green)" }} />
            <p className="text-[12px]" style={{ color: "var(--ide-fg-dimmer)" }}>Working tree is clean</p>
            <p className="text-[10px]" style={{ color: "var(--ide-fg-dimmest)" }}>No uncommitted changes in the workspace clone.</p>
          </div>
        ) : (
          <div className="p-2">
            {data.truncated && (
              <p className="mb-2 rounded border px-2 py-1 text-[10px]" style={{ borderColor: "var(--ide-warning-border)", background: "var(--ide-warning-bg)", color: "var(--ide-warning-fg)" }}>
                Diff truncated to 80 KB for safe display.
              </p>
            )}
            <DiffViewer patch={data.diff} viewMode="unified" className="text-[11px] leading-5" />
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Terminal panel ───────────────────────────────────────────────────────────

function TerminalPanel({ slugId }: { slugId: string }) {
  const { data, isFetching, refetch } = useQuery({
    queryKey: ["session-git-status", slugId],
    queryFn: () => getSessionGitStatus(slugId),
    retry: false,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  // Parse `git status --short` lines into coloured entries
  const lines = useMemo(() => {
    const raw = data?.output ?? "";
    if (!raw.trim()) return [];
    return raw.split("\n").filter(Boolean).map((line) => {
      const xy = line.slice(0, 2);
      const path = line.slice(3);
      let color = "var(--ide-fg)";
      if (xy.includes("M")) color = "var(--ide-yellow)";
      else if (xy.includes("A")) color = "var(--ide-green)";
      else if (xy.includes("D")) color = "var(--ide-red)";
      else if (xy.includes("?")) color = "var(--ide-blue)";
      else if (xy.includes("R")) color = "var(--ide-orange)";
      return { xy, path, color };
    });
  }, [data?.output]);

  return (
    <div className="flex h-full min-h-0 flex-col" style={{ background: "var(--ide-bg)" }}>
      <div className="flex shrink-0 items-center gap-2 border-b px-3 py-2" style={{ borderColor: "var(--ide-border)", background: "var(--ide-bg2)" }}>
        <Terminal className="size-3.5" style={{ color: "var(--ide-teal)" }} />
        <span className="flex-1 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: "var(--ide-fg2)" }}>
          Terminal — git status
        </span>
        {data?.branch && (
          <span className="flex items-center gap-1 font-mono text-[10px]" style={{ color: "var(--ide-fg-dimmer)" }}>
            <GitBranch className="size-3" />{data.branch}
          </span>
        )}
        {data?.headSha && (
          <span className="font-mono text-[10px]" style={{ color: "var(--ide-fg-dimmest)" }}>{data.headSha.slice(0, 7)}</span>
        )}
        <button type="button" onClick={() => refetch()} disabled={isFetching} title="Refresh status"
          className="disabled:opacity-40" style={{ color: "var(--ide-fg-dim)" }}>
          <RefreshCw className={cn("size-3.5", isFetching && "animate-spin")} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-3 font-mono text-[12px]">
        {/* Prompt line */}
        <p className="mb-2" style={{ color: "var(--ide-fg-dimmer)" }}>
          <span className="" style={{ color: "var(--ide-green)" }}>$</span>
          <span className="ml-2" style={{ color: "var(--ide-teal)" }}>git status --short</span>
        </p>
        {isFetching && !data ? (
          <div className="flex items-center gap-2" style={{ color: "var(--ide-fg-dimmer)" }}>
            <LoaderCircle className="size-3.5 animate-spin" /> Running…
          </div>
        ) : !data?.success ? (
          <p className="" style={{ color: "var(--ide-red)" }}>git status failed. Workspace may not be ready.</p>
        ) : lines.length === 0 ? (
          <p style={{ color: "var(--ide-green)" }}>nothing to commit, working tree clean</p>
        ) : (
          <div className="space-y-0.5">
            {lines.map((l, i) => (
              <div key={i} className="flex items-baseline gap-2">
                <span className="w-5 shrink-0 text-[10px]" style={{ color: l.color }}>{l.xy}</span>
                <span className="truncate" style={{ color: l.color }}>{l.path}</span>
              </div>
            ))}
          </div>
        )}
        {data && (
          <p className="mt-3" style={{ color: "var(--ide-fg-dimmer)" }}>
            <span style={{ color: "var(--ide-green)" }}>$</span>
            <span className="ml-2 animate-pulse">█</span>
          </p>
        )}
      </div>
    </div>
  );
}

// ─── Commits rail ─────────────────────────────────────────────────────────────

function AuthorAvatar({ name }: { name: string }) {
  const initials = name.split(/[\s@_-]/).filter(Boolean).slice(0, 2).map((s) => s[0]?.toUpperCase() ?? "").join("") || "?";
  let hash = 0;
  for (const c of name) hash = (hash * 31 + c.charCodeAt(0)) & 0xffffffff;
  const hue = Math.abs(hash) % 360;
  return (
    <span className="flex size-5 shrink-0 items-center justify-center rounded-full text-[9px] font-bold text-white"
      style={{ background: `hsl(${hue},55%,38%)` }} title={name}>{initials}</span>
  );
}

function CommitsRail({ commits, loading, deliveredSha, postPushCi }: {
  commits: CommitItem[]; loading: boolean;
  deliveredSha?: string | null; postPushCi?: CommitCI;
}) {
  return (
    <aside className="flex h-full min-h-0 w-[260px] shrink-0 flex-col border-l"
      style={{ borderColor: "var(--ide-border)", background: "var(--ide-bg)" }}>
      <div className="shrink-0 border-b px-3 py-2.5" style={{ borderColor: "var(--ide-border)" }}>
        <p className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: "var(--ide-fg-dim)" }}>
          <GitCommitHorizontal className="size-3.5" style={{ color: "var(--ide-teal)" }} /> Commits &amp; CI
        </p>
      </div>
      <div className="min-h-0 flex-1 space-y-1.5 overflow-auto p-2">
        {deliveredSha && (
          <div className="rounded border px-2.5 py-2" style={{ borderColor: "var(--ide-success-border)", background: "var(--ide-success-bg)" }}>
            <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--ide-success-fg)" }}>Last pushed</p>
            <p className="mt-0.5 font-mono text-[10px]" style={{ color: "var(--ide-teal)" }}>{deliveredSha.slice(0, 12)}</p>
            <div className="mt-1"><CIBadge ci={postPushCi ?? { status: "not_observed" }} /></div>
            <p className="mt-1 text-[10px] leading-4" style={{ color: "var(--ide-fg2)" }}>
              {postPushCi?.conclusion === "success" ? "GitHub confirms checks passed."
                : postPushCi?.conclusion === "failure" ? "Checks failed — inspect the linked run."
                : "Waiting for GitHub Actions to report."}
            </p>
          </div>
        )}
        {loading ? (
          <p className="py-4 text-center text-[11px]" style={{ color: "var(--ide-fg-dimmer)" }}>Loading…</p>
        ) : commits.length ? commits.map((c) => (
          <div key={c.sha} className="rounded border px-2.5 py-2"
            style={{
              borderColor: c.sha === deliveredSha ? "var(--ide-success-border)" : "var(--ide-commit-border)",
              background: c.sha === deliveredSha ? "var(--ide-success-bg)" : "var(--ide-commit-bg)",
            }}>
            <p className="line-clamp-2 text-[11px] leading-[1.5]" style={{ color: "var(--ide-fg-bright)" }}>{c.subject}</p>
            <div className="mt-1.5 flex items-center gap-1.5">
              <AuthorAvatar name={c.author} />
              <span className="min-w-0 flex-1 truncate font-mono text-[9px]" style={{ color: "var(--ide-teal)" }}>{c.shortSha}</span>
              <CIBadge ci={c.ci} />
            </div>
            <div className="mt-1 flex items-center justify-between gap-1">
              <p className="text-[9px]" style={{ color: "var(--ide-fg-dimmer)" }}>{c.date}</p>
              {c.ci?.url && <a href={c.ci.url} target="_blank" rel="noreferrer" className="text-[9px] hover:underline" style={{ color: "var(--ide-accent)" }}>run ↗</a>}
            </div>
          </div>
        )) : (
          <p className="py-4 text-center text-[11px]" style={{ color: "var(--ide-fg-dimmer)" }}>No commit history available.</p>
        )}
        <p className="pt-2 text-center text-[10px] leading-4" style={{ color: "var(--ide-fg-dimmest)" }}>
          CI status updates after GitHub reports a completed run.
        </p>
      </div>
    </aside>
  );
}

// ─── Status bar ───────────────────────────────────────────────────────────────

function StatusBar({ path, branch, repoName, ciStatus, sha, onFullscreen, fullscreen }: {
  path?: string; branch?: string; repoName?: string;
  ciStatus?: string | null; sha?: string;
  onFullscreen: () => void; fullscreen: boolean;
}) {
  return (
    <div className="flex h-[22px] shrink-0 items-center bg-[#007acc] text-[11px] text-white">
      <span className="flex h-full cursor-default items-center gap-1.5 border-r border-white/10 px-3 hover:bg-white/10">
        <GitBranch className="size-3" />
        <span className="max-w-[160px] truncate font-mono">{branch ?? "main"}</span>
      </span>
      {sha && <span className="flex h-full items-center border-r border-white/10 px-3 font-mono opacity-80">{sha.slice(0, 7)}</span>}
      {repoName && <span className="flex h-full max-w-[180px] items-center truncate border-r border-white/10 px-3 opacity-75">{repoName}</span>}
      {path && <span className="flex h-full max-w-[200px] items-center truncate px-3 font-mono opacity-70">{path.split("/").at(-1)}</span>}
      {ciStatus === "success" && <span className="flex h-full items-center gap-1 border-l border-white/10 bg-[#238636]/60 px-3">✓ CI passing</span>}
      {ciStatus === "failure" && <span className="flex h-full items-center gap-1 border-l border-white/10 bg-[#da3633]/50 px-3">✗ CI failing</span>}
      <button type="button" onClick={onFullscreen}
        className="ml-auto flex h-full items-center px-3 opacity-70 hover:bg-white/10 hover:opacity-100"
        title={fullscreen ? "Exit full screen (F11)" : "Full screen (F11)"}>
        {fullscreen ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
      </button>
    </div>
  );
}

// ─── CI banner ────────────────────────────────────────────────────────────────

function CIBanner({ ci, relevantPaths, selectedPath, onJumpToFile, onGenerateProposal, generatingProposal, proposalReady, ciProposalAvailable }: {
  ci: CIInvestigationSummary; relevantPaths: string[];
  selectedPath: string | null; onJumpToFile: (path: string) => void;
  onGenerateProposal: () => void; generatingProposal: boolean;
  proposalReady: boolean; ciProposalAvailable: boolean;
}) {
  const failed = ci.failed_jobs[0];
  return (
    <div className="shrink-0 border-b px-4 py-2.5" style={{ borderColor: "var(--ide-danger-border)", background: "var(--ide-danger-bg)" }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-1 items-start gap-2">
          <span className="mt-0.5 inline-flex shrink-0 items-center gap-1 rounded px-2 py-0.5 text-[10px] font-semibold text-white" style={{ background: "var(--ide-danger-strong)" }}>
            <AlertTriangle className="size-2.5" /> CI FAILURE · RUN #{ci.run_id}
          </span>
          <div className="min-w-0">
            <p className="truncate text-[11px]" style={{ color: "var(--ide-fg2)" }}>
              <span className="font-medium" style={{ color: "var(--ide-fg-bright)" }}>{ci.workflow_name ?? "GitHub Actions"}</span>
              {failed && <span style={{ color: "var(--ide-fg-dim)" }}>{" · "}{failed.name}{failed.failed_step && ` · step: ${failed.failed_step}`}</span>}
            </p>
            {relevantPaths.length > 0 && (
              <div className="mt-1 flex flex-wrap items-center gap-1">
                <span className="text-[10px]" style={{ color: "var(--ide-fg-dim)" }}>Jump to:</span>
                {relevantPaths.slice(0, 4).map((p) => (
                  <button key={p} type="button" onClick={() => onJumpToFile(p)}
                    className="rounded border px-1.5 py-0.5 font-mono text-[10px] transition-colors"
                    style={selectedPath === p
                      ? { borderColor: "var(--ide-accent)", background: "var(--ide-accent-bg)", color: "var(--ide-blue)" }
                      : { borderColor: "var(--ide-border2)", background: "var(--ide-bg3)", color: "var(--ide-fg)" }}>
                    {p.split("/").at(-1)}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="shrink-0">
          {proposalReady ? (
            <span className="inline-flex items-center gap-1.5 rounded border px-3 py-1.5 text-[11px] font-semibold" style={{ borderColor: "var(--ide-success-border)", background: "var(--ide-success-bg)", color: "var(--ide-success-fg)" }}>✓ Proposal ready</span>
          ) : (
            <button type="button" onClick={onGenerateProposal} disabled={generatingProposal || !ciProposalAvailable}
              className={cn("flex items-center gap-1.5 rounded px-3 py-1.5 text-[11px] font-semibold transition-colors disabled:opacity-50", !ciProposalAvailable && "cursor-not-allowed")}
              style={ciProposalAvailable
                ? { background: "var(--ide-accent)", color: "white" }
                : { background: "var(--ide-bg3)", color: "var(--ide-fg-dim)" }}
              title={!ciProposalAvailable ? "Collect CI evidence first via Pipelines" : undefined}>
              {generatingProposal
                ? <><LoaderCircle className="size-3 animate-spin" /> Generating…</>
                : <><Zap className="size-3" />{ciProposalAvailable ? "Analyze & fix CI" : "No CI evidence"}</>}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Review panel ─────────────────────────────────────────────────────────────

function ReviewPanel({ file, draft, deliveryPlan, stagedForCommit, commitMessage, onCommitMessageChange, pendingApproval, onStage, onDeliver, delivering, deliveryResult }: {
  file?: CodeFile; draft: string; deliveryPlan: DeliveryPlanSummary;
  stagedForCommit: boolean; commitMessage: string;
  onCommitMessageChange: (v: string) => void; pendingApproval?: ApprovalRequest;
  onStage: () => void; onDeliver: () => void; delivering: boolean;
  deliveryResult?: { status: string; pull_request_url?: string } | null;
}) {
  const ideTheme = useIDETheme();
  const exactDiff = deliveryPlan.files.map((f) => f.unified_diff).join("\n");
  const passedValidation = (pendingApproval?.validation ?? []).filter((v) => v.status === "passed");
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 border-b px-5 py-3" style={{ borderColor: "var(--ide-danger-border)", background: "var(--ide-danger-bg)" }}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: "var(--ide-danger-fg)" }}>Why it failed</p>
            <p className="mt-0.5 text-[13px] font-medium" style={{ color: "var(--ide-fg-bright)" }}>{deliveryPlan.rationale}</p>
            {passedValidation.length > 0 && (
              <p className="mt-1.5 text-[11px]" style={{ color: "var(--ide-success-fg)" }}>
                ✓ Passed {passedValidation.length} pre-delivery validation {passedValidation.length === 1 ? "check" : "checks"}
              </p>
            )}
          </div>
          <span className="shrink-0 rounded border px-2 py-1 text-[10px] font-semibold" style={{ borderColor: "var(--ide-warning-border)", background: "var(--ide-warning-bg)", color: "var(--ide-warning-fg)" }}>
            {deliveryPlan.risk_level.toUpperCase()} RISK
          </span>
        </div>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden xl:grid-cols-2">
        <div className="flex min-h-0 flex-col overflow-hidden border-r" style={{ borderColor: "var(--ide-border)" }}>
          <div className="flex shrink-0 items-center gap-2 px-4 py-2 text-[10px] font-semibold uppercase tracking-wider" style={{ background: "var(--ide-bg2)", color: "var(--ide-blue)" }}>
            <FileCode2 className="size-3.5" />
            {stagedForCommit ? "Staged exact source" : "Current affected source"}
          </div>
          <div className="min-h-0 flex-1 overflow-hidden p-3">
            {file ? <VSCodeEditor value={draft} onChange={() => {}} path={file.path} sha={file.headSha} editable={false} truncated={false} ideTheme={ideTheme} />
              : <p className="text-[12px]" style={{ color: "var(--ide-fg-dim)" }}>Loading source…</p>}
          </div>
        </div>
        <div className="flex min-h-0 flex-col overflow-hidden">
          <div className="flex shrink-0 items-center justify-between gap-2 px-4 py-2 text-[10px] font-semibold uppercase tracking-wider" style={{ background: "var(--ide-success-bg)", color: "var(--ide-teal)" }}>
            <span className="flex items-center gap-2"><ShieldCheck className="size-3.5" /> Suggested exact change</span>
            <span style={{ color: "var(--ide-fg2)" }}>green = add · red = remove</span>
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            <DiffViewer patch={exactDiff} viewMode="unified" className="text-[11px] leading-5" />
          </div>
        </div>
      </div>
      <div className="shrink-0 border-t p-4" style={{ borderColor: "var(--ide-border)", background: "var(--ide-bg2)" }}>
        {deliveryResult && (
          <div className="mb-3 flex items-center gap-2 rounded border px-3 py-2 text-[12px]" style={{ borderColor: "var(--ide-success-border)", background: "var(--ide-success-bg)" }}>
            <span style={{ color: "var(--ide-success-fg)" }}>
              ✓ Delivery {deliveryResult.status}.{" "}
              {deliveryResult.pull_request_url
                ? <a href={deliveryResult.pull_request_url} target="_blank" rel="noreferrer" className="underline">Open draft PR ↗</a>
                : "Check the Commits & CI rail for the GitHub Actions result."}
            </span>
          </div>
        )}
        {!stagedForCommit ? (
          <div className="flex flex-wrap items-center justify-between gap-4 rounded border p-4" style={{ borderColor: "var(--ide-info-border)", background: "var(--ide-info-bg)" }}>
            <div>
              <p className="text-[12px] font-semibold" style={{ color: "var(--ide-info-fg)" }}>Step 1 — Apply the reviewed change</p>
              <p className="mt-1 max-w-2xl text-[11px] leading-[1.6]" style={{ color: "var(--ide-fg2)" }}>Stages the exact approved diff. Nothing is written to GitHub yet.</p>
            </div>
            <button type="button" onClick={onStage} disabled={!file}
              className="rounded px-4 py-2 text-[12px] font-semibold text-white disabled:opacity-50" style={{ background: "var(--ide-accent)" }}>
              Apply changes
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="flex items-center gap-2 text-[12px] font-semibold" style={{ color: "var(--ide-success-fg)" }}>✓ Step 1 complete — change staged in editor</p>
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
              <div>
                <label className="flex items-center gap-1.5 text-[11px] font-semibold" style={{ color: "var(--ide-fg)" }}>
                  <Pencil className="size-3" style={{ color: "var(--ide-teal)" }} /> Step 2 — Commit message
                </label>
                <div className="relative mt-1.5">
                  <input value={commitMessage} onChange={(e) => onCommitMessageChange(e.target.value)} maxLength={120}
                    className="h-9 w-full rounded border px-3 pr-8 font-mono text-[12px] outline-none" style={{ borderColor: "var(--ide-border2)", background: "var(--ide-bg)", color: "var(--ide-fg)" }}
                    placeholder="Describe this change…" />
                  <Sparkles className="pointer-events-none absolute right-2 top-1/2 size-3.5 -translate-y-1/2" style={{ color: "var(--ide-teal)" }} />
                </div>
                <p className="mt-1 text-[10px]" style={{ color: "var(--ide-fg-dim)" }}>AI-suggested — edit freely.</p>
              </div>
              <div className="flex flex-col justify-end gap-2">
                {pendingApproval ? (
                  <>
                    <button type="button" onClick={onDeliver} disabled={delivering}
                      className="flex items-center gap-2 rounded px-4 py-2 text-[12px] font-semibold text-white disabled:opacity-50" style={{ background: "var(--ide-success-strong)" }}>
                      {delivering
                        ? <><LoaderCircle className="size-4 animate-spin" /> Pushing…</>
                        : <><GitPullRequest className="size-4" />{commitMessage.trim() !== deliveryPlan.title ? "Update & push to GitHub" : "Step 3 — Push to GitHub"}</>}
                    </button>
                    <p className="text-center text-[10px]" style={{ color: "var(--ide-fg-dimmer)" }}>Creates a branch &amp; draft PR</p>
                  </>
                ) : (
                  <p className="text-[11px]" style={{ color: "var(--ide-fg-dim)" }}>Plan no longer pending. Regenerate from current source.</p>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Main export ──────────────────────────────────────────────────────────────

export interface IDEWorkspaceProps {
  data: SingleSessionResponse;
  files: FileEntry[];
  selectedPath: string | null;
  onSelectPath: (path: string) => void;
  file?: CodeFile;
  filePending: boolean;
  fileError: unknown;
  draft: string;
  onDraftChange: (value: string) => void;
  commitMessage: string;
  onCommitMessageChange: (value: string) => void;
  changed: boolean;
  onPropose: () => void;
  proposalPending: boolean;
  commits: CommitItem[];
  commitsPending: boolean;
  ci?: CIInvestigationSummary | null;
  onGenerateCiProposal: () => void;
  ciProposalPending: boolean;
  ciProposalAvailable: boolean;
  deliveryPlan?: DeliveryPlanSummary | null;
  pendingApproval?: ApprovalRequest;
  onDeliver: () => void;
  deliveryPending: boolean;
}

export function IDEWorkspace({
  data, files, selectedPath, onSelectPath,
  file, filePending, fileError, draft, onDraftChange,
  commitMessage, onCommitMessageChange, changed,
  onPropose, proposalPending, commits, commitsPending,
  ci, onGenerateCiProposal, ciProposalPending, ciProposalAvailable,
  deliveryPlan, pendingApproval, onDeliver, deliveryPending,
}: IDEWorkspaceProps) {
  const [fullscreen, setFullscreen] = useState(false);
  const [explorerOpen, setExplorerOpen] = useState(true);
  const [commitsOpen, setCommitsOpen] = useState(true);
  const [bottomPanel, setBottomPanel] = useState<BottomPanel>(null);
  const [stagedForCommit, setStagedForCommit] = useState(false);
  const [openTabs, setOpenTabs] = useState<string[]>([]);
  const containerRef = useRef<HTMLDivElement>(null);

  // Resolve the page theme into "dark" | "light" for the IDE palette.
  const { theme: rawTheme } = useTheme();
  const ideTheme: "dark" | "light" = useMemo(() => {
    if (rawTheme === "system") {
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    return rawTheme === "light" ? "light" : "dark";
  }, [rawTheme]);

  useEffect(() => { setStagedForCommit(false); }, [deliveryPlan?.id]);

  useEffect(() => {
    if (!selectedPath) return;
    setOpenTabs((prev) => prev.includes(selectedPath) ? prev : [...prev, selectedPath]);
  }, [selectedPath]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "F11") { e.preventDefault(); setFullscreen((v) => !v); }
      if (e.key === "Escape" && fullscreen) setFullscreen(false);
      // Ctrl+` toggles terminal (same as VS Code)
      if (e.key === "`" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        setBottomPanel((p) => p === "terminal" ? null : "terminal");
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [fullscreen]);

  const toggleBottom = useCallback((p: "terminal" | "source-control") => {
    setBottomPanel((cur) => cur === p ? null : p);
  }, []);

  const closeTab = useCallback((path: string) => {
    setOpenTabs((prev) => {
      const next = prev.filter((t) => t !== path);
      if (path === selectedPath && next.length) onSelectPath(next[Math.max(0, prev.indexOf(path) - 1)] ?? next[0]);
      return next;
    });
  }, [selectedPath, onSelectPath]);

  const stageExactChange = useCallback(() => {
    const diff = deliveryPlan?.files.find((f) => f.path === selectedPath)?.unified_diff ?? deliveryPlan?.files[0]?.unified_diff;
    if (!file || !diff) { toast.error("Affected source or exact diff unavailable."); return; }
    const result = applyPatch(draft, diff);
    if (result === false) { toast.error("The exact diff no longer applies. Regenerate the proposal."); return; }
    onDraftChange(result);
    setStagedForCommit(true);
    toast.success("Exact change applied to the editor.");
  }, [deliveryPlan, file, draft, selectedPath, onDraftChange]);

  const slugId = data.session.slugId;
  const deliveredSha = data.commandCenter?.deliveryResult?.commit_sha;
  const postPushCi = deliveredSha ? commits.find((c) => c.sha === deliveredSha)?.ci : undefined;
  const session = data.session;
  const branch = session.branchName ?? session.defaultBranch ?? "main";
  const headSha = data.commandCenter?.repository?.commit_sha;
  const latestCiConclusion = commits[0]?.ci?.conclusion;

  const outerCls = cn(
    // ide-workspace + data-ide-theme picks up --ide-* CSS tokens defined in index.css.
    // No hardcoded "dark" class — the IDE now follows the page theme.
    "ide-workspace flex flex-col font-sans overflow-hidden select-none",
    fullscreen ? "fixed inset-0 z-[200]" : "relative h-[calc(100dvh-7rem)] rounded-xl shadow-[0_8px_48px_rgba(0,0,0,0.3)]",
    "border",
  );

  // Inline style applies the data attribute so CSS tokens resolve correctly.
  const outerStyle = { "--ide-resolved-theme": ideTheme } as React.CSSProperties;

  return (
    <div ref={containerRef} className={outerCls} data-ide-theme={ideTheme} style={outerStyle}>
      {/* Title bar */}
      <div className="flex h-8 shrink-0 items-center gap-3 border-b px-4 text-[11px]"
        style={{ borderColor: "var(--ide-border)", background: "var(--ide-bg2)", color: "var(--ide-fg2)" }}>
        <span className="truncate font-medium" style={{ color: "var(--ide-fg)" }}>{session.repoName ?? "Repository"} — Base64 Code</span>
        <span className="ml-auto font-mono text-[10px] opacity-60">{branch}</span>
        <button type="button" onClick={() => setFullscreen((v) => !v)}
          className="shrink-0 rounded p-1 transition-colors"
          style={{ color: "var(--ide-fg-dim)" }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--ide-hover-bg)")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
          title="Toggle fullscreen (F11)">
          {fullscreen ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
        </button>
      </div>

      {/* Main row */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <ActivityBar
          explorerOpen={explorerOpen} commitsOpen={commitsOpen} bottomPanel={bottomPanel}
          onToggleExplorer={() => setExplorerOpen((v) => !v)}
          onToggleCommits={() => setCommitsOpen((v) => !v)}
          onToggleBottom={toggleBottom}
        />

        {explorerOpen && files.length > 0 && (
          <ExplorerPanel repoName={session.repoName ?? undefined} files={files} selected={selectedPath}
            onSelect={(p) => { onSelectPath(p); setOpenTabs((prev) => prev.includes(p) ? prev : [...prev, p]); }} />
        )}

        {/* Editor + optional bottom panel */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {ci && (
            <CIBanner ci={ci} relevantPaths={ci.relevant_paths ?? []} selectedPath={selectedPath}
              onJumpToFile={(p) => { onSelectPath(p); setOpenTabs((prev) => prev.includes(p) ? prev : [...prev, p]); }}
              onGenerateProposal={onGenerateCiProposal} generatingProposal={ciProposalPending}
              proposalReady={Boolean(deliveryPlan)} ciProposalAvailable={ciProposalAvailable} />
          )}
          <TabBar tabs={openTabs} active={selectedPath} dirty={changed} onActivate={onSelectPath} onClose={closeTab} />
          {selectedPath && <Breadcrumb path={selectedPath} changed={changed} sha={file?.headSha} />}

          {/* Editor area — shrinks when bottom panel is open */}
          <div className={cn("flex min-h-0 flex-col overflow-hidden", bottomPanel ? "flex-[3]" : "flex-1")}>
            {deliveryPlan ? (
              <ReviewPanel file={file} draft={draft} deliveryPlan={deliveryPlan}
                stagedForCommit={stagedForCommit} commitMessage={commitMessage}
                onCommitMessageChange={onCommitMessageChange} pendingApproval={pendingApproval}
                onStage={stageExactChange} onDeliver={onDeliver} delivering={deliveryPending}
                deliveryResult={data.commandCenter?.deliveryResult} />
            ) : (
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                <div className="min-h-0 flex-1 overflow-hidden">
                  {filePending ? (
                    <div className="flex h-full items-center justify-center">
                      <div className="flex items-center gap-2 text-[12px]" style={{ color: "var(--ide-fg-dim)" }}>
                        <LoaderCircle className="size-4 animate-spin" /> Loading file…
                      </div>
                    </div>
                  ) : fileError ? (
                    <div className="flex h-full items-center justify-center">
                      <p className="text-[12px]" style={{ color: "var(--ide-red)" }}>The selected file could not be read safely.</p>
                    </div>
                  ) : file ? (
                    <div className="h-full overflow-hidden">
                      <VSCodeEditor value={draft} onChange={onDraftChange} path={file.path} sha={file.headSha}
                        editable={file.editable && !file.truncated} truncated={file.truncated} ideTheme={ideTheme} />
                    </div>
                  ) : (
                    <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
                      <Code2 className="size-14" style={{ color: "var(--ide-bg3)" }} />
                      <div>
                        <p className="text-[14px]" style={{ color: "var(--ide-fg-dim)" }}>Select a file from the Explorer</p>
                        <p className="mt-1 text-[11px]" style={{ color: "var(--ide-fg-dimmest)" }}>Changes become reviewable proposals — nothing pushes until you approve.</p>
                      </div>
                    </div>
                  )}
                </div>
                {file && !file.truncated && (
                  <div className="shrink-0 border-t px-4 py-2.5" style={{ borderColor: "var(--ide-border)", background: "var(--ide-bg2)" }}>
                    <div className="flex flex-wrap items-center gap-3">
                      <input value={commitMessage} onChange={(e) => onCommitMessageChange(e.target.value)}
                        maxLength={120} placeholder={`Update ${file.path}`}
                        className="h-8 min-w-0 max-w-sm flex-1 rounded border px-3 font-mono text-[11px] outline-none" style={{ borderColor: "var(--ide-border2)", background: "var(--ide-bg)", color: "var(--ide-fg)" }} />
                      <button type="button" onClick={onPropose} disabled={!changed || proposalPending || !file.editable}
                        className="flex items-center gap-1.5 rounded px-3 py-1.5 text-[11px] font-semibold text-white disabled:opacity-50" style={{ background: "var(--ide-accent)" }}>
                        {proposalPending ? <><LoaderCircle className="size-3 animate-spin" /> Validating…</>
                          : <><ShieldCheck className="size-3" /> Generate reviewed proposal</>}
                      </button>
                      {!file.editable && <span className="text-[10px]" style={{ color: "var(--ide-yellow)" }}>Read-only: redacted values</span>}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Bottom panel (Terminal / Source Control) */}
          {bottomPanel && (
            <div className="flex min-h-[200px] flex-[1.2] flex-col overflow-hidden border-t" style={{ borderColor: "var(--ide-border)" }}>
              {/* Panel tab bar */}
              <div className="flex shrink-0 items-center border-b" style={{ borderColor: "var(--ide-border)", background: "var(--ide-bg2)" }}>
                <button type="button" onClick={() => setBottomPanel("terminal")}
                  className={cn("flex items-center gap-1.5 px-4 py-1.5 text-[11px] font-medium transition-colors", bottomPanel === "terminal" && "border-b-2")}
                  style={bottomPanel === "terminal"
                    ? { borderBottomColor: "var(--ide-accent)", background: "var(--ide-bg)", color: "var(--ide-fg-bright)" }
                    : { color: "var(--ide-fg-dimmer)" }}>
                  <Terminal className="size-3.5" /> Terminal
                </button>
                <button type="button" onClick={() => setBottomPanel("source-control")}
                  className={cn("flex items-center gap-1.5 px-4 py-1.5 text-[11px] font-medium transition-colors", bottomPanel === "source-control" && "border-b-2")}
                  style={bottomPanel === "source-control"
                    ? { borderBottomColor: "var(--ide-accent)", background: "var(--ide-bg)", color: "var(--ide-fg-bright)" }
                    : { color: "var(--ide-fg-dimmer)" }}>
                  <GitBranch className="size-3.5" /> Source Control
                </button>
                <button type="button" onClick={() => setBottomPanel(null)}
                  className="ml-auto px-3 py-1.5" style={{ color: "var(--ide-fg-dim)" }} title="Close panel">
                  <X className="size-3.5" />
                </button>
              </div>
              <div className="min-h-0 flex-1 overflow-hidden">
                {bottomPanel === "terminal"
                  ? <TerminalPanel slugId={slugId} />
                  : <SourceControlPanel slugId={slugId} />}
              </div>
            </div>
          )}
        </div>

        {commitsOpen && (
          <CommitsRail commits={commits} loading={commitsPending} deliveredSha={deliveredSha} postPushCi={postPushCi} />
        )}
      </div>

      <StatusBar path={selectedPath ?? undefined} branch={branch} sha={headSha ?? undefined}
        repoName={session.repoName ?? undefined} ciStatus={latestCiConclusion}
        onFullscreen={() => setFullscreen((v) => !v)} fullscreen={fullscreen} />
    </div>
  );
}
