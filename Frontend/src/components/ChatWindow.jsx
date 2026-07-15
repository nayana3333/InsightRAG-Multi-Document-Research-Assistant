import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../auth-client";
import useAuth from "../useAuth";
import BrandMark from "./BrandMark";

export default function ChatWindow({ chatId, onMessageComplete, onOpenLibrary, onOpenDocuments }) {
  const { user, logout } = useAuth();
  const [input, setInput] = useState("");
  const [isThinking, setIsThinking] = useState(false);
  const [messages, setMessages] = useState([]);
  const [error, setError] = useState("");
  const [responseMeta, setResponseMeta] = useState(null);
  const messagesEndRef = useRef(null);
  const streamControllerRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isThinking]);

  useEffect(() => {
    streamControllerRef.current?.abort();
    setError("");
    setResponseMeta(null);
    if (!chatId) {
      setMessages([]);
      return;
    }

    const controller = new AbortController();
    apiFetch(`/chats/${chatId}/messages`, { signal: controller.signal })
      .then(async (response) => {
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || "Could not load this chat.");
        return result;
      })
      .then((data) => setMessages(data.messages || []))
      .catch((requestError) => {
        if (requestError.name !== "AbortError") setError(requestError.message);
      });
    return () => controller.abort();
  }, [chatId]);

  useEffect(() => () => streamControllerRef.current?.abort(), []);

  const handleSend = async (event) => {
    event.preventDefault();
    const question = input.trim();
    if (!question || !chatId || isThinking) return;

    const optimisticId = `optimistic-${Date.now()}`;
    const assistantId = `stream-${Date.now()}`;
    setMessages((current) => [
      ...current,
      { id: optimisticId, type: "human", text: question, sources: [] },
      { id: assistantId, type: "ai", text: "", sources: [] },
    ]);
    setInput("");
    setError("");
    setIsThinking(true);
    const streamController = new AbortController();
    streamControllerRef.current = streamController;

    try {
      const response = await apiFetch(`/chats/${chatId}/messages/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
        signal: streamController.signal,
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail || "The AI request failed.");
      }
      if (!response.body) throw new Error("Streaming is unavailable in this browser.");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";
        for (const block of events) {
          const line = block.split("\n").find((item) => item.startsWith("data: "));
          if (!line) continue;
          const eventData = JSON.parse(line.slice(6));
          if (eventData.type === "token") {
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantId
                  ? { ...message, text: message.text + eventData.token }
                  : message
              )
            );
          } else if (eventData.type === "sources") {
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantId
                  ? { ...message, sources: eventData.sources || [] }
                  : message
              )
            );
          } else if (eventData.type === "done") {
            setResponseMeta({ latencyMs: eventData.latencyMs, model: eventData.model });
          } else if (eventData.type === "error") {
            throw new Error(eventData.message || "Streaming failed.");
          }
        }
        if (done) break;
      }
      onMessageComplete?.();
    } catch (requestError) {
      setMessages((current) =>
        current.filter((message) => message.id !== optimisticId && message.id !== assistantId)
      );
      setError(requestError.name === "AbortError" ? "Generation stopped." : requestError.message);
    } finally {
      streamControllerRef.current = null;
      setIsThinking(false);
    }
  };

  const stopGeneration = () => streamControllerRef.current?.abort();

  return (
    <div className="h-full min-h-0 flex flex-col bg-[#0a0d12]">
      <header className="border-b border-white/[0.07] bg-[#0a0d12]/90 p-3 backdrop-blur-xl sm:p-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-2">
            <button type="button" onClick={onOpenLibrary} className="rounded-lg border border-gray-700 px-3 py-2 text-xs text-gray-300 lg:hidden" aria-label="Open knowledge base">Library</button>
            <BrandMark compact heading showIcon={false} />
          </div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={onOpenDocuments} className="rounded-lg border border-gray-700 bg-white/[0.03] px-3 py-2 text-xs text-gray-300 hover:bg-white/[0.06] xl:hidden" aria-label="Open sources">Sources</button>
            <span className="hidden text-right text-xs text-gray-400 sm:block">
              <strong className="block text-gray-200">{user?.name}</strong>
              {user?.email}
            </span>
            <button onClick={logout} className="rounded-lg border border-gray-700 px-3 py-2 text-xs text-gray-300 hover:bg-gray-800">
              Sign out
            </button>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3 sm:p-4" aria-live="polite">
        {!chatId && (
          <div className="h-full grid place-items-center text-center">
            <div className="max-w-sm">
              <div className="mx-auto mb-5 grid h-14 w-14 place-items-center rounded-2xl border border-gray-700 bg-white/[0.03] font-mono text-lg text-gray-300">
                [ ]
              </div>
              <h3 className="text-xl font-medium tracking-[-0.02em]">Start an evidence trail</h3>
              <p className="mt-2 text-sm text-gray-400">
                Add source PDFs or open a workspace. Every answer stays connected to the pages behind it.
              </p>
            </div>
          </div>
        )}

        {messages.map((message, index) => (
          <div
            key={message.id || `${message.type}-${index}`}
            className={`flex ${message.type === "human" ? "justify-end" : "justify-start"}`}
          >
            <div className="max-w-[92%] space-y-2 sm:max-w-[82%]">
              <div
                className={`p-3 rounded-2xl text-sm leading-6 shadow-md whitespace-pre-wrap ${
                  message.type === "human"
                    ? "bg-blue-600 text-white rounded-br-none"
                    : "border border-white/[0.06] bg-[#12161d] text-gray-200 rounded-bl-none"
                }`}
              >
                {message.text || "Retrieving evidence..."}
              </div>
              {message.type === "ai" && message.sources?.length > 0 && (
                <details className="rounded-xl border border-gray-800 bg-black/20 p-3 text-xs text-gray-300">
                  <summary className="cursor-pointer font-medium text-blue-300">
                    Evidence trail · {message.sources.length} sources
                  </summary>
                  <div className="mt-3 space-y-2">
                    {message.sources.map((source) => (
                      <div key={`${message.id || index}-${source.id}`} className="rounded-lg bg-[#181818] p-3">
                        <div className="mb-1 flex flex-wrap justify-between gap-1 text-blue-300">
                          <span>Source {source.id} · {source.fileName || "Document"} · Page {source.page}</span>
                          <span>{Math.round((source.relevance || 0) * 100)}% match</span>
                        </div>
                        <p className="line-clamp-3 text-gray-400">{source.snippet}</p>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </div>
          </div>
        ))}

        <div ref={messagesEndRef} />
      </div>

      <footer className="border-t border-gray-800 p-3 sm:p-4">
        {error && (
          <div role="alert" className="mb-3 rounded-lg border border-red-500/40 bg-red-950/40 p-3 text-sm text-red-200">
            {error}
          </div>
        )}
        {responseMeta && (
          <div className="mb-2 text-right text-xs text-gray-500">
            {responseMeta.model} / {(responseMeta.latencyMs / 1000).toFixed(1)}s
          </div>
        )}
        <form onSubmit={handleSend} className="flex gap-2 rounded-2xl border border-white/[0.07] bg-[#11151b] p-1.5 focus-within:border-blue-400/50">
          <input
            type="text"
            aria-label="Message"
            placeholder={chatId ? "Ask a question about this document..." : "Select a document first"}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            disabled={!chatId || isThinking}
            className="flex-1 min-w-0 rounded-xl bg-transparent p-3 text-white disabled:opacity-50 focus:outline-none"
          />
          {isThinking ? (
            <button type="button" onClick={stopGeneration} className="rounded-lg border border-red-500/50 bg-red-500/10 px-4 py-2 text-sm text-red-200">Stop</button>
          ) : (
            <button type="submit" disabled={!chatId || !input.trim()} className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-40 sm:px-6">Send</button>
          )}
        </form>
      </footer>
    </div>
  );
}
