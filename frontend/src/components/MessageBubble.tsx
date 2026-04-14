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
  if (!rawText) return null;

  const isUser = message.role === "user";
  const text = isUser ? rawText : rewritePlotPaths(rawText);

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[75%] rounded-2xl px-4 py-3 ${
          isUser
            ? "bg-brand-700 text-white"
            : "border border-brand-border bg-white text-brand-900"
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap text-sm">{text}</p>
        ) : (
          <>
            <div className="prose prose-sm max-w-none prose-p:my-1 prose-headings:text-brand-900 prose-a:text-brand-500 prose-img:rounded-lg prose-img:shadow-sm prose-th:text-left prose-table:text-sm">
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
            {plotPaths?.map((src) => (
              <PlotImage
                key={src}
                src={src}
                alt="Flow analysis plot"
                className="mt-3 w-full rounded-lg shadow-sm"
              />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

export { extractPlotPaths };
