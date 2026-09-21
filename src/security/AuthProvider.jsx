import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { SecurityContext } from "./securityContext.js";

const SECURITY_ROOT = "/local/security";
const REMEMBERED_ACCOUNT_KEY = "bass.security.rememberedAccount";
const STORAGE_WARNING =
  "This browser cannot save the selected account. PIN-only return sign-in will last only until this tab reloads.";

function hostScope(status) {
  const ownership =
    status?.ownership || (status?.configured ? "legacy" : "unclaimed");
  if (
    !status?.configured ||
    ownership === "unclaimed" ||
    typeof status?.device_id !== "string" ||
    !status.device_id ||
    !Number.isInteger(status?.generation) ||
    status.generation < 0
  ) {
    return null;
  }
  return { device_id: status.device_id, generation: status.generation };
}

function isRememberedAccount(value) {
  return Boolean(
    value &&
      typeof value.device_id === "string" &&
      value.device_id &&
      Number.isInteger(value.generation) &&
      value.generation >= 0 &&
      typeof value.user?.id === "string" &&
      value.user.id &&
      typeof value.user?.name === "string" &&
      value.user.name,
  );
}

function matchesScope(account, scope) {
  return Boolean(
    account &&
      scope &&
      account.device_id === scope.device_id &&
      account.generation === scope.generation,
  );
}

