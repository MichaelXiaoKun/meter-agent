export interface Conversation {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
}

export interface ContentBlock {
  type: string;
  text?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
  tool_use_id?: string;
  content?: string;
}

export interface Message {
  role: "user" | "assistant";
  content: string | ContentBlock[];
}

export interface SSEEvent {
  type:
    | "text_delta"
    | "tool_call"
    | "tool_result"
    | "thinking"
    | "token_usage"
    | "compressing"
    | "done"
    | "error";
  text?: string;
  tool?: string;
  input?: Record<string, unknown>;
  success?: boolean;
  plot_paths?: string[];
  tokens?: number;
  pct?: number;
  error?: string;
}
