import { useState, useRef, useEffect, useCallback } from "react";
import { MessageCircle, X, Send, Loader2, ExternalLink } from "lucide-react";
import { askQuestion } from "../lib/api";

// Keep in sync with HISTORY_LIMIT in backend/app/api/ask.py
const HISTORY_LIMIT = 6;

// ── Error message helper ───────────────────────────────────────────────────

function errorText(err) {
  const msg = err?.message ?? "";
  if (msg.includes("429")) return "You've sent too many questions. Please wait a moment and try again.";
  if (msg.includes("500") || msg.includes("502") || msg.includes("503"))
    return "The knowledge base is temporarily unavailable. Please try again shortly.";
  if (msg.includes("Failed to fetch") || msg.includes("NetworkError"))
    return "Network error — check your connection and try again.";
  return "Something went wrong. Please try again.";
}

// ── Individual message bubble ─────────────────────────────────────────────

function MessageBubble({ msg }) {
  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-tr-sm px-4 py-2.5 bg-cosmos-600 text-white text-sm leading-relaxed">
          {msg.text}
        </div>
      </div>
    );
  }

  if (msg.role === "error") {
    return (
      <div className="flex justify-start">
        <div className="max-w-[90%] rounded-2xl rounded-tl-sm px-4 py-2.5 bg-red-500/15 border border-red-500/25 text-red-300 text-sm leading-relaxed">
          {msg.text}
        </div>
      </div>
    );
  }

  // assistant
  return (
    <div className="flex justify-start">
      <div className="max-w-[90%] space-y-2">
        <div className="rounded-2xl rounded-tl-sm px-4 py-2.5 bg-white/[0.06] border border-white/[0.08] text-white/85 text-sm leading-relaxed whitespace-pre-wrap">
          {msg.text}
        </div>
        {msg.sources?.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-1">
            {msg.sources.map((s, i) => {
              const label = s.class_key
                ? `${s.source} — ${s.class_key}`
                : s.source;
              return (
                <span
                  key={i}
                  className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-white/[0.04] border border-white/[0.08] text-white/35"
                >
                  {label}
                </span>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main widget ───────────────────────────────────────────────────────────

export default function AskWidget() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      text: "Hi! I'm the Rubin Scout knowledge assistant. Ask me anything about transient astronomy — supernovae, kilonovae, TDEs, how the pipeline works, or anything in the docs.",
      sources: [],
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [panelHeight, setPanelHeight] = useState(null); // for mobile keyboard avoidance

  const listRef = useRef(null);
  const inputRef = useRef(null);
  const isMobile = () => window.innerWidth < 640;

  // Scroll to bottom on new messages
  useEffect(() => {
    if (open && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages, open, loading]);

  // Focus input when opened
  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  // visualViewport — keep panel above keyboard on mobile
  useEffect(() => {
    if (!open || !isMobile()) return;

    const vv = window.visualViewport;
    if (!vv) return;

    const handler = () => {
      // visualViewport.height shrinks when the keyboard opens
      setPanelHeight(vv.height - 16); // 8px margin top + bottom
    };

    vv.addEventListener("resize", handler);
    vv.addEventListener("scroll", handler);
    handler(); // set initial

    return () => {
      vv.removeEventListener("resize", handler);
      vv.removeEventListener("scroll", handler);
      setPanelHeight(null);
    };
  }, [open]);

  const handleSubmit = useCallback(
    async (e) => {
      e?.preventDefault();
      const q = input.trim();
      if (!q || loading) return;

      // Build history from real exchange turns only:
      // - skip the hardcoded welcome message (index 0, assistant, no prior user turn)
      // - skip role:"error" (UI-only, not real conversation context)
      // - cap to last HISTORY_LIMIT turns to match the backend cap
      const history = messages
        .filter((m) => m.role === "user" || m.role === "assistant")
        .slice(1) // drop the canned welcome greeting (first message)
        .slice(-HISTORY_LIMIT)
        .map((m) => ({ role: m.role, content: m.text }));

      setInput("");
      setMessages((prev) => [...prev, { role: "user", text: q }]);
      setLoading(true);

      try {
        const data = await askQuestion(q, history);
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            text: data.answer,
            sources: data.sources ?? [],
          },
        ]);
      } catch (err) {
        setMessages((prev) => [
          ...prev,
          { role: "error", text: errorText(err) },
        ]);
      } finally {
        setLoading(false);
      }
    },
    [input, loading, messages]
  );

  // ── Panel style — dynamic height on mobile when keyboard is open ─────────
  const panelStyle = panelHeight != null ? { height: `${panelHeight}px` } : {};

  return (
    <>
      {/* ── Floating toggle button ─────────────────────────────────────── */}
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "Close knowledge assistant" : "Open knowledge assistant"}
        style={{ paddingBottom: "max(12px, env(safe-area-inset-bottom))" }}
        className={`
          fixed bottom-4 right-4 z-50
          w-14 h-14 rounded-full
          flex items-center justify-center
          shadow-lg shadow-cosmos-900/60
          transition-all duration-200
          ${open
            ? "bg-white/10 border border-white/20 text-white/60 hover:bg-white/15"
            : "bg-cosmos-600 hover:bg-cosmos-500 text-white"
          }
        `}
      >
        {open ? <X className="w-5 h-5" /> : <MessageCircle className="w-5 h-5" />}
      </button>

      {/* ── Panel ──────────────────────────────────────────────────────── */}
      {open && (
        <div
          style={panelStyle}
          className={`
            fixed z-40
            bg-cosmos-950 border border-white/[0.08]
            flex flex-col shadow-2xl shadow-black/60
            /* mobile: bottom sheet — full width, ~88vh */
            bottom-0 left-0 right-0 rounded-t-2xl
            h-[88svh]
            /* desktop 640px+: docked panel above button, capped to viewport */
            sm:bottom-20 sm:right-4 sm:left-auto sm:rounded-2xl
            sm:w-[360px] sm:h-[520px] sm:max-h-[calc(100svh-6rem)]
          `}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.07] shrink-0">
            <div className="flex items-center gap-2.5">
              <div className="w-7 h-7 rounded-lg bg-cosmos-600/30 border border-cosmos-500/30 flex items-center justify-center">
                <MessageCircle className="w-3.5 h-3.5 text-cosmos-400" />
              </div>
              <div>
                <p className="text-sm font-medium text-white/85 leading-none">Ask Rubin Scout</p>
                <p className="text-[10px] text-white/30 mt-0.5">Knowledge assistant</p>
              </div>
            </div>
            <button
              onClick={() => setOpen(false)}
              aria-label="Close"
              className="w-11 h-11 rounded-lg flex items-center justify-center text-white/40 hover:text-white/70 hover:bg-white/[0.06] transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Message list */}
          <div
            ref={listRef}
            className="flex-1 overflow-y-auto px-4 py-4 space-y-4 min-h-0"
          >
            {messages.map((msg, i) => (
              <MessageBubble key={i} msg={msg} />
            ))}

            {loading && (
              <div className="flex justify-start">
                <div className="rounded-2xl rounded-tl-sm px-4 py-3 bg-white/[0.06] border border-white/[0.08]">
                  <Loader2 className="w-4 h-4 text-white/40 animate-spin" />
                </div>
              </div>
            )}
          </div>

          {/* Input row */}
          <form
            onSubmit={handleSubmit}
            className="shrink-0 flex items-end gap-2 px-3 py-3 border-t border-white/[0.07]"
            style={{ paddingBottom: "max(12px, env(safe-area-inset-bottom))" }}
          >
            <textarea
              ref={inputRef}
              rows={1}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                // auto-grow up to ~4 lines
                e.target.style.height = "auto";
                e.target.style.height = `${Math.min(e.target.scrollHeight, 96)}px`;
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSubmit();
                }
              }}
              maxLength={500}
              placeholder="Ask about supernovae, kilonovae…"
              disabled={loading}
              className="
                flex-1 resize-none rounded-xl
                bg-white/[0.05] border border-white/[0.1]
                focus:border-cosmos-500/50 focus:outline-none
                px-3 py-2.5 text-sm text-white/85 placeholder-white/25
                leading-relaxed transition-colors
                disabled:opacity-40
                min-h-[44px]
              "
            />
            <button
              type="submit"
              disabled={!input.trim() || loading}
              aria-label="Send"
              className="
                w-11 h-11 shrink-0 rounded-xl
                flex items-center justify-center
                bg-cosmos-600 hover:bg-cosmos-500
                text-white transition-colors
                disabled:opacity-30 disabled:pointer-events-none
              "
            >
              <Send className="w-4 h-4" />
            </button>
          </form>
        </div>
      )}

      {/* Mobile overlay backdrop */}
      {open && (
        <div
          className="fixed inset-0 z-30 bg-black/40 sm:hidden"
          onClick={() => setOpen(false)}
        />
      )}
    </>
  );
}