async function securityRequest(path, options = {}) {
  const response = await fetch(`${SECURITY_ROOT}${path}`, {
    cache: "no-store",
    credentials: "same-origin",
    redirect: "error",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.message || payload?.error || payload?.detail;
    const error = new Error(
      typeof detail === "string"
        ? detail
        : `HTTP ${response.status} ${response.statusText}`,
    );
    error.status = response.status;
    error.code = payload?.code;
    throw error;
  }
  return payload;
}

export default function AuthProvider({ children }) {
  const [phase, setPhase] = useState("loading");
  const [user, setUser] = useState(null);
  const [csrfToken, setCsrfToken] = useState("");
  const [hostStatus, setHostStatus] = useState(null);
  const [error, setError] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const [sessionEpoch, setSessionEpoch] = useState(0);
  const [rememberedAccount, setRememberedAccount] = useState(null);
  const [rememberWarning, setRememberWarning] = useState("");
  const requestGenerationRef = useRef(0);
  const authRequestRef = useRef(null);
  const rememberedAccountRef = useRef(null);
  const memoryOnlyRef = useRef(false);

  const cancelAuthentication = useCallback(() => {
    authRequestRef.current?.abort();
    authRequestRef.current = null;
    requestGenerationRef.current += 1;
    return requestGenerationRef.current;
  }, []);

  const applyRememberedAccount = useCallback((account) => {
    rememberedAccountRef.current = account;
    setRememberedAccount(account);
  }, []);

  const clearRememberedAccount = useCallback(() => {
    applyRememberedAccount(null);
    try {
      localStorage.removeItem(REMEMBERED_ACCOUNT_KEY);
      if (localStorage.getItem(REMEMBERED_ACCOUNT_KEY) !== null) {
        throw new Error("Saved account could not be cleared.");
      }
      memoryOnlyRef.current = false;
      setRememberWarning("");
    } catch {
      memoryOnlyRef.current = true;
      setRememberWarning(
        "The account selection was cleared for this tab, but this browser could not clear its saved copy. Clear this site's data before using a shared device.",
      );
    }
  }, [applyRememberedAccount]);

  const loadRememberedAccount = useCallback(
    (status) => {
      const scope = hostScope(status);
      if (!scope) {
        clearRememberedAccount();
        return null;
      }

      try {
        const stored = localStorage.getItem(REMEMBERED_ACCOUNT_KEY);
        if (!stored) {
          const transient = rememberedAccountRef.current;
          const account =
            memoryOnlyRef.current && matchesScope(transient, scope)
              ? transient
              : null;
          applyRememberedAccount(account);
          if (!account) setRememberWarning("");
          return account;
        }

        let account;
        try {
          account = JSON.parse(stored);
        } catch {
          localStorage.removeItem(REMEMBERED_ACCOUNT_KEY);
          memoryOnlyRef.current = false;
          applyRememberedAccount(null);
          setRememberWarning(
            "The saved account was invalid and was cleared. Sign in with your name once.",
          );
          return null;
        }
        if (!isRememberedAccount(account) || !matchesScope(account, scope)) {
          localStorage.removeItem(REMEMBERED_ACCOUNT_KEY);
          memoryOnlyRef.current = false;
          applyRememberedAccount(null);
          setRememberWarning(
            "The saved account did not match this host and was cleared. Sign in with your name once.",
          );
          return null;
        }

        applyRememberedAccount(account);
        memoryOnlyRef.current = false;
        setRememberWarning("");
        return account;
      } catch {
        memoryOnlyRef.current = true;
        const transient = rememberedAccountRef.current;
        const account = matchesScope(transient, scope) ? transient : null;
        applyRememberedAccount(account);
        setRememberWarning(STORAGE_WARNING);
        return account;
      }
    },
    [applyRememberedAccount, clearRememberedAccount],
  );

  const rememberAuthenticatedAccount = useCallback(
    (status, authenticatedUser) => {
      const scope = hostScope(status);
      if (
        !scope ||
        typeof authenticatedUser?.id !== "string" ||
        !authenticatedUser.id ||
        typeof authenticatedUser?.name !== "string" ||
        !authenticatedUser.name
      ) {
        applyRememberedAccount(null);
        setRememberWarning(
          "This host did not provide a stable account identity, so PIN-only return sign-in is unavailable.",
        );
        return;
      }

      const account = {
        ...scope,
        user: { id: authenticatedUser.id, name: authenticatedUser.name },
      };
      applyRememberedAccount(account);
      try {
        const stored = JSON.stringify(account);
        localStorage.setItem(REMEMBERED_ACCOUNT_KEY, stored);
        if (localStorage.getItem(REMEMBERED_ACCOUNT_KEY) !== stored) {
          throw new Error("Saved account could not be verified.");
        }
        memoryOnlyRef.current = false;
        setRememberWarning("");
      } catch {
        memoryOnlyRef.current = true;
        setRememberWarning(STORAGE_WARNING);
      }
    },
    [applyRememberedAccount],
  );

  const clearSession = useCallback((nextPhase = "login") => {
    cancelAuthentication();
    setUser(null);
    setCsrfToken("");
    setError("");
    setLoggingOut(false);
    setLogoutError("");
    setSessionEpoch((value) => value + 1);
    setPhase(nextPhase);
  }, [cancelAuthentication]);

  const acceptSession = useCallback(
    (payload, generation, status) => {
      if (requestGenerationRef.current !== generation) return false;
      if (!payload?.user?.id || typeof payload?.csrf_token !== "string") {
        throw new Error("The host returned an invalid session.");
      }
      rememberAuthenticatedAccount(status, payload.user);
      setUser(payload.user);
      setCsrfToken(payload.csrf_token);
      setError("");
      setSessionEpoch((value) => value + 1);
      setPhase("authenticated");
      return true;
    },
    [rememberAuthenticatedAccount],
  );

  const refreshAuthentication = useCallback(async () => {
    if (authRequestRef.current) return null;
    const request = new AbortController();
    const generation = requestGenerationRef.current + 1;
    requestGenerationRef.current = generation;
    authRequestRef.current = request;
    try {
      const status = await securityRequest("/status", {
        signal: request.signal,
      });
      if (requestGenerationRef.current !== generation) return null;
      setHostStatus(status);
      const ownership =
        status?.ownership || (status?.configured ? "legacy" : "unclaimed");
      if (!status?.configured || ownership === "unclaimed") {
        loadRememberedAccount(status);
        setUser(null);
        setCsrfToken("");
        setError("");
        setPhase("setup");
        return status;
      }
      loadRememberedAccount(status);

      try {
        const session = await securityRequest("/session", {
          signal: request.signal,
        });
        acceptSession(session, generation, status);
      } catch (sessionError) {
        if (sessionError.name === "AbortError") return null;
        if (requestGenerationRef.current !== generation) return null;
        if (sessionError.status !== 401) throw sessionError;
        setUser(null);
        setCsrfToken("");
        setError("");
        setPhase("login");
      }
      return status;
    } catch (requestError) {
      if (requestError.name === "AbortError") return null;
      if (requestGenerationRef.current !== generation) return null;
      setError(requestError.message);
      setPhase("error");
      throw requestError;
    } finally {
      if (authRequestRef.current === request) authRequestRef.current = null;
    }
  }, [acceptSession, loadRememberedAccount]);

  useEffect(() => {
    void refreshAuthentication().catch(() => {});
    return () => {
      const request = authRequestRef.current;
      request?.abort();
      if (authRequestRef.current === request) authRequestRef.current = null;
    };
  }, [refreshAuthentication]);

  const login = useCallback(
    async (name, pin) => {
      if (authRequestRef.current) {
        throw new Error("An authentication request is already in progress.");
      }
      const request = new AbortController();
      const generation = requestGenerationRef.current + 1;
      requestGenerationRef.current = generation;
      authRequestRef.current = request;
      try {
        const scope = hostScope(hostStatus);
        const remembered = rememberedAccountRef.current;
        if (remembered && !matchesScope(remembered, scope)) {
          clearRememberedAccount();
          throw new Error(
            "The host identity changed. Sign in with your name and PIN.",
          );
        }
        const loginTarget = remembered
          ? { user_id: remembered.user.id }
          : { name: name.trim() };
        const payload = await securityRequest("/login", {
          method: "POST",
          body: JSON.stringify({ ...loginTarget, pin }),
          signal: request.signal,
        });
        return acceptSession(payload, generation, hostStatus);
      } catch (loginError) {
        if (
          loginError.name === "AbortError" ||
          requestGenerationRef.current !== generation
        ) {
          return false;
        }

        if (authRequestRef.current === request) authRequestRef.current = null;
        const statusRequest = new AbortController();
        const statusGeneration = requestGenerationRef.current + 1;
        requestGenerationRef.current = statusGeneration;
        authRequestRef.current = statusRequest;
        try {
          const status = await securityRequest("/status", {
            signal: statusRequest.signal,
          });
          if (requestGenerationRef.current === statusGeneration) {
            setHostStatus(status);
            loadRememberedAccount(status);
            const ownership =
              status?.ownership ||
              (status?.configured ? "legacy" : "unclaimed");
            if (!status?.configured || ownership === "unclaimed") {
              setUser(null);
              setCsrfToken("");
              setError("");
              setPhase("setup");
            }
          }
        } catch {
          // Keep the original login error and form state if status is unavailable.
        } finally {
          if (authRequestRef.current === statusRequest) {
            authRequestRef.current = null;
          }
        }
        if (requestGenerationRef.current !== statusGeneration) return false;
        throw loginError;
      } finally {
        if (authRequestRef.current === request) authRequestRef.current = null;
      }
    },
    [
      acceptSession,
      clearRememberedAccount,
      hostStatus,
      loadRememberedAccount,
    ],
  );

  const logout = useCallback(async () => {
    if (loggingOut) return;
    const token = csrfToken;
    const generation = cancelAuthentication();
    setLoggingOut(true);
    setLogoutError("");
    try {
      await securityRequest("/logout", {
        method: "POST",
        headers: { "X-BASS-CSRF": token },
      });
      if (requestGenerationRef.current === generation) {
        clearRememberedAccount();
        clearSession("login");
      }
    } catch (requestError) {
      if (requestGenerationRef.current !== generation) return;
      if (requestError.status === 401) {
        clearRememberedAccount();
        clearSession("login");
        return;
      }
      setLogoutError(
        "Sign-out could not be confirmed. Check the host connection and retry.",
      );
      setLoggingOut(false);
    }
  }, [
    cancelAuthentication,
    clearRememberedAccount,
    clearSession,
    csrfToken,
    loggingOut,
  ]);

  const useAnotherAccount = useCallback(async () => {
    const generation = cancelAuthentication();
    try {
      await securityRequest("/logout", {
        method: "POST",
        headers: { "X-BASS-CSRF": csrfToken },
      });
    } catch (requestError) {
      if (requestError.status !== 401) throw requestError;
    }
    if (requestGenerationRef.current !== generation) return false;
    clearRememberedAccount();
    clearSession("login");
    return true;
  }, [
    cancelAuthentication,
    clearRememberedAccount,
    clearSession,
    csrfToken,
  ]);

  const expireSession = useCallback(
    (nextPhase = "login") => {
      if (nextPhase === "setup") clearRememberedAccount();
      if (nextPhase === "login") {
        clearSession("loading");
        void refreshAuthentication().catch(() => {});
        return;
      }
      clearSession(nextPhase);
    },
    [clearRememberedAccount, clearSession, refreshAuthentication],
  );

  useEffect(() => {
    const synchronizeAccount = (event) => {
      if (event.key !== REMEMBERED_ACCOUNT_KEY && event.key !== null) return;
      memoryOnlyRef.current = false;
      if (event.newValue === null) applyRememberedAccount(null);
      clearSession("loading");
      void refreshAuthentication().catch(() => {});
    };
    window.addEventListener("storage", synchronizeAccount);
    return () => window.removeEventListener("storage", synchronizeAccount);
  }, [applyRememberedAccount, clearSession, refreshAuthentication]);

  const dismissLogoutError = useCallback(() => setLogoutError(""), []);

  const value = useMemo(
    () => ({
      phase,
      user,
      csrfToken,
      hostStatus,
      error,
      loggingOut,
      logoutError,
      sessionEpoch,
      rememberedAccount,
      rememberWarning,
      login,
      logout,
      useAnotherAccount,
      dismissLogoutError,
      expireSession,
      refreshAuthentication,
    }),
    [
      csrfToken,
      dismissLogoutError,
      error,
      expireSession,
      hostStatus,
      login,
      loggingOut,
      logout,
      logoutError,
      phase,
      rememberedAccount,
      rememberWarning,
      refreshAuthentication,
      sessionEpoch,
      useAnotherAccount,
      user,
    ],
  );

  return (
    <SecurityContext.Provider value={value}>
      {children}
    </SecurityContext.Provider>
  );
}
