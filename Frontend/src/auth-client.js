import { API_URL } from "./api";


const STORAGE_KEY = "insightrag.session";
const LEGACY_STORAGE_KEY = "ragflow.session";


export function readSession() {
  try {
    const current = localStorage.getItem(STORAGE_KEY);
    if (current) return JSON.parse(current) || null;
    const legacy = localStorage.getItem(LEGACY_STORAGE_KEY);
    if (!legacy) return null;
    const session = JSON.parse(legacy) || null;
    if (session) {
      localStorage.setItem(STORAGE_KEY, legacy);
      localStorage.removeItem(LEGACY_STORAGE_KEY);
    }
    return session;
  } catch {
    return null;
  }
}


export function storeSession(session) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
}


export function clearSession() {
  localStorage.removeItem(STORAGE_KEY);
  localStorage.removeItem(LEGACY_STORAGE_KEY);
}


export async function apiFetch(path, options = {}) {
  const session = readSession();
  const headers = new Headers(options.headers || {});
  if (session?.accessToken) headers.set("Authorization", `Bearer ${session.accessToken}`);
  const response = await fetch(`${API_URL}${path}`, { ...options, headers });
  if (response.status === 401 && session) {
    clearSession();
    window.dispatchEvent(new Event("insightrag:auth-expired"));
  }
  return response;
}
