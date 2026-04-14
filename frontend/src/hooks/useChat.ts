import { useCallback, useEffect, useRef, useState } from "react";
import type { Message, SSEEvent } from "../types";
import * as api from "../api";

function isAbortOrUnload(err: unknown): boolean {
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (err instanceof TypeError && /load failed/i.test(err.message)) return true;
  return false;
}

export type AgentStatus =
  | { kind: "idle" }
  | { kind: "thinking" }
  | { kind: "streaming" }
  | { kind: "tool_call"; tool: string }
  | { kind: "tool_result"; tool: string; success: boolean }
  | { kind: "compressing" }
  | { kind: "error"; error: string };

interface StreamState {
  status: AgentStatus;
  text: string;
  plots: string[];
  tokenUsage: { tokens: number; pct: number };
  messages: Message[];
}

const IDLE: AgentStatus = { kind: "idle" };

export function useChat(activeConvId: string | null, token: string) {
  const [viewedMessages, setViewedMessages] = useState<Message[]>([]);
  const [processingConvId, setProcessingConvId] = useState<string | null>(null);
  const [serverProcessing, setServerProcessing] = useState(false);
  const [streamStatus, setStreamStatus] = useState<AgentStatus>(IDLE);
  const [streamText, setStreamText] = useState("");
  const [streamPlots, setStreamPlots] = useState<string[]>([]);
  const [streamTokenUsage, setStreamTokenUsage] = useState({ tokens: 0, pct: 0 });
  const processingMsgs = useRef<Message[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const activeConvRef = useRef(activeConvId);
  const accumulatedRef = useRef("");
  const plotsRef = useRef<string[]>([]);

  useEffect(() => {
    activeConvRef.current = activeConvId;
  }, [activeConvId]);

  // Load messages when the viewed conversation changes
  useEffect(() => {
    if (!activeConvId) {
      setViewedMessages([]);
      setServerProcessing(false);
      return;
    }

    if (activeConvId === processingConvId) {
      setViewedMessages(processingMsgs.current);
      setServerProcessing(false);
      return;
    }

    const ac = new AbortController();
    setViewedMessages([]);
    setServerProcessing(false);

    api.loadMessages(activeConvId, ac.signal).then(async (msgs) => {
      setViewedMessages(msgs);
      // If the last message is from the user, ask the server if it's still processing
      const last = msgs[msgs.length - 1];
      if (last?.role === "user" && typeof last.content === "string") {
        try {
          const active = await api.checkProcessing(activeConvId, ac.signal);
          setServerProcessing(active);
        } catch {
          // ignore — if the check fails, just don't show the banner
        }
      }
    }).catch((err) => {
      if (!isAbortOrUnload(err))
        console.error("Failed to load messages:", err);
    });

    return () => ac.abort();
  }, [activeConvId, processingConvId]);

  // Poll for completion while the server confirms it's actively processing
  useEffect(() => {
    if (!serverProcessing || !activeConvId || processingConvId) return;

    const convId = activeConvId;
    const ac = new AbortController();

    const interval = setInterval(async () => {
      try {
        const still = await api.checkProcessing(convId, ac.signal);
        if (!still) {
          clearInterval(interval);
          const msgs = await api.loadMessages(convId, ac.signal);
          if (activeConvRef.current === convId) {
            setViewedMessages(msgs);
          }
          setServerProcessing(false);
        }
      } catch {
        // ignore
      }
    }, 5000);

    return () => {
      ac.abort();
      clearInterval(interval);
    };
  }, [serverProcessing, activeConvId, processingConvId]);

  const sendMessage = useCallback(
    async (text: string, convIdOverride?: string) => {
      const convId = convIdOverride ?? activeConvId;
      if (!convId || !token || !text.trim()) return;

      if (processingConvId && processingConvId !== convId) {
        abortRef.current?.abort();
      }

      setProcessingConvId(convId);

      const userMsg: Message = { role: "user", content: text };
      const updatedMsgs = [...viewedMessages, userMsg];
      processingMsgs.current = updatedMsgs;
      setViewedMessages(updatedMsgs);

      setStreamStatus({ kind: "thinking" });
      setStreamText("");
      setStreamPlots([]);
      accumulatedRef.current = "";
      plotsRef.current = [];

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        await api.streamChat(
          convId,
          text,
          token,
          (event: SSEEvent) => {
            switch (event.type) {
              case "thinking":
                accumulatedRef.current = "";
                setStreamStatus({ kind: "thinking" });
                setStreamText("");
                break;
              case "text_delta":
                accumulatedRef.current += event.text ?? "";
                setStreamStatus({ kind: "streaming" });
                setStreamText(accumulatedRef.current);
                break;
              case "tool_call":
                setStreamStatus({ kind: "tool_call", tool: event.tool ?? "" });
                break;
              case "tool_result":
                setStreamStatus({
                  kind: "tool_result",
                  tool: event.tool ?? "",
                  success: event.success ?? false,
                });
                if (event.plot_paths?.length) {
                  plotsRef.current = [...plotsRef.current, ...event.plot_paths];
                  setStreamPlots([...plotsRef.current]);
                }
                break;
              case "token_usage":
                setStreamTokenUsage({
                  tokens: event.tokens ?? 0,
                  pct: event.pct ?? 0,
                });
                break;
              case "compressing":
                setStreamStatus({ kind: "compressing" });
                break;
              case "error":
                setStreamStatus({
                  kind: "error",
                  error: event.error ?? "Unknown error",
                });
                break;
              case "done":
                break;
            }
          },
          controller.signal
        );

        // Stream finished — load final persisted messages
        const final = await api.loadMessages(convId);
        processingMsgs.current = final;
        if (activeConvRef.current === convId) {
          setViewedMessages(final);
        }
        setStreamStatus(IDLE);
        setStreamText("");
        setStreamPlots([]);
        setProcessingConvId(null);
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setStreamStatus({
            kind: "error",
            error: (err as Error).message,
          });
        }
        setProcessingConvId(null);
      } finally {
        abortRef.current = null;
      }
    },
    [activeConvId, token, processingConvId, viewedMessages]
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setStreamStatus(IDLE);
    setStreamText("");
    setStreamPlots([]);
    setProcessingConvId(null);
  }, []);

  const isViewingProcessing =
    !!processingConvId && activeConvId === processingConvId;

  return {
    messages: viewedMessages,
    status: isViewingProcessing ? streamStatus : IDLE,
    streamingText: isViewingProcessing ? streamText : "",
    tokenUsage: isViewingProcessing ? streamTokenUsage : { tokens: 0, pct: 0 },
    pendingPlots: isViewingProcessing ? streamPlots : [],
    processingConvId,
    serverProcessing,
    sendMessage,
    cancel,
  };
}
