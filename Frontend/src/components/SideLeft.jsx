import { useEffect, useState } from "react";
import { apiFetch } from "../auth-client";

export default function SideLeft({ onSelectChat, onDeleteChat, refreshToken, selectedChatId }) {
  const [conversations, setConversations] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [pendingDelete, setPendingDelete] = useState(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    apiFetch("/chats", { signal: controller.signal })
      .then(async (response) => {
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || "Could not load chats.");
        return result;
      })
      .then((data) => {
        setConversations(data.conversations || []);
        setError("");
      })
      .catch((requestError) => {
        if (requestError.name !== "AbortError") setError(requestError.message);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [refreshToken]);

  const deleteWorkspace = async (conversation) => {
    if (pendingDelete !== conversation.id) {
      setPendingDelete(conversation.id);
      return;
    }
    setError("");
    try {
      const response = await apiFetch(`/chats/${conversation.id}`, { method: "DELETE" });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail || "Could not delete the workspace.");
      }
      setConversations((current) => current.filter((item) => item.id !== conversation.id));
      setPendingDelete(null);
      onDeleteChat?.(conversation.id);
    } catch (requestError) {
      setError(requestError.message);
    }
  };

  const normalizedSearch = search.trim().toLowerCase();
  const visibleConversations = conversations.filter((conversation) =>
    `${conversation.fileName} ${conversation.lastMessage}`.toLowerCase().includes(normalizedSearch)
  );

  return (
    <div className="h-full min-h-0 p-4 flex flex-col">
      <div className="mb-4">
        <h2 className="text-lg font-semibold tracking-[-0.02em] text-white">Evidence library</h2>
        <p className="text-xs text-gray-500">Your connected workspaces</p>
      </div>

      <label className="mb-3 block">
        <span className="sr-only">Search workspaces</span>
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Find a workspace"
          className="w-full rounded-lg border border-gray-700 bg-[#171717] px-3 py-2 text-sm outline-none focus:border-blue-500"
        />
      </label>

      <div className="flex-1 min-h-0 overflow-y-auto space-y-2">
        {loading && <div className="text-sm text-gray-500">Loading conversations...</div>}
        {error && <div className="rounded-lg bg-red-950/40 p-2 text-xs text-red-200">{error}</div>}
        {visibleConversations.map((conversation) => (
          <div
            key={conversation.id}
            className={`group relative w-full rounded-xl border transition ${
              selectedChatId === conversation.id
                ? "border-blue-400/40 bg-blue-500/[0.08]"
                : "border-transparent bg-[#1c1c1c] hover:border-gray-700"
            }`}
          >
            <button type="button" onClick={() => onSelectChat(conversation.id)} className="w-full p-3 pr-9 text-left">
              <div className="truncate text-sm font-medium text-white">{conversation.fileName}</div>
              <div className="mt-1 truncate text-xs text-gray-400">{conversation.lastMessage || "Ready to chat"}</div>
              <div className="mt-2 flex justify-between gap-2 text-[11px] text-gray-500">
                <span>{conversation.documentCount || 1} docs · {conversation.messageCount} messages</span>
                <span>{new Date(conversation.updatedAt || conversation.timestamp).toLocaleDateString()}</span>
              </div>
            </button>
            <button
              type="button"
              onClick={() => deleteWorkspace(conversation)}
              onBlur={() => setTimeout(() => setPendingDelete(null), 150)}
              className={`absolute right-2 top-2 rounded-md px-2 py-1 text-[10px] ${pendingDelete === conversation.id ? "bg-red-600 text-white" : "text-gray-500 hover:bg-red-950 hover:text-red-300"}`}
              aria-label={pendingDelete === conversation.id ? `Confirm delete ${conversation.fileName}` : `Delete ${conversation.fileName}`}
            >
              {pendingDelete === conversation.id ? "Confirm" : "Delete"}
            </button>
          </div>
        ))}
        {!loading && visibleConversations.length === 0 && conversations.length > 0 && (
          <div className="mt-8 text-center text-sm text-gray-500">No matching workspaces.</div>
        )}
        {!loading && conversations.length === 0 && !error && (
          <div className="mt-8 text-center text-sm text-gray-500">No indexed documents yet.</div>
        )}
      </div>

      <a href="/" className="mt-5 text-center text-sm text-blue-400 hover:text-blue-200">
        Back to Home
      </a>
    </div>
  );
}
