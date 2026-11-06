import { useState, type FormEvent } from "react";
import { EmailCodeForm } from "./AccountForms";
import { errorMessage, type Account } from "./account-api";
import { api } from "./workbench-api";

type Impact = { courses: number; materials: number; notes: number; quizzes: number; attempts: number };
type Preview = { grace_days: number; impact: Impact };
const labels: Record<keyof Impact, string> = { courses: "课程", materials: "资料", notes: "笔记", quizzes: "试卷", attempts: "作答记录" };
const deadlineFormat = new Intl.DateTimeFormat("zh-CN", {
  dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Shanghai",
});

export default function AccountDeletion({ email, onReauthenticate }: {
  email: string; onReauthenticate: (email: string, notice: string) => void;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [proof, setProof] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function showImpact() {
    setBusy(true);
    setError("");
    try {
      setPreview(await api<Preview>("/api/account/deletion"));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function request(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/api/account/deletion", "POST", { proof, confirmation });
      onReauthenticate(email, "注销申请已提交。恢复期内可登录并验证邮箱，撤销注销。");
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return <section className="account-card account-deletion" aria-labelledby="deletion-heading">
    <div className="account-security-heading">
      <div><h2 id="deletion-heading">注销账号</h2>
        <p className="account-hint">查看数据影响，并通过绑定邮箱验证后申请注销。</p></div>
      {!preview && <button className="account-secondary account-danger-outline" type="button" onClick={() => void showImpact()} disabled={busy}>申请注销</button>}
      {preview && <button className="account-text-link" type="button" disabled={busy} onClick={() => {
        setPreview(null); setProof(""); setConfirmation(""); setError("");
      }}>返回</button>}
    </div>
    {preview && <>
      <ul className="account-impact">{(Object.keys(labels) as (keyof Impact)[]).map(key =>
        <li key={key}><strong>{preview.impact[key]}</strong><span>{labels[key]}</span></li>
      )}</ul>
      <p className="account-hint">申请后进入 {preview.grace_days} 天恢复期。期间暂停学习功能，可登录并验证邮箱撤销。到期后，账号、个人资料、学习记录和关联文件将永久删除。</p>
      <div className="account-deletion-form">
        {proof ? <form className="account-form" onSubmit={request}>
          <label htmlFor="confirm-account-deletion">请输入「注销账号」确认</label>
          <input id="confirm-account-deletion" value={confirmation} onChange={event => setConfirmation(event.target.value)} autoComplete="off" />
          <button className="account-primary account-danger" type="submit" disabled={busy || confirmation !== "注销账号"}>确认申请注销</button>
        </form> : <EmailCodeForm purpose="delete_account" target={email} onVerified={setProof} />}
      </div>
    </>}
    {error && <p className="account-error" role="alert">{error}</p>}
  </section>;
}

export function AccountRecovery({ account, onReauthenticate, signOut }: {
  account: Account; onReauthenticate: (email: string, notice: string) => void;
  signOut: () => Promise<void>;
}) {
  const [error, setError] = useState("");
  async function cancel(proof: string) {
    await api("/api/account/recovery/cancel", "POST", { proof });
    onReauthenticate(account.email ?? "", "已撤销注销，请重新登录恢复使用。");
  }
  return <main className="auth-page account-recovery">
    <section className="account-card account-recovery-card" aria-labelledby="recovery-heading">
      <p className="account-recovery-label">Final Review</p>
      <h1 id="recovery-heading">账号处于注销恢复期</h1>
      <p className="account-hint">计划清理时间 <strong>{deadlineFormat.format(new Date(account.deletion_due_at!))}</strong>（北京时间）。</p>
      <p className="account-hint">恢复期内，验证绑定邮箱即可撤销注销。</p>
      <EmailCodeForm purpose="cancel_deletion" target={account.email ?? ""} onVerified={cancel}
        verifyLabel="验证并撤销注销" />
      <button className="account-text-link auth-edit-email" type="button" onClick={() => void signOut().catch(reason => setError(errorMessage(reason)))}>退出登录</button>
      {error && <p className="account-error" role="alert">{error}</p>}
    </section>
  </main>;
}
