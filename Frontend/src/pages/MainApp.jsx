import { useState } from "react";
import ChatWindow from "../components/ChatWindow";
import SideLeft from "../components/SideLeft";
import SideRight from "../components/SideRight";

export default function MainApp() {
  const [selectedChatId, setSelectedChatId] = useState(null);
  const [conversationRefresh, setConversationRefresh] = useState(0);
  const [mobilePanel, setMobilePanel] = useState(null);

  const refreshConversations = () => setConversationRefresh((value) => value + 1);
  const selectChat = (chatId) => {
    setSelectedChatId(chatId);
    setMobilePanel(null);
  };
  const handleUploadComplete = (chatId) => {
    selectChat(chatId);
    refreshConversations();
  };
  const handleDeleteChat = (chatId) => {
    if (selectedChatId === chatId) setSelectedChatId(null);
    refreshConversations();
  };

  const library = (
    <SideLeft
      onSelectChat={selectChat}
      onDeleteChat={handleDeleteChat}
      refreshToken={conversationRefresh}
      selectedChatId={selectedChatId}
    />
  );
  const documents = (
    <SideRight onUploadComplete={handleUploadComplete} chatId={selectedChatId} />
  );

  return (
    <div className="h-[100dvh] min-h-0 bg-[#0d0d0d] text-white">
      <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[240px_minmax(0,1fr)] xl:grid-cols-[240px_minmax(0,1fr)_300px]">
        <aside className="hidden min-w-0 border-r border-gray-800 lg:block">{library}</aside>
        <main className="min-h-0 min-w-0">
          <ChatWindow
            chatId={selectedChatId}
            onMessageComplete={refreshConversations}
            onOpenLibrary={() => setMobilePanel("library")}
            onOpenDocuments={() => setMobilePanel("documents")}
          />
        </main>
        <aside className="hidden min-w-0 overflow-y-auto border-l border-gray-800 xl:block">
          {documents}
        </aside>
      </div>

      {mobilePanel && (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label={mobilePanel === "library" ? "Evidence library" : "Sources"}>
          <button
            type="button"
            className="absolute inset-0 bg-black/70"
            aria-label="Dismiss panel"
            onClick={() => setMobilePanel(null)}
          />
          <aside className="absolute inset-y-0 right-0 w-[min(88vw,360px)] overflow-y-auto border-l border-gray-700 bg-[#101010] shadow-2xl">
            <button
              type="button"
              onClick={() => setMobilePanel(null)}
              className="absolute right-3 top-3 z-10 rounded-lg border border-gray-700 bg-[#171717] px-3 py-2 text-xs text-gray-300"
              aria-label="Close panel"
            >
              Close
            </button>
            {mobilePanel === "library" ? library : documents}
          </aside>
        </div>
      )}

      {mobilePanel === "documents" && (
        <div className="fixed inset-0 z-50 hidden lg:block xl:hidden" role="dialog" aria-modal="true" aria-label="Sources">
          <button type="button" className="absolute inset-0 bg-black/60" aria-label="Close documents" onClick={() => setMobilePanel(null)} />
          <aside className="absolute inset-y-0 right-0 w-[340px] overflow-y-auto border-l border-gray-700 bg-[#101010] shadow-2xl">
            <button type="button" onClick={() => setMobilePanel(null)} className="absolute right-3 top-3 z-10 rounded-lg border border-gray-700 px-3 py-2 text-xs" aria-label="Close documents">Close</button>
            {documents}
          </aside>
        </div>
      )}
    </div>
  );
}
