import { useEffect, useRef, useState } from "react";
import { fetchExamples, streamRun } from "./api";
import type { ChatReply, QuestionMeta } from "./types";

type ChatMessage =
  | { id: string; role: "user"; text: string }
  | {
      id: string;
      role: "assistant";
      text: string;
      kind?: string;
      streaming?: boolean;
      question?: string;
      questionMeta?: QuestionMeta;
    }
  | { id: string; role: "error"; text: string };

const STAGE_COPY: Record<string, string> = {
  understand: "Reading your product…",
  publishers: "Finding the best publishers…",
  personas: "Finding shopper fits…",
  creatives: "Writing ad creatives…",
};

function nextId(): string {
  return crypto.randomUUID();
}

export default function App() {
  const [examples, setExamples] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [threadId, setThreadId] = useState<string>();
  const [awaiting, setAwaiting] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchExamples().then(setExamples).catch(() => setExamples([]));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy, stage]);

  async function onSubmit(text: string, skip = false) {
    const query = text.trim();
    if ((!query && !skip) || busy) return;
    setInput("");
    setBusy(true);
    const isResume = awaiting;
    setStage(isResume ? "creatives" : "understand");
    setAwaiting(false);
    setMessages((prev) => [...prev, { id: nextId(), role: "user", text: skip ? "Skip" : query }]);
    try {
      await streamRun(
        isResume ? "" : query,
        isResume ? threadId : undefined,
        isResume && !skip ? query : undefined,
        {
          onStage: setStage,
          onSection: (kind: string) => {
            setMessages((prev) => {
              const closed = prev.map((m) =>
                m.role === "assistant" && m.streaming ? { ...m, streaming: false } : m,
              );
              return [
                ...closed,
                { id: nextId(), role: "assistant", text: "", kind, streaming: true },
              ];
            });
          },
          onToken: (token) => {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last?.role === "assistant") {
                return [...prev.slice(0, -1), { ...last, text: last.text + token, streaming: true }];
              }
              return [...prev, { id: nextId(), role: "assistant", text: token, streaming: true }];
            });
          },
          onClarify: (reply: ChatReply) => {
            setThreadId(reply.thread_id);
            setAwaiting(true);
            setMessages((prev) => [
              ...prev.map((m) => (m.role === "assistant" && m.streaming ? { ...m, streaming: false } : m)),
              {
                id: nextId(),
                role: "assistant",
                text: reply.question ?? "",
                question: reply.question,
                questionMeta: reply.question_meta,
              },
            ]);
          },
          onDone: () => {
            setThreadId(undefined);
            setMessages((prev) =>
              prev.map((m) => (m.role === "assistant" && m.streaming ? { ...m, streaming: false } : m)),
            );
          },
        },
        skip,
      );
    } catch (err) {
      setMessages((prev) => [
        ...prev.map((m) => (m.role === "assistant" && m.streaming ? { ...m, streaming: false } : m)),
        { id: nextId(), role: "error", text: err instanceof Error ? err.message : "Run failed" },
      ]);
    } finally {
      setBusy(false);
      setStage(null);
    }
  }

  const empty = messages.length === 0 && !busy;
  const last = messages[messages.length - 1];
  const showDots = busy;

  return (
    <div className="flex h-screen flex-col bg-[#212121] text-[#ececec]">
      <header className="flex h-14 shrink-0 items-center justify-center border-b border-white/5">
        <p className="text-sm font-medium">Disco</p>
      </header>

      <div className="flex-1 overflow-y-auto">
        {empty && (
          <div className="mx-auto flex min-h-full max-w-2xl flex-col items-center justify-center px-4 py-16 text-center">
            <h1 className="text-3xl font-semibold tracking-tight">Where should this product run?</h1>
            <p className="mt-3 max-w-md text-sm text-[#b4b4b4]">
              Describe the product in one sentence. I’ll recommend publishers, shoppers, and a few ad
              angles.
            </p>
            <ul className="mt-10 grid w-full gap-2 sm:grid-cols-2">
              {examples.slice(0, 6).map((line) => (
                <li key={line}>
                  <button
                    type="button"
                    className="w-full rounded-2xl border border-white/10 px-4 py-3 text-left text-sm text-[#d5d5d5] hover:bg-white/5"
                    onClick={() => void onSubmit(line)}
                  >
                    {line}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {!empty && (
          <div className="mx-auto w-full max-w-2xl px-4 py-6">
            {messages.map((message) => (
              <MessageBubble
                key={message.id}
                message={message}
                interactive={
                  awaiting && !busy && message.role === "assistant" && message.id === last?.id
                }
                onPick={(label) => void onSubmit(label)}
                onSkip={() => void onSubmit("Skip", true)}
              />
            ))}
            {showDots && (
              <div className="mt-6 flex items-center gap-3 text-sm text-[#8f8f8f]" role="status" aria-live="polite">
                <span className="loading-dots" aria-hidden>
                  <span />
                  <span />
                  <span />
                </span>
                <span>{STAGE_COPY[stage ?? ""] ?? "Working…"}</span>
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      <div className="shrink-0 px-4 pb-6 pt-2">
        <form
          className="mx-auto flex max-w-2xl items-end gap-2 rounded-[28px] border border-white/10 bg-[#303030] px-4 py-2"
          onSubmit={(e) => {
            e.preventDefault();
            void onSubmit(input);
          }}
        >
          <textarea
            rows={1}
            className="max-h-40 min-h-10 flex-1 resize-none bg-transparent py-2 text-sm outline-none placeholder:text-[#8f8f8f]"
            aria-label={awaiting ? "Answer the question" : "Describe the product"}
            placeholder={awaiting ? "Answer in your own words…" : "Describe the product…"}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void onSubmit(input);
              }
            }}
          />
          <button
            type="submit"
            disabled={busy || !input.trim()}
            className="mb-1 flex h-8 w-8 items-center justify-center rounded-full bg-white text-black disabled:opacity-30"
            aria-label="Send"
          >
            ↑
          </button>
        </form>
      </div>
    </div>
  );
}

function MessageBubble({
  message,
  interactive,
  onPick,
  onSkip,
}: {
  message: ChatMessage;
  interactive?: boolean;
  onPick?: (label: string) => void;
  onSkip?: () => void;
}) {
  if (message.role === "user") {
    return (
      <div className="mb-6 flex justify-end">
        <div className="max-w-[85%] rounded-[22px] bg-[#323232] px-4 py-2.5 text-sm leading-6">{message.text}</div>
      </div>
    );
  }
  if (message.role === "error") {
    return <p className="mb-6 text-sm text-red-400">{message.text}</p>;
  }
  const lines = message.text.split("\n");
  const meta = message.questionMeta;
  return (
    <div className="fade-in mb-6 max-w-[90%] text-sm leading-5 text-[#ececec]">
      {lines.map((line, i) => {
        const last = i === lines.length - 1;
        const extra =
          line === "Near misses" || line === "Shoppers this fits" || line === "Ads" || line.startsWith("Remaining ") || line.startsWith("I left the rest")
            ? " mt-2 text-[#b4b4b4]"
            : line.startsWith("• ")
              ? " pl-2"
              : line.includes(" — ")
                ? " font-medium"
                : "";
        return (
          <div key={i} className={`m-0${extra}`}>
            {line}
            {last && message.streaming ? <span className="caret" aria-hidden /> : null}
          </div>
        );
      })}
      {interactive && meta && onPick && (
        <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Suggested answers">
          {meta.quick_replies.map((label) => (
            <button
              key={label}
              type="button"
              className="rounded-full border border-white/15 px-3 py-1.5 text-xs text-[#d5d5d5] hover:bg-white/10"
              onClick={() => onPick(label)}
            >
              {label}
            </button>
          ))}
          {meta.allow_skip && onSkip ? (
            <button
              type="button"
              className="rounded-full border border-white/10 px-3 py-1.5 text-xs text-[#8f8f8f] hover:bg-white/5"
              onClick={onSkip}
            >
              Skip
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}
