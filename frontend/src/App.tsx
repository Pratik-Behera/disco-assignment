import { useEffect, useRef, useState, type ReactNode } from "react";
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
  campaign: "Drafting the campaign plan…",
};

function stageForResume(field: string | undefined, skip: boolean): string {
  if (field === "product") return "understand";
  if (field === "target_audience" && !skip) return "creatives";
  return "campaign";
}

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
    const isRevision = !awaiting && Boolean(threadId);
    const lastMsg = messages[messages.length - 1];
    const lastField = lastMsg?.role === "assistant" ? lastMsg.questionMeta?.field : undefined;
    setStage(isResume ? stageForResume(lastField, skip) : isRevision ? "campaign" : "understand");
    setAwaiting(false);
    setMessages((prev) => [...prev, { id: nextId(), role: "user", text: skip ? "Skip" : query }]);
    try {
      await streamRun(
        isResume ? "" : query,
        isResume || isRevision ? threadId : undefined,
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
          onDone: (reply: ChatReply) => {
            setThreadId(reply.thread_id);
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
  const streaming = last?.role === "assistant" && Boolean(last.streaming);
  const showDots = busy && !streaming;
  const budgetAsk =
    awaiting && last?.role === "assistant" && last.questionMeta?.field === "total_budget_usd";

  return (
    <div className="flex h-screen flex-col bg-[#212121] text-[#ececec]">
      <header className="flex h-14 shrink-0 items-center justify-center border-b border-white/5">
        <p className="text-lg font-semibold tracking-tight">Campaign builder</p>
      </header>

      <div className="flex-1 overflow-y-auto">
        {empty && (
          <div className="mx-auto flex min-h-full max-w-2xl flex-col items-center justify-center px-4 py-16 text-center">
            <h1 className="text-3xl font-semibold tracking-tight">Where should this product run?</h1>
            <p className="mt-3 max-w-md text-sm text-[#b4b4b4]">
              Describe the product in one sentence. I’ll recommend publishers, shoppers, ads, and a
              draft campaign.
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
            placeholder={
              awaiting
                ? budgetAsk
                  ? "Or type an amount, e.g. 200"
                  : "Answer in your own words…"
                : "Describe the product…"
            }
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
  const meta = message.questionMeta;
  return (
    <div className="fade-in mb-6 max-w-[90%] text-sm leading-7 text-[#ececec]">
      {message.kind === "ads" ? (
        <AdsBlock text={message.text} streaming={message.streaming} />
      ) : message.kind === "personas" ? (
        <PersonasBlock text={message.text} streaming={message.streaming} />
      ) : (
        <RichText text={message.text} streaming={message.streaming} />
      )}
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

function inlineMarkdown(text: string): ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={i} className="font-semibold text-white">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

function RichText({ text, streaming }: { text: string; streaming?: boolean }) {
  const lines = text.split("\n");
  return (
    <div>
      {lines.map((line, i) => {
        const last = i === lines.length - 1;
        const caret = last && streaming ? <span className="caret" aria-hidden /> : null;
        if (!line) {
          return <div key={i} className="h-2" />;
        }
        const wrapped = /^\*\*[^*]+\*\*$/.test(line);
        if (wrapped) {
          return (
            <p key={i} className={`text-[15px] font-semibold leading-6 text-white${i === 0 ? "" : " mt-4"}`}>
              {line.slice(2, -2)}
              {caret}
            </p>
          );
        }
        const bullet = line.startsWith("• ") || line.startsWith("- ");
        const muted =
          line.startsWith("I left the rest") ||
          line.startsWith("This catalog doesn’t") ||
          line.startsWith("This catalog doesn't");
        const extra = bullet
          ? " pl-3 text-[#d5d5d5]"
          : muted
            ? " mt-3 text-[#b4b4b4]"
            : /\s{2,}\d+%/.test(line)
              ? " font-mono text-[13px] whitespace-pre text-[#d5d5d5]"
              : "";
        return (
          <p key={i} className={`m-0${extra}`}>
            {inlineMarkdown(bullet ? line.slice(2) : line)}
            {caret}
          </p>
        );
      })}
    </div>
  );
}

type PersonaTile = { name: string; who: string; why: string };

function parsePersonas(text: string): PersonaTile[] {
  return text
    .split(/\n\n+/)
    .map((block) => block.split("\n").map((line) => line.trim()).filter(Boolean))
    .filter((lines) => lines.length > 0)
    .map((lines) => {
      if (lines.length >= 3) {
        return { name: lines[0], who: lines[1], why: lines.slice(2).join(" ") };
      }
      return { name: lines[0] ?? "", who: "", why: lines.slice(1).join(" ") };
    });
}

function SectionHeading({ children }: { children: string }) {
  return <p className="mb-3 text-[15px] font-semibold leading-6 text-white">{children}</p>;
}

function PersonasBlock({ text, streaming }: { text: string; streaming?: boolean }) {
  const tiles = parsePersonas(text);
  return (
    <div>
      <SectionHeading>Shoppers I’d write for</SectionHeading>
      <div className="grid gap-3 sm:grid-cols-2">
        {tiles.map((item, i) => {
          const last = i === tiles.length - 1;
          return (
            <div
              key={`${item.name}-${i}`}
              className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3"
            >
              <p className="text-sm font-medium text-white">{item.name}</p>
              {item.who ? <p className="mt-0.5 text-xs leading-5 text-[#d5d5d5]">{item.who}</p> : null}
              {item.why ? <p className="mt-1 text-xs leading-5 text-[#8f8f8f]">{item.why}</p> : null}
              {last && streaming ? <span className="caret" aria-hidden /> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

type AdVariant = { who: string; why: string; headline: string; body: string; cta: string };

function parseAds(text: string): AdVariant[] {
  return text
    .split(/\n\n+/)
    .map((block) => block.split("\n").map((line) => line.trim()).filter(Boolean))
    .filter((lines) => lines.length > 0)
    .map((lines) => {
      const strip = (s: string) => s.replace(/^\*\*|\*\*$/g, "");
      if (lines.length >= 5) {
        return {
          who: lines[0],
          why: lines[1],
          headline: strip(lines[2]),
          body: lines[3],
          cta: lines[4],
        };
      }
      if (lines.length === 4) {
        return {
          who: lines[0],
          why: "",
          headline: strip(lines[1]),
          body: lines[2],
          cta: lines[3],
        };
      }
      if (lines.length === 3) {
        return {
          who: "",
          why: "",
          headline: strip(lines[0]),
          body: lines[1],
          cta: lines[2],
        };
      }
      return {
        who: "",
        why: "",
        headline: strip(lines[0] ?? ""),
        body: lines[1] ?? "",
        cta: lines[2] ?? "",
      };
    });
}

function AdsBlock({ text, streaming }: { text: string; streaming?: boolean }) {
  const variants = parseAds(text);
  return (
    <div>
      <SectionHeading>Ad creatives</SectionHeading>
      <div className="grid gap-3 sm:grid-cols-2">
      {variants.map((item, i) => {
        const last = i === variants.length - 1;
        return (
          <div
            key={`${item.headline}-${i}`}
            className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3"
          >
            {item.who ? (
              <p className="mb-2 inline-flex rounded-full bg-white/10 px-2.5 py-0.5 text-[11px] text-[#d5d5d5]">
                {item.who}
              </p>
            ) : null}
            {item.why ? <p className="mb-2 text-xs leading-5 text-[#8f8f8f]">{item.why}</p> : null}
            <p className="text-[15px] font-semibold leading-6 text-white">{item.headline}</p>
            {item.body ? <p className="mt-1 text-sm leading-6 text-[#d5d5d5]">{item.body}</p> : null}
            {item.cta ? (
              <p className="mt-3 inline-flex rounded-full border border-white/15 px-2.5 py-0.5 text-[11px] text-[#d5d5d5]">
                {item.cta}
              </p>
            ) : null}
            {last && streaming ? <span className="caret" aria-hidden /> : null}
          </div>
        );
      })}
      </div>
    </div>
  );
}
