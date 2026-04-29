import { jsPDF } from "jspdf";
import { extractPlotAttachments } from "../components/plotAttachments";
import { plotCaptionFromMeta } from "../plotLabels";
import type { ContentBlock, Message, PlotAttachment } from "../types";
import { stripTurnActivityBlocks } from "./messageStrip";

function sanitizeFileName(title: string): string {
  const t = title.trim() || "bluebot-chat";
  const safe = t.replace(/[^\w\-.\s()]/g, "_").replace(/\s+/g, "-").slice(0, 80);
  return safe || "bluebot-chat";
}

export interface ExportTranscriptToPdfOptions {
  messages: Message[];
  title: string;
  onProgress?: (label: string) => void;
}

interface PdfState {
  doc: jsPDF;
  pageW: number;
  pageH: number;
  margin: number;
  innerW: number;
  bottomY: number;
  cursorY: number;
}

interface PlotImageData {
  dataUrl: string;
  width: number;
  height: number;
}

const COLORS = {
  brand: [58, 95, 154] as const,
  text: [15, 23, 42] as const,
  muted: [100, 116, 139] as const,
  rule: [219, 229, 243] as const,
  codeBg: [248, 250, 252] as const,
};

function shouldInsertSpace(prev: string, next: string): boolean {
  const a = prev.trimEnd().at(-1);
  const b = next.trimStart().at(0);
  if (!a || !b) return false;
  if (/\s/.test(a) || /\s/.test(b)) return false;
  return /[\p{L}\p{N})\]]/u.test(a) && /[\p{L}\p{N}([{]/u.test(b);
}

function joinTextBlocks(parts: string[]): string {
  return parts.reduce((out, part) => {
    if (!out) return part;
    return `${out}${shouldInsertSpace(out, part) ? " " : ""}${part}`;
  }, "");
}

function messageText(content: string | ContentBlock[]): string {
  if (typeof content === "string") return content;
  return joinTextBlocks(
    content
      .filter((block) => block.type === "text" && block.text)
      .map((block) => block.text!),
  );
}

function buildPlotsByIndex(messages: Message[]): Map<number, PlotAttachment[]> {
  const plotsByIndex = new Map<number, PlotAttachment[]>();
  let queued: PlotAttachment[] = [];
  messages.forEach((msg, i) => {
    const next = extractPlotAttachments(msg.content);
    if (next.length > 0) queued.push(...next);
    if (msg.role === "assistant" && queued.length > 0) {
      plotsByIndex.set(i, [...queued]);
      queued = [];
    }
  });
  return plotsByIndex;
}

function formatExportedAt(date: Date): string {
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function normalizeForPdf(text: string): string {
  return text
    .replace(/\r\n?/g, "\n")
    .replace(/\0/g, "")
    .replace(/\u00a0/g, " ")
    .replace(/\u2705\ufe0f?/g, "OK:")
    .replace(/\u26a0\ufe0f?/g, "Warning:")
    .replace(/[\u2714\u2713]/g, "OK:")
    .replace(/[\u274c\u2717\u2718]/g, "Error:")
    .replace(/\u2139\ufe0f?/g, "Info:")
    .replace(/[\u200b-\u200d]/g, "")
    .replace(/\ufe0f/g, "")
    .replace(/[\u{1f300}-\u{1faff}]/gu, "")
    .replace(/[\u2600-\u27bf]/g, "")
    .replace(/[\u2b00-\u2bff]/g, "")
    .replace(/[“”]/g, "\"")
    .replace(/[‘’]/g, "'")
    .replace(/[–—]/g, "-")
    // jsPDF's built-in Helvetica is WinAnsi-based. Keep emitted text ASCII so
    // one stray unsupported symbol cannot force UTF-16-looking PDF extraction.
    .replace(/[^\n\r\t\x20-\x7e]/g, "")
    .replace(/[ \t]+$/gm, "");
}

function stripMarkdownInline(text: string): string {
  return normalizeForPdf(text)
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/_([^_]+)_/g, "$1");
}

function setTextColor(doc: jsPDF, color: readonly [number, number, number]) {
  doc.setTextColor(color[0], color[1], color[2]);
}

function setDrawColor(doc: jsPDF, color: readonly [number, number, number]) {
  doc.setDrawColor(color[0], color[1], color[2]);
}

function setFillColor(doc: jsPDF, color: readonly [number, number, number]) {
  doc.setFillColor(color[0], color[1], color[2]);
}

function lineHeight(fontSize: number, multiplier = 1.35): number {
  return fontSize * multiplier;
}

function splitText(doc: jsPDF, text: string, width: number): string[] {
  const out = doc.splitTextToSize(text, width);
  return Array.isArray(out) ? out : [out];
}

function ensureSpace(state: PdfState, height: number) {
  if (state.cursorY > state.margin && state.cursorY + height > state.bottomY) {
    state.doc.addPage();
    state.cursorY = state.margin;
  }
}

function drawRule(state: PdfState, y = state.cursorY) {
  const { doc, margin, innerW } = state;
  setDrawColor(doc, COLORS.rule);
  doc.setLineWidth(0.8);
  doc.line(margin, y, margin + innerW, y);
}

function drawWrappedText(
  state: PdfState,
  text: string,
  opts: {
    x?: number;
    width?: number;
    fontSize?: number;
    fontStyle?: "normal" | "bold" | "italic";
    font?: "helvetica" | "courier";
    color?: readonly [number, number, number];
    leading?: number;
  } = {},
) {
  const { doc } = state;
  const x = opts.x ?? state.margin;
  const width = opts.width ?? state.innerW;
  const fontSize = opts.fontSize ?? 11;
  const leading = opts.leading ?? lineHeight(fontSize);
  doc.setFont(opts.font ?? "helvetica", opts.fontStyle ?? "normal");
  doc.setFontSize(fontSize);
  setTextColor(doc, opts.color ?? COLORS.text);
  const lines = splitText(doc, normalizeForPdf(text), width);
  for (const line of lines) {
    ensureSpace(state, leading);
    doc.text(line, x, state.cursorY);
    state.cursorY += leading;
  }
}

function drawParagraph(state: PdfState, text: string, indent = 0) {
  drawWrappedText(state, stripMarkdownInline(text), {
    x: state.margin + indent,
    width: state.innerW - indent,
    fontSize: 11,
    leading: 15.5,
  });
  state.cursorY += 4;
}

function drawCodeBlock(state: PdfState, code: string) {
  const { doc, margin, innerW } = state;
  doc.setFont("courier", "normal");
  doc.setFontSize(9);
  const codeLines = normalizeForPdf(code)
    .split("\n")
    .flatMap((line) => splitText(doc, line || " ", innerW - 24));
  const leading = 12;
  let i = 0;
  while (i < codeLines.length) {
    const availableLines = Math.max(1, Math.floor((state.bottomY - state.cursorY - 18) / leading));
    if (state.cursorY > margin && availableLines <= 1) {
      doc.addPage();
      state.cursorY = margin;
    }
    const chunk = codeLines.slice(i, i + availableLines);
    const boxH = chunk.length * leading + 16;
    ensureSpace(state, boxH);
    setFillColor(doc, COLORS.codeBg);
    setDrawColor(doc, COLORS.rule);
    doc.roundedRect(margin, state.cursorY, innerW, boxH, 4, 4, "FD");
    doc.setFont("courier", "normal");
    doc.setFontSize(9);
    setTextColor(doc, COLORS.text);
    let y = state.cursorY + 12;
    for (const line of chunk) {
      doc.text(line, margin + 10, y);
      y += leading;
    }
    state.cursorY += boxH + 8;
    i += chunk.length;
  }
}

function drawTableAsText(state: PdfState, rows: string[]) {
  const parsed = rows.map((row) =>
    row
      .trim()
      .replace(/^\||\|$/g, "")
      .split("|")
      .map((cell) => stripMarkdownInline(cell.trim())),
  );
  const widths = parsed.reduce<number[]>((acc, row) => {
    row.forEach((cell, i) => {
      acc[i] = Math.max(acc[i] ?? 0, Math.min(cell.length, 28));
    });
    return acc;
  }, []);
  const lines = parsed
    .filter((row) => !row.every((cell) => /^:?-{3,}:?$/.test(cell)))
    .map((row) =>
      row
        .map((cell, i) => cell.padEnd(widths[i] ?? 0))
        .join("  "),
    );
  drawCodeBlock(state, lines.join("\n"));
}

function drawMarkdown(state: PdfState, markdown: string) {
  const lines = normalizeForPdf(markdown).split("\n");
  let paragraph: string[] = [];
  let code: string[] | null = null;
  let table: string[] = [];

  function flushParagraph() {
    if (paragraph.length === 0) return;
    drawParagraph(state, paragraph.join(" "));
    paragraph = [];
  }

  function flushTable() {
    if (table.length === 0) return;
    flushParagraph();
    drawTableAsText(state, table);
    table = [];
  }

  for (const rawLine of lines) {
    const line = rawLine.replace(/\s+$/g, "");
    if (line.trim().startsWith("```")) {
      if (code) {
        drawCodeBlock(state, code.join("\n"));
        code = null;
      } else {
        flushTable();
        flushParagraph();
        code = [];
      }
      continue;
    }
    if (code) {
      code.push(line);
      continue;
    }

    if (/^\s*\|.+\|\s*$/.test(line)) {
      flushParagraph();
      table.push(line);
      continue;
    }
    flushTable();

    if (!line.trim()) {
      flushParagraph();
      state.cursorY += 2;
      continue;
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      flushParagraph();
      const level = heading[1]!.length;
      const text = stripMarkdownInline(heading[2]!);
      state.cursorY += level === 1 ? 6 : 4;
      drawWrappedText(state, text, {
        fontSize: level === 1 ? 16 : level === 2 ? 14 : 12.5,
        fontStyle: "bold",
        leading: level === 1 ? 20 : 17,
      });
      state.cursorY += 4;
      continue;
    }

    const bullet = /^\s*[-*]\s+(.+)$/.exec(line);
    if (bullet) {
      flushParagraph();
      const x = state.margin + 14;
      ensureSpace(state, 15.5);
      state.doc.setFont("helvetica", "normal");
      state.doc.setFontSize(11);
      setTextColor(state.doc, COLORS.text);
      state.doc.text("-", state.margin + 2, state.cursorY);
      drawWrappedText(state, stripMarkdownInline(bullet[1]!), {
        x,
        width: state.innerW - 14,
        fontSize: 11,
        leading: 15.5,
      });
      continue;
    }

    const ordered = /^\s*(\d+)\.\s+(.+)$/.exec(line);
    if (ordered) {
      flushParagraph();
      const label = `${ordered[1]}.`;
      const x = state.margin + 20;
      ensureSpace(state, 15.5);
      state.doc.setFont("helvetica", "normal");
      state.doc.setFontSize(11);
      setTextColor(state.doc, COLORS.text);
      state.doc.text(label, state.margin + 2, state.cursorY);
      drawWrappedText(state, stripMarkdownInline(ordered[2]!), {
        x,
        width: state.innerW - 20,
        fontSize: 11,
        leading: 15.5,
      });
      continue;
    }

    paragraph.push(line.trim());
  }

  if (code) drawCodeBlock(state, code.join("\n"));
  flushTable();
  flushParagraph();
}

function drawHeader(state: PdfState, title: string, exportedAt: Date) {
  const { doc, margin, innerW } = state;
  doc.setFont("helvetica", "bold");
  doc.setFontSize(9);
  setTextColor(doc, COLORS.brand);
  doc.text("FLOWIQ CHAT EXPORT", margin, state.cursorY);
  state.cursorY += 22;

  drawWrappedText(state, title.trim() || "Conversation", {
    fontSize: 21,
    fontStyle: "bold",
    leading: 25,
  });

  drawWrappedText(state, `Exported on ${formatExportedAt(exportedAt)}`, {
    fontSize: 11,
    color: COLORS.muted,
    leading: 15,
  });
  drawWrappedText(state, "Generated by FlowIQ by bluebot", {
    fontSize: 11,
    color: COLORS.muted,
    leading: 15,
  });
  state.cursorY += 10;
  drawRule(state);
  state.cursorY += 22;

  // Prevent a tiny first page when the title is long.
  if (state.cursorY > state.bottomY - 80) {
    doc.addPage();
    state.cursorY = margin;
  }
  state.innerW = innerW;
}

function drawRole(state: PdfState, role: Message["role"]) {
  ensureSpace(state, 36);
  const label = role === "user" ? "YOU" : "ASSISTANT";
  state.doc.setFont("helvetica", "bold");
  state.doc.setFontSize(9);
  setTextColor(state.doc, role === "user" ? COLORS.brand : [51, 65, 85]);
  state.doc.text(label, state.margin, state.cursorY);
  state.cursorY += 12;
  drawRule(state);
  state.cursorY += 14;
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("Could not read image"));
    reader.readAsDataURL(blob);
  });
}

