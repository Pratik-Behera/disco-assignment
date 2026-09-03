import type { ChatReply } from "./types";

export async function fetchExamples(): Promise<string[]> {
  const res = await fetch("/api/examples");
  const body = await res.json();
  return Array.isArray(body.examples) ? (body.examples as string[]) : [];
}

type StreamHandlers = {
  onStage: (stage: string) => void;
  onToken: (text: string) => void;
  onClarify: (reply: ChatReply) => void;
  onDone: (reply: ChatReply) => void;
};

export async function streamRun(
  rawInput: string,
  threadId: string | undefined,
  resume: string | undefined,
  handlers: StreamHandlers,
): Promise<void> {
  const res = await fetch("/api/run/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw_input: rawInput, thread_id: threadId, resume }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || res.statusText);
  }
  if (!res.body) throw new Error("No stream");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const block of parts) {
        let event = "";
        let data = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event: ")) event = line.slice(7);
          if (line.startsWith("data: ")) data = line.slice(6);
        }
        if (!data) continue;
        const parsed = JSON.parse(data);
        if (event === "stage") handlers.onStage(parsed.stage);
        if (event === "token") handlers.onToken(parsed.text);
        if (event === "clarify") {
          handlers.onClarify({ thread_id: parsed.thread_id, text: "", question: parsed.question });
        }
        if (event === "done") {
          handlers.onDone({ thread_id: parsed.thread_id, text: parsed.text });
        }
        if (event === "error") throw new Error(parsed.detail);
      }
    }
  } finally {
    // Throwing mid-stream (error event, bad JSON) would otherwise leave the body open.
    void reader.cancel().catch(() => {});
  }
}
