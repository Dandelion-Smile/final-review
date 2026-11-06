import { useEffect, useState, type FormEvent } from "react";
import { errorMessage, sendEmailCode, verifyEmailCode, type EmailPurpose } from "./account-api";

export function EmailCodeForm({ purpose, email, target, oldProof, onVerified, verifyLabel = "验证邮箱" }: {
  purpose: EmailPurpose;
  email?: string;
  target: string;
  oldProof?: string;
  verifyLabel?: string;
  onVerified: (proof: string) => void | Promise<void>;
}) {
  const [challenge, setChallenge] = useState("");
  const [code, setCode] = useState("");
  const [resendAt, setResendAt] = useState(0);
  const [remaining, setRemaining] = useState(0);
  const [busy, setBusy] = useState<"send" | "verify" | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!resendAt) return;
    const update = () => {
      const seconds = Math.max(0, Math.ceil((resendAt - Date.now()) / 1000));
      setRemaining(seconds);
      if (!seconds) setResendAt(0);
    };
    update();
    const timer = window.setInterval(update, 500);
    return () => window.clearInterval(timer);
  }, [resendAt]);

  async function send() {
    setBusy("send");
    setError("");
    try {
      const result = await sendEmailCode(purpose, email, oldProof);
      setChallenge(result.challenge_id);
      setCode("");
      setRemaining(result.retry_after);
      setResendAt(Date.now() + result.retry_after * 1000);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(null);
    }
  }

  async function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("verify");
    setError("");
    try {
      const { proof } = await verifyEmailCode(purpose, challenge, code);
      await onVerified(proof);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(null);
    }
  }

  return <form className="account-form" onSubmit={verify}>
    <label htmlFor="account-email-code">邮箱验证码</label>
    <div className="account-input-action">
      <input id="account-email-code" name="code" inputMode="numeric" autoComplete="one-time-code"
        pattern="[0-9]{6}" maxLength={6} value={code} onChange={event => setCode(event.target.value.replace(/\D/g, ""))}
        disabled={!challenge || busy === "verify"} placeholder="输入 6 位验证码" required />
      <button className="account-secondary" type="button" disabled={busy !== null || remaining > 0} onClick={() => void send()}>
        {busy === "send" ? "正在发送…" : remaining ? `${remaining} 秒后重发` : challenge ? "重新发送" : "发送验证码"}
      </button>
    </div>
    <p className="account-hint" role={challenge ? "status" : undefined}>
      {challenge ? `如果邮箱符合条件，验证码将发送至 ${target}。请在 10 分钟内使用。` : `验证码将发送至 ${target}。`}
    </p>
    {error && <p className="account-error" role="alert">{error}</p>}
    <button className="account-primary" type="submit" disabled={!challenge || code.length !== 6 || busy !== null}>
      {busy === "verify" ? "正在验证…" : verifyLabel}
    </button>
  </form>;
}

export function NewPasswordForm({ onSave, submitLabel }: {
  onSave: (password: string) => Promise<void>;
  submitLabel: string;
}) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (password !== confirmation) {
      setError("两次输入的密码不一致。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onSave(password);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return <form className="account-form" onSubmit={save}>
    <label htmlFor="account-new-password">新密码</label>
    <input id="account-new-password" name="new-password" type="password" autoComplete="new-password"
      minLength={8} maxLength={128} value={password} onChange={event => setPassword(event.target.value)}
      placeholder="至少 8 个字符" required />
    <label htmlFor="account-confirm-password">确认新密码</label>
    <input id="account-confirm-password" type="password" autoComplete="new-password" minLength={8} maxLength={128}
      value={confirmation} onChange={event => setConfirmation(event.target.value)} required />
    {error && <p className="account-error" role="alert">{error}</p>}
    <button className="account-primary" type="submit" disabled={busy}>{busy ? "正在保存…" : submitLabel}</button>
  </form>;
}