function readImageSize(dataUrl: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve({ width: img.naturalWidth || img.width, height: img.naturalHeight || img.height });
    img.onerror = () => reject(new Error("Could not load image"));
    img.src = dataUrl;
  });
}

async function loadImageForPdf(src: string): Promise<PlotImageData> {
  const url = new URL(src, window.location.origin);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Plot unavailable (${res.status})`);
  const dataUrl = await blobToDataUrl(await res.blob());
  const size = await readImageSize(dataUrl);
  return { dataUrl, ...size };
}

async function drawPlot(state: PdfState, plot: PlotAttachment) {
  const caption = plotCaptionFromMeta(plot.src, plot.title) ?? "Analysis plot";
  try {
    const img = await loadImageForPdf(plot.src);
    const maxW = state.innerW;
    const maxH = state.bottomY - state.margin - 40;
    const scale = Math.min(maxW / img.width, maxH / img.height, 1);
    const w = img.width * scale;
    const h = img.height * scale;
    ensureSpace(state, h + 34);
    const x = state.margin + (state.innerW - w) / 2;
    state.doc.addImage(img.dataUrl, "PNG", x, state.cursorY, w, h, undefined, "FAST");
    state.cursorY += h + 10;
    drawWrappedText(state, caption, {
      fontSize: 9,
      color: COLORS.muted,
      leading: 12,
    });
    if (plot.plotTimezone && plot.plotType !== "flow_duration_curve") {
      drawWrappedText(state, `Time axes: ${plot.plotTimezone}`, {
        fontSize: 8,
        color: COLORS.muted,
        leading: 10,
      });
    }
    state.cursorY += 8;
  } catch (e) {
    drawWrappedText(state, `[Plot unavailable: ${caption}]`, {
      fontSize: 9,
      color: COLORS.muted,
      leading: 12,
    });
    if (e instanceof Error) {
      drawWrappedText(state, e.message, {
        fontSize: 8,
        color: COLORS.muted,
        leading: 10,
      });
    }
  }
}

function drawFooters(state: PdfState, title: string) {
  const footerTitle = (title.trim() || "Conversation").slice(0, 60);
  const totalPages = state.doc.getNumberOfPages();
  for (let pageNum = 1; pageNum <= totalPages; pageNum++) {
    state.doc.setPage(pageNum);
    state.doc.setFont("helvetica", "normal");
    state.doc.setFontSize(8);
    setTextColor(state.doc, COLORS.muted);
    state.doc.text(footerTitle, state.margin, state.pageH - state.margin + 10);
    state.doc.text(`Page ${pageNum} / ${totalPages}`, state.pageW - state.margin, state.pageH - state.margin + 10, {
      align: "right",
    });
  }
}

export async function exportTranscriptToPdf(
  options: ExportTranscriptToPdfOptions,
): Promise<void> {
  const { messages, title, onProgress } = options;
  const cleanMessages = stripTurnActivityBlocks(messages);
  const plotsByIndex = buildPlotsByIndex(cleanMessages);
  const doc = new jsPDF({ format: "a4", unit: "pt", orientation: "portrait" });
  const pageW = doc.internal.pageSize.getWidth();
  const pageH = doc.internal.pageSize.getHeight();
  const margin = 36;
  const state: PdfState = {
    doc,
    pageW,
    pageH,
    margin,
    innerW: pageW - 2 * margin,
    bottomY: pageH - margin - 20,
    cursorY: margin,
  };

  onProgress?.("Preparing...");
  drawHeader(state, title, new Date());

  for (let i = 0; i < cleanMessages.length; i++) {
    const msg = cleanMessages[i]!;
    const text = messageText(msg.content).trim();
    const plots = plotsByIndex.get(i) ?? [];
    if (!text && plots.length === 0) continue;

    onProgress?.(`Rendering ${msg.role === "user" ? "You" : "Assistant"}`);
    drawRole(state, msg.role);
    if (text) {
      drawMarkdown(state, text);
    }
    for (const plot of plots) {
      await drawPlot(state, plot);
    }
    state.cursorY += 12;
  }

  drawFooters(state, title);
  onProgress?.("Done");
  doc.save(`${sanitizeFileName(title)}.pdf`);
}
