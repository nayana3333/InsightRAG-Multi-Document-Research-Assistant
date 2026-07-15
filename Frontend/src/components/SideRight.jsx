import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../auth-client";

export default function SidebarRight({ onUploadComplete, chatId }) {
  const [uploadedFiles, setUploadedFiles] = useState([]);
  const [error, setError] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [aiConfigured, setAiConfigured] = useState(null);
  const [connectionIssue, setConnectionIssue] = useState(false);
  const [success, setSuccess] = useState("");
  const [evaluation, setEvaluation] = useState(null);
  const [evaluationQuestion, setEvaluationQuestion] = useState("");
  const [relevantPages, setRelevantPages] = useState("");
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [uploadProgress, setUploadProgress] = useState("");
  const [pendingRemove, setPendingRemove] = useState(null);
  const [removingId, setRemovingId] = useState(null);

  const checkConfiguration = useCallback(async (refresh = false) => {
    try {
      const response = await apiFetch(`/configuration${refresh ? "?refresh=true" : ""}`);
      if (!response.ok) throw new Error("Could not check backend configuration.");
      const result = await response.json();
      setConnectionIssue(false);
      setAiConfigured(result.aiConfigured);
      setError(
        result.aiConfigured
          ? ""
          : result.configurationError ||
              "OpenRouter API key is not configured. Add OPENROUTER_API_KEY to Backend/.env."
      );
      return true;
    } catch {
      setConnectionIssue(true);
      setAiConfigured(null);
      setError("Backend is starting. Reconnecting automatically...");
      return false;
    }
  }, []);

  useEffect(() => {
    let active = true;
    let retryTimer;

    const verifyConfiguration = async () => {
      const connected = await checkConfiguration();
      if (active && !connected) {
        retryTimer = setTimeout(verifyConfiguration, 3000);
      }
    };

    verifyConfiguration();
    return () => {
      active = false;
      clearTimeout(retryTimer);
    };
  }, [checkConfiguration]);

  useEffect(() => {
    if (!chatId) {
      setUploadedFiles([]);
      return;
    }
    const controller = new AbortController();
    apiFetch(`/chats/${chatId}/documents`, { signal: controller.signal })
      .then(async (response) => {
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || "Could not load documents.");
        setUploadedFiles(result.documents || []);
      })
      .catch((requestError) => {
        if (requestError.name !== "AbortError") setError(requestError.message);
      });
    return () => controller.abort();
  }, [chatId]);

  const handleFileUpload = async (event) => {
    const files = event.target.files;
    if (!files.length) return;

    setError("");
    setSuccess("");
    setIsUploading(true);

    let targetChatId = chatId;
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      setUploadProgress(`Indexing ${index + 1} of ${files.length}: ${file.name}`);
      const formData = new FormData();
      formData.append("file", file);

      try {
        const endpoint = targetChatId ? `/chats/${targetChatId}/documents` : "/chats";
        const response = await apiFetch(endpoint, {
          method: "POST",
          body: formData,
        });

        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || "Upload failed.");
        targetChatId = result.chatId;
        setSuccess(`${file.name} was added to the workspace.`);

        // Update UI with uploaded file
        setUploadedFiles((prev) => [
          ...prev,
          {
            id: result.documentId,
            fileName: file.name,
            byteSize: result.byteSize || file.size,
            pageCount: result.pageCount || 0,
            status: "ready",
          },
        ]);
      } catch (error) {
        setError(error.message);
        break;
      }
    }

    setIsUploading(false);
    setUploadProgress("");
    if (targetChatId) onUploadComplete?.(targetChatId);
    event.target.value = "";
  };

  const removeDocument = async (document) => {
    if (pendingRemove !== document.id) {
      setPendingRemove(document.id);
      return;
    }
    setError("");
    setRemovingId(document.id);
    try {
      const response = await apiFetch(`/chats/${chatId}/documents/${document.id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail || "Document deletion failed.");
      }
      setUploadedFiles((current) => current.filter((item) => item.id !== document.id));
      setPendingRemove(null);
      setSuccess(`${document.fileName} was removed and the workspace was reindexed.`);
      onUploadComplete?.(chatId);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setRemovingId(null);
    }
  };

  const runEvaluation = async (event) => {
    event.preventDefault();
    const pages = relevantPages
      .split(",")
      .map((page) => Number(page.trim()))
      .filter((page) => Number.isInteger(page) && page > 0);
    if (!chatId || !evaluationQuestion.trim() || pages.length === 0) return;
    setIsEvaluating(true);
    setError("");
    try {
      const response = await apiFetch(`/chats/${chatId}/evaluations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cases: [{ question: evaluationQuestion.trim(), relevantPages: pages }],
          k: 4,
        }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || "Evaluation failed.");
      setEvaluation(result);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setIsEvaluating(false);
    }
  };

  return (
    <div className="p-4 pt-14 lg:pt-4">
      <h2 className="text-lg mb-1 font-semibold tracking-[-0.02em] text-white">Sources</h2>
      <p className="mb-4 text-xs text-gray-500">Evidence indexed in this workspace</p>

      {/* Upload area */}
      <div
        className="border-2 border-dashed border-[#4C82FB]
             rounded-xl text-center overflow-hidden hover:bg-[#2a2a2a]"
      >
        <input
          id="pdf-upload"
          type="file"
          accept="application/pdf,.pdf"
          multiple
          className="sr-only"
          onChange={handleFileUpload}
          disabled={isUploading || aiConfigured !== true}
        />
        <label
          htmlFor="pdf-upload"
          className={`flex min-h-40 w-full items-center justify-center p-6 ${
            isUploading || aiConfigured !== true
              ? "cursor-not-allowed opacity-60"
              : "cursor-pointer"
          }`}
        >
          <span>
            {isUploading
              ? uploadProgress || "Uploading and indexing..."
              : aiConfigured === null
                ? "Checking backend..."
                : "Add PDF sources"}
          </span>
        </label>
      </div>

      {error && (
        <div
          role="alert"
          className={`mt-4 rounded-lg border p-3 text-sm ${
            connectionIssue
              ? "border-amber-500/50 bg-amber-950/40 text-amber-200"
              : "border-red-500/50 bg-red-950/40 text-red-200"
          }`}
        >
          <p>{error}</p>
          {aiConfigured !== true && (
            <button
              type="button"
              onClick={() => checkConfiguration(true)}
              className="mt-2 text-blue-300 underline hover:text-blue-200"
            >
              Retry now
            </button>
          )}
        </div>
      )}

      {success && (
        <div aria-live="polite" className="mt-4 rounded-lg border border-blue-500/40 bg-blue-950/30 p-3 text-sm text-blue-200">
          {success}
        </div>
      )}

      {/* Uploaded files list */}
      {uploadedFiles.length > 0 && (
        <ul className="mt-4 space-y-2">
          {uploadedFiles.map((file) => (
            <li
              key={file.id}
              className="p-3 rounded-lg 
                        bg-[#1c1c1c] hover:bg-[#2a2a2a] flex justify-between"
            >
              <div className="min-w-0">
                <span className="block truncate text-sm text-white">{file.fileName}</span>
                <span className="text-xs text-gray-500">
                  {file.pageCount ? `${file.pageCount} pages · ` : ""}
                  {file.byteSize ? `${(file.byteSize / (1024 * 1024)).toFixed(2)} MB` : "Indexed"}
                </span>
              </div>
              <button
                type="button"
                onClick={() => removeDocument(file)}
                disabled={removingId === file.id || uploadedFiles.length === 1}
                className={`ml-2 rounded px-2 text-xs disabled:cursor-not-allowed disabled:opacity-40 ${pendingRemove === file.id ? "bg-red-600 text-white" : "text-red-300 hover:text-red-200"}`}
                aria-label={pendingRemove === file.id ? `Confirm remove ${file.fileName}` : `Remove ${file.fileName}`}
                title={uploadedFiles.length === 1 ? "A workspace must contain at least one document" : ""}
              >
                {removingId === file.id ? "Reindexing..." : pendingRemove === file.id ? "Confirm" : "Remove"}
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Fallback message */}
      {uploadedFiles.length === 0 && (
        <div className="mt-4 text-gray-500 text-sm text-center">
          No sources connected yet
        </div>
      )}

      <div className="mt-6 border-t border-gray-800 pt-5">
        <h3 className="text-sm font-semibold text-white">Retrieval quality lab</h3>
        <p className="mt-1 text-xs leading-5 text-gray-500">Check whether the right evidence surfaces before generation.</p>
        <form onSubmit={runEvaluation} className="mt-3 space-y-2">
          <textarea
            value={evaluationQuestion}
            onChange={(event) => setEvaluationQuestion(event.target.value)}
            disabled={!chatId || isEvaluating}
            placeholder="Evaluation question"
            rows={3}
            className="w-full resize-none rounded-lg border border-gray-700 bg-[#171717] p-2 text-xs outline-none focus:border-blue-500 disabled:opacity-50"
          />
          <input
            value={relevantPages}
            onChange={(event) => setRelevantPages(event.target.value)}
            disabled={!chatId || isEvaluating}
            placeholder="Relevant pages, e.g. 2, 4"
            className="w-full rounded-lg border border-gray-700 bg-[#171717] p-2 text-xs outline-none focus:border-blue-500 disabled:opacity-50"
          />
          <button
            disabled={!chatId || !evaluationQuestion.trim() || !relevantPages.trim() || isEvaluating}
            className="w-full rounded-lg border border-blue-500/50 bg-blue-500/10 p-2 text-xs text-blue-200 disabled:opacity-40"
          >
            {isEvaluating ? "Evaluating..." : "Run evaluation"}
          </button>
        </form>
        {evaluation && (
          <div className="mt-3 grid grid-cols-3 gap-2 text-center text-[11px]">
            <div className="rounded-lg bg-[#181818] p-2"><strong className="block text-blue-300">{Math.round(evaluation.retrievalHitRate * 100)}%</strong>Hit@4</div>
            <div className="rounded-lg bg-[#181818] p-2"><strong className="block text-blue-300">{evaluation.meanReciprocalRank.toFixed(2)}</strong>MRR</div>
            <div className="rounded-lg bg-[#181818] p-2"><strong className="block text-violet-300">{Math.round(evaluation.averageTopRelevance * 100)}%</strong>Top score</div>
          </div>
        )}
      </div>
    </div>
  );
}
