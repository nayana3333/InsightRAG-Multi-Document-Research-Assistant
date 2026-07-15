import { expect, test } from "@playwright/test";

test("registers, builds a multi-document workspace, and renders a streamed cited answer", async ({ page }) => {
  const user = { id: "usr_e2e", name: "AI Engineer", email: "engineer@example.com" };
  const documents = [];
  const conversations = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    const method = request.method();
    const respond = (body, status = 200, contentType = "application/json") =>
      route.fulfill({ status, contentType, body: typeof body === "string" ? body : JSON.stringify(body) });
    if (path === "/auth/register" && method === "POST") return respond({ accessToken: "signed-test-token", expiresAt: 9999999999, user }, 201);
    if (path === "/auth/me") return respond({ user });
    if (path === "/configuration") return respond({ aiConfigured: true, provider: "OpenRouter" });
    if (path === "/chats" && method === "GET") return respond({ conversations });
    if (path === "/chats" && method === "POST") {
      documents.push({ id: "doc_1", fileName: "research-one.pdf", status: "ready", pageCount: 4, byteSize: 1048576 });
      conversations.push({ id: "chat_1", fileName: "Research workspace", updatedAt: new Date().toISOString(), lastMessage: "Ready", messageCount: 1, documentCount: 1 });
      return respond({ chatId: "chat_1", documentId: "doc_1", fileName: "research-one.pdf", pageCount: 4, byteSize: 1048576, status: "ready" }, 201);
    }
    if (path === "/chats/chat_1/documents" && method === "GET") return respond({ documents });
    if (path === "/chats/chat_1/documents" && method === "POST") {
      documents.push({ id: "doc_2", fileName: "research-two.pdf", status: "ready", pageCount: 7, byteSize: 2097152 });
      return respond({ chatId: "chat_1", documentId: "doc_2", fileName: "research-two.pdf", pageCount: 7, byteSize: 2097152, status: "ready" }, 201);
    }
    if (path === "/chats/chat_1/documents/doc_2" && method === "DELETE") {
      documents.splice(documents.findIndex((item) => item.id === "doc_2"), 1);
      return route.fulfill({ status: 204, body: "" });
    }
    if (path === "/chats/chat_1/messages" && method === "GET") return respond({ messages: [{ id: 1, type: "ai", text: "Workspace ready.", sources: [] }] });
    if (path === "/chats/chat_1/messages/stream" && method === "POST") {
      const events = [
        { type: "sources", sources: [{ id: 1, fileName: "research-one.pdf", page: 2, relevance: 0.91, snippet: "Grounded evidence" }] },
        { type: "token", token: "The evidence " },
        { type: "token", token: "supports the conclusion [Source 1]." },
        { type: "done", model: "openrouter/free", latencyMs: 420 },
      ];
      return respond(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""), 200, "text/event-stream");
    }
    return respond({ detail: `Unhandled ${method} ${path}` }, 500);
  });

  await page.goto("/MainApp");
  await page.getByRole("button", { name: "No workspace yet? Create one" }).click();
  await page.getByLabel("Name").fill("AI Engineer");
  await page.getByLabel("Email").fill("engineer@example.com");
  await page.getByLabel("Password").fill("StrongPassword1!");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page.getByRole("heading", { name: "InsightRAG" })).toBeVisible();

  const upload = page.locator("#pdf-upload");
  await upload.setInputFiles({ name: "research-one.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 test") });
  await expect(page.getByText("research-one.pdf", { exact: true })).toBeVisible();
  await upload.setInputFiles({ name: "research-two.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 test two") });
  await expect(page.getByText("research-two.pdf", { exact: true })).toBeVisible();
  await expect(page.getByText("7 pages · 2.00 MB")).toBeVisible();

  await page.getByPlaceholder(/Ask a question about this document/).fill("What does the evidence show?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("The evidence supports the conclusion [Source 1].")).toBeVisible();
  await page.getByText("Evidence trail · 1 sources").click();
  await expect(page.getByText(/research-one\.pdf.*Page 2/)).toBeVisible();

  await page.getByRole("button", { name: "Remove research-two.pdf" }).click();
  await page.getByRole("button", { name: "Confirm remove research-two.pdf" }).click();
  await expect(page.getByText("research-two.pdf", { exact: true })).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Open knowledge base" }).click();
  await expect(page.getByRole("dialog", { name: "Evidence library" })).toBeVisible();
  await page.getByRole("button", { name: "Close panel" }).click();
  await page.getByRole("button", { name: "Open sources" }).click();
  await expect(page.getByRole("dialog", { name: "Sources" })).toBeVisible();
});
