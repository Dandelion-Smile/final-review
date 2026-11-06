import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import AuthPage, { authRoute, readAuthRoute } from "./AuthPage";
import { AccountRecovery } from "./AccountDeletion";
import { errorMessage, type Account } from "./account-api";
import { api, ApiError } from "./workbench-api";
import "./account.css";

const SESSION_EVENT = "final-review-session-changed";
const DEFAULT_ROUTE = "#/courses";

function clearAccountData() {
  for (const key of Object.keys(localStorage)) {
    if (/^(current-course-id$|current-conversation-|note-scope-|note-uploads-|note-selection-|note-export:|notes-generation-)/.test(key)) {
      localStorage.removeItem(key);
    }
  }
  for (const key of Object.keys(sessionStorage)) {
    if (key.startsWith("open-formal-attempt-")) sessionStorage.removeItem(key);
  }
}

export type AccountSession = {
  account: Account;
  update: (updated: Account) => void;
  signOut: () => Promise<void>;
  reauthenticate: (email: string, notice: string) => void;
};

export default function SessionGate({ children }: { children: (session: AccountSession) => ReactNode }) {
  const [hash, setHash] = useState(window.location.hash);
  const [account, setAccount] = useState<Account | null | undefined>(undefined);
  const currentAccount = useRef(account);
  const checkVersion = useRef(0);
  const [initialEmail, setInitialEmail] = useState("");
  const [notice, setNotice] = useState("");
  const [startupError, setStartupError] = useState("");
  const route = readAuthRoute(hash);

  const replaceRoute = useCallback((destination: string) => {
    window.history.replaceState(null, "", destination);
    setHash(destination);
  }, []);

  const update = useCallback((updated: Account | null) => {
    if (currentAccount.current && currentAccount.current.id !== updated?.id) clearAccountData();
    currentAccount.current = updated;
    setAccount(updated);
  }, []);

  const checkSession = useCallback(async (fromAnotherTab = false) => {
    const version = ++checkVersion.current;
    try {
      const restored = await api<Account>("/api/auth/me");
      if (version !== checkVersion.current) return;
      setStartupError("");
      update(restored);
    } catch (failure) {
      if (version !== checkVersion.current) return;
      if (failure instanceof ApiError && failure.status === 401) {
        setStartupError("");
        update(null);
      } else if (!currentAccount.current || fromAnotherTab) {
        setStartupError("暂时无法连接账户服务，请稍后重试。");
        update(null);
      }
    }
  }, [update]);

  useEffect(() => {
    function hashChanged() {
      setHash(window.location.hash);
    }
    function sessionChanged(event: StorageEvent) {
      if (event.key === SESSION_EVENT) {
        setAccount(undefined);
        void checkSession(true);
      }
    }
    function recheck() {
      if (currentAccount.current) void checkSession();
    }
    window.addEventListener("hashchange", hashChanged);
    window.addEventListener("storage", sessionChanged);
    window.addEventListener("final-review-session-expired", recheck);
    void checkSession();
    return () => {
      window.removeEventListener("hashchange", hashChanged);
      window.removeEventListener("storage", sessionChanged);
      window.removeEventListener("final-review-session-expired", recheck);
    };
  }, [checkSession]);

  useEffect(() => {
    if (account === undefined) return;
    if (account?.deletion_due_at) {
      if (hash !== "#/account/recovery") replaceRoute("#/account/recovery");
      return;
    }
    if (account && route) replaceRoute(route.next || DEFAULT_ROUTE);
    if (account && hash === "#/account/recovery") replaceRoute(DEFAULT_ROUTE);
    if (!account && !route) replaceRoute(authRoute("login", hash));
  }, [account, hash, route?.mode, route?.next, replaceRoute]);

  function broadcast() {
    localStorage.setItem(SESSION_EVENT, `${Date.now()}-${Math.random()}`);
  }

  function reauthenticate(email: string, message: string) {
    ++checkVersion.current;
    setInitialEmail(email);
    setNotice(message);
    setStartupError("");
    update(null);
    replaceRoute(authRoute("login", ""));
    broadcast();
  }

  async function signOut() {
    await api("/api/auth/sign-out", "POST");
    reauthenticate("", "已退出登录。");
  }

  async function login() {
    ++checkVersion.current;
    let restored: Account;
    try {
      restored = await api<Account>("/api/auth/me");
      update(restored);
    } catch (error) {
      throw new Error(error instanceof ApiError && error.status === 401
        ? "无法保存登录会话，请检查浏览器的 Cookie 设置。" : errorMessage(error));
    }
    setNotice("");
    setStartupError("");
    broadcast();
    replaceRoute(restored.deletion_due_at ? "#/account/recovery" : route?.next || DEFAULT_ROUTE);
  }

  if (account?.deletion_due_at) {
    return <AccountRecovery account={account} onReauthenticate={reauthenticate} signOut={signOut} />;
  }
  if (account === undefined || (account && route)) {
    return <main className="session-loading" role="status">正在检查登录状态…</main>;
  }
  if (account) return children({ account, update, signOut, reauthenticate });

  return <AuthPage key={route?.mode ?? "login"} mode={route?.mode ?? "login"} next={route?.next ?? hash}
    initialEmail={initialEmail} notice={notice} startupError={startupError} onLogin={login}
    onComplete={(email, message) => {
      setInitialEmail(email);
      setNotice(message);
      replaceRoute(authRoute("login", route?.next ?? ""));
    }} />;
}
