import { useState, type FormEvent } from "react";
import { EmailCodeForm, NewPasswordForm } from "./AccountForms";
import { AccountAvatar } from "./AccountMenu";
import AccountDeletion from "./AccountDeletion";
import { errorMessage, uploadAvatar, type Account } from "./account-api";
import { api } from "./workbench-api";

type SecurityAction = "password" | "email" | "verify";

export default function AccountCenter({ account, onUpdate, onReauthenticate }: {
  account: Account;
  onUpdate: (updated: Account) => void;
  onReauthenticate: (email: string, notice: string) => void;
}) {
  const [nickname, setNickname] = useState(account.nickname ?? "");
  const [editing, setEditing] = useState<SecurityAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function update(action: () => Promise<Account>, message: string) {
    setBusy(true);
    setNotice("");
    setError("");
    try {
      const updated = await action();
      onUpdate(updated);
      setNotice(message);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  function saveNickname(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void update(() => api<Account>("/api/account/profile", "PATCH", { nickname: nickname.trim() }), "昵称已保存。");
  }

  return <section className="account-center" aria-labelledby="account-heading">
    <div className="account-center-heading">
      <h1 id="account-heading">个人中心</h1>
      <p>管理个人资料、绑定邮箱和登录密码。</p>
    </div>
    <div className="account-center-grid">
      <section className="account-card" aria-labelledby="profile-heading">
        <h2 id="profile-heading">个人资料</h2>
        <div className="account-avatar-row">
          <AccountAvatar key={account.avatar_url} account={account} />
          <div>
            <label className="account-secondary account-upload" htmlFor="profile-avatar">更换头像</label>
            <input id="profile-avatar" className="account-file" type="file" accept="image/jpeg,image/png,image/webp"
              disabled={busy} onChange={event => {
                const image = event.target.files?.[0];
                event.target.value = "";
                if (image) void update(() => uploadAvatar(image), "头像已更新。");
              }} />
            <p className="account-hint">支持 JPG、PNG、WebP，最大 2 MB。</p>
          </div>
        </div>
        <form className="account-form" onSubmit={saveNickname}>
          <label htmlFor="profile-nickname">昵称</label>
          <input id="profile-nickname" name="nickname" value={nickname} maxLength={40}
            onChange={event => setNickname(event.target.value)} placeholder="输入你的昵称" required />
          <button className="account-primary" type="submit"
            disabled={busy || !nickname.trim() || nickname.trim() === account.nickname}>保存昵称</button>
        </form>
        {notice && <p className="account-notice" role="status">{notice}</p>}
        {error && <p className="account-error" role="alert">{error}</p>}
      </section>
      <section className="account-card" aria-labelledby="security-heading">
        <h2 id="security-heading">账户安全</h2>
        {account.local ? <p className="account-hint">登录正式账号后可管理邮箱和密码。</p> : <>
          <div className="account-security-row">
            <div><h3>绑定邮箱</h3><p>{account.email}</p>
              <span className={account.email_verified ? "account-verified" : "account-unverified"}>
                {account.email_verified ? "已验证" : "待验证"}
              </span>
            </div>
            <div className="account-security-actions">
              {!account.email_verified && <button type="button" className="account-secondary" onClick={() => setEditing("verify")}>验证邮箱</button>}
              <button type="button" className="account-secondary" onClick={() => setEditing("email")}>更换邮箱</button>
            </div>
          </div>
          <div className="account-security-row">
            <div><h3>登录密码</h3><p>通过绑定邮箱验证码修改密码。</p></div>
            <button type="button" className="account-secondary" onClick={() => setEditing("password")}>修改密码</button>
          </div>
          {editing && <SecurityForm key={editing} action={editing} email={account.email ?? ""}
            onCancel={() => setEditing(null)} onReauthenticate={onReauthenticate}
            onVerified={updated => { onUpdate(updated); setEditing(null); }} />}
        </>}
      </section>
    </div>
    {!account.local && <AccountDeletion email={account.email ?? ""} onReauthenticate={onReauthenticate} />}
  </section>;
}

function SecurityForm({ action, email, onCancel, onVerified, onReauthenticate }: {
  action: SecurityAction;
  email: string;
  onCancel: () => void;
  onVerified: (account: Account) => void;
  onReauthenticate: (email: string, notice: string) => void;
}) {
  const [proof, setProof] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [emailSelected, setEmailSelected] = useState(false);
  const title = action === "password" ? "修改密码" : action === "email" ? "更换邮箱" : "验证绑定邮箱";

  async function completeVerification(value: string) {
    if (action === "verify") {
      onVerified(await api<Account>("/api/account/verify-email", "POST", { proof: value }));
    } else {
      setProof(value);
    }
  }

  async function changePassword(password: string) {
    await api("/api/account/password", "POST", { password, proof });
    onReauthenticate(email, "密码已更新，请重新登录。");
  }

  async function changeEmail(newProof: string) {
    const result = await api<{ email: string }>("/api/account/email", "POST", { old_proof: proof, new_proof: newProof });
    onReauthenticate(result.email, "邮箱已更换，请使用新邮箱登录。");
  }

  return <section className="account-security-form" aria-label={title}>
    <div className="account-security-heading"><h3>{title}</h3>
      <button className="account-text-link" type="button" onClick={onCancel}>取消</button></div>
    {!proof ? <EmailCodeForm purpose={action === "password" ? "change_password" : action === "email" ? "change_email_old" : "verify_email"}
      target={email} onVerified={completeVerification} />
      : action === "password" ? <NewPasswordForm onSave={changePassword} submitLabel="保存新密码" />
      : !emailSelected ? <form className="account-form" onSubmit={event => { event.preventDefault(); setEmailSelected(true); }}>
        <label htmlFor="account-new-email">新邮箱</label>
        <input id="account-new-email" type="email" autoComplete="email" maxLength={320}
          value={newEmail} onChange={event => setNewEmail(event.target.value)} required />
        <button className="account-primary" type="submit">继续验证新邮箱</button>
      </form> : <>
        <EmailCodeForm purpose="change_email_new" key="change-email-new" email={newEmail} target={newEmail}
          oldProof={proof} onVerified={changeEmail} />
        <button className="account-text-link auth-edit-email" type="button" onClick={() => setEmailSelected(false)}>重新填写新邮箱</button>
      </>}
  </section>;
}
