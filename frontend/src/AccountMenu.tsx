import { createContext, useContext, useRef, useState } from "react";
import { errorMessage, type Account } from "./account-api";
import { NotificationBell } from "./NotificationCenter";

export const AccountMenuContext = createContext<{ account: Account; signOut: () => Promise<void> } | null>(null);

export function AccountAvatar({ account }: { account: Account }) {
  const [failed, setFailed] = useState(false);
  if (account.avatar_url && !failed) {
    return <img className="account-avatar" src={account.avatar_url} alt="" onError={() => setFailed(true)} />;
  }
  const initial = Array.from(account.nickname || account.email || "学")[0].toUpperCase();
  return <span className="account-avatar account-avatar-fallback" aria-hidden="true">{initial}</span>;
}

export default function AccountMenu() {
  const session = useContext(AccountMenuContext);
  const menu = useRef<HTMLDetailsElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!session || session.account.local) return null;
  const { account, signOut } = session;

  async function logout() {
    setBusy(true);
    setError("");
    try {
      await signOut();
    } catch (failure) {
      setError(errorMessage(failure));
      setBusy(false);
    }
  }

  return <><NotificationBell /><details className="account-menu" ref={menu} onKeyDown={event => {
    if (event.key === "Escape" && menu.current?.open) {
      menu.current.open = false;
      menu.current.querySelector("summary")?.focus();
    }
  }}>
    <summary aria-label="账户菜单" title={account.nickname || account.email || "账户菜单"}>
      <AccountAvatar key={account.avatar_url} account={account} />
    </summary>
    <section className="account-menu-content" aria-label="当前账户">
      {account.nickname && <strong>{account.nickname}</strong>}
      <span>{account.email}</span>
      <a href="#/account" onClick={() => { if (menu.current) menu.current.open = false; }}>个人中心</a>
      <button type="button" disabled={busy} onClick={() => void logout()}>
        {busy ? "正在退出…" : "退出登录"}
      </button>
      {error && <p className="account-error" role="alert">{error}</p>}
    </section>
  </details></>;
}
