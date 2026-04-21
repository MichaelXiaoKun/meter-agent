import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message, ContentBlock } from "../types";
import PlotImage from "./PlotImage";

function extractText(content: string | ContentBlock[]): string {
  if (typeof content === "string") return content;
  return content
    .filter((b) => b.type === "text" && b.text)
    .map((b) => b.text!)
    .join("");
}

function isToolResultRow(content: string | ContentBlock[]): boolean {
  if (typeof content === "string") return false;
  return content.length > 0 && content[0]?.type === "tool_result";
}

function rewritePlotPaths(text: string): string {
  return text.replace(
    /!\[([^\]]*)\]\(([^)]*\.png)\)/g,
    (_match, alt, src) => {
      const filename = src.split("/").pop() ?? src;
      return `![${alt}](/api/plots/${filename})`;
    }
  );
}

/** Remove markdown image lines so we do not double-render or hit wrong URLs from the LLM. */
function stripMarkdownPngImages(text: string): string {
  return text
    .replace(/!\[[^\]]*\]\([^)]*\.png\)/gi, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function extractPlotPaths(content: string | ContentBlock[]): string[] {
  if (typeof content === "string") return [];
  const paths: string[] = [];
  for (const block of content) {
    if (block.type !== "tool_result" || !block.content) continue;
    try {
      const result = JSON.parse(block.content);
      for (const p of result.plot_paths ?? []) {
        const filename = (p as string).split("/").pop() ?? p;
        paths.push(`/api/plots/${filename}`);
      }
    } catch {
      // skip
    }
  }
  return paths;
}

interface MessageBubbleProps {
  message: Message;
  plotPaths?: string[];
}

export default function MessageBubble({ message, plotPaths }: MessageBubbleProps) {
  if (isToolResultRow(message.content)) return null;

  const rawText = extractText(message.content);
  const trimmed = rawText.trim();
  const isUser = message.role === "user";
  const hasPlots = !!(plotPaths && plotPaths.length > 0);

  // User bubbles need text. Assistant bubbles need text and/or plots (tool-only turns can be
  // empty here; final reply may have plots attached while markdown is briefly empty).
  if (isUser && !trimmed) return null;
  if (!isUser && !trimmed && !hasPlots) return null;

  // When tool_result provides plot_paths, render images only from those URLs — not from
  // markdown (the model often echoes a different timestamp than int(timestamps[0]) in filenames).
  const text = isUser
    ? rawText
    : !hasPlots
      ? rewritePlotPaths(rawText)
      : trimmed
        ? stripMarkdownPngImages(rawText)
        : "";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        /*
          ``min-w-0`` lets this flex child actually honour its ``max-w-*``
          when it contains unbreakable strings (long serials, UUIDs, URLs).
          ``overflow-hidden`` is a hard backstop — even if an inner element
          refuses to wrap, it is clipped to the bubble's rounded border
          rather than bleeding outside the white frame.
        */
        className={`max-w-[min(92%,28rem)] min-w-0 overflow-hidden rounded-2xl px-4 py-3.5 sm:max-w-[75%] sm:py-3 ${
          isUser
            ? "bg-brand-700 text-white"
            : "border border-brand-border bg-white text-brand-900"
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap break-words text-base leading-relaxed sm:text-sm">{text}</p>
        ) : (
          <>
            {text ? (
              /*
                Everything below is scoped to the assistant bubble so model
                output — which can include markdown tables, fenced code
                blocks, bare UUIDs, and long URLs — never overflows the
                white frame:
                  ``break-words`` + ``prose-a:break-words`` wraps long words
                  and links between characters when there is no space.
                  ``[&_pre]:overflow-x-auto`` + ``whitespace-pre-wrap``
                  keeps fenced code within the bubble (wraps, and scrolls
                  horizontally if a token really cannot break).
                  ``[&_table]:block [&_table]:overflow-x-auto`` turns wide
                  markdown tables into a horizontally scrollable region
                  instead of letting them stretch the bubble's width.
                  ``[&_img]:max-w-full`` is belt-and-braces for any raw
                  ``<img>`` the model inlines.
              */
              <div className="prose prose-base max-w-none break-words prose-p:my-2 prose-p:leading-relaxed prose-headings:text-brand-900 prose-a:break-words prose-a:text-brand-500 prose-img:rounded-lg prose-img:shadow-sm prose-th:text-left prose-table:text-sm [&_pre]:overflow-x-auto [&_pre]:whitespace-pre-wrap [&_pre]:break-words [&_table]:block [&_table]:overflow-x-auto [&_img]:max-w-full sm:prose-sm sm:prose-p:my-1">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    img: ({ src, alt }) => (
                      <PlotImage
                        src={src ?? ""}
                        alt={alt ?? undefined}
                        className="w-full rounded-lg shadow-sm"
                      />
                    ),
                  }}
                >
                  {text}
                </ReactMarkdown>
              </div>
            ) : null}
            {plotPaths?.map((src) => (
              <PlotImage
                key={src}
                src={src}
                alt="Flow analysis plot"
                className={`w-full rounded-lg shadow-sm ${text ? "mt-3" : ""}`}
              />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

export { extractPlotPaths };
