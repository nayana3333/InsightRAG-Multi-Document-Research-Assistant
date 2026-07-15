import { useEffect, useState } from "react";
import { API_URL } from "./api";
import AuthContext from "./auth-context";
import { apiFetch, clearSession, readSession, storeSession } from "./auth-client";


export default function AuthProvider({ children }) {
  const [session, setSession] = useState(readSession);
  const [loading, setLoading] = useState(() => Boolean(readSession()));

  useEffect(() => {
    const expire = () => setSession(null);
    window.addEventListener("insightrag:auth-expired", expire);
    return () => window.removeEventListener("insightrag:auth-expired", expire);
  }, []);

  useEffect(() => {
    const persisted = readSession();
    if (!persisted) {
      setLoading(false);
      return;
    }
    apiFetch("/auth/me")
      .then(async (response) => {
        if (!response.ok) throw new Error("Session expired");
        const result = await response.json();
        const next = { ...persisted, user: result.user };
        storeSession(next);
        setSession(next);
      })
      .catch(() => {
        clearSession();
        setSession(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const authenticate = async (mode, values) => {
    const response = await fetch(`${API_URL}/auth/${mode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || "Authentication failed.");
    storeSession(result);
    setSession(result);
  };

  const value = {
    user: session?.user || null,
    loading,
    login: (values) => authenticate("login", values),
    register: (values) => authenticate("register", values),
    logout: () => {
      clearSession();
      setSession(null);
    },
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
