import { useEffect, useState, type FormEvent } from "react";
import { EmailCodeForm, NewPasswordForm } from "./AccountForms";
import AuthStudyScene from "./AuthStudyScene";
import { errorMessage } from "./account-api";
import NavigationIcon, { type NavigationIconName } from "./NavigationIcon";
import { api } from "./workbench-api";
import "./auth-page.css";

export type AuthPageMode = "login" | "register" | "forgot-password";
const title = { login: "登录", register: "注册账号", "forgot-password": "找回密码" };
const features: [NavigationIconName, string][] = [
  ["materials", "资料管理"], ["notes", "笔记整理"], ["quiz", "测试练习"],
];

export function authRoute(mode: AuthPageMode, next: string) {
  return `#/${mode}${next ? `?next=${encodeURIComponent(next)}` : ""}`;
}

export function readAuthRoute(hash: string): { mode: AuthPageMode; next: string } | null {
  const [path, query] = hash.split("?", 2);
  const mode = path.slice(2);
  if (!path.startsWith("#/") || !(mode === "login" || mode === "register" || mode === "forgot-password")) {
    return null;
  }
  const next = new URLSearchParams(query).get("next") ?? "";
  const pointsToAuth = /^#\/(login|register|forgot-password)(\?|$)/.test(next);
  return { mode, next: next.startsWith("#") && !pointsToAuth ? next : "" };
}

export default function AuthPage({ mode, next, initialEmail, notice, startupError, onLogin, onComplete }: {
  mode: AuthPageMode;
  next: string;
  initialEmail: string;
  notice: string;
  startupError: string;
  onLogin: () => Promise<void>;
  onComplete: (email: string, message: string) => void;
}) {
  const [email, setEmail] = useState(initialEmail);
  const [emailSelected, setEmailSelected] = useState(false);
  const [proof, setProof] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const isLogin = mode === "login";
  const isRegistration = mode === "register";

  useEffect(() => {
    const previous = document.title;
    document.title = `${title[mode]} · Final Review`;
    return () => { document.title = previous; };
  }, [mode]);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/api/auth/sign-in", "POST", { email, password });
      await onLogin();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  function chooseEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setEmail(email.trim());
    setEmailSelected(true);
  }

  async function savePassword(value: string) {
    const path = isRegistration ? "sign-up" : "reset-password";
    await api(`/api/auth/${path}`, "POST", { email, password: value, proof });
    onComplete(email, isRegistration ? "账号已创建，请使用邮箱和密码登录。" : "密码已重置，请使用新密码登录。");
  }

  return <main className="auth-page auth-study">
    <AuthStudyScene />
    <header className="auth-brand"><div><NavigationIcon name="courses" />Final Review</div></header>
    <section className="auth-panel" aria-labelledby="auth-heading">
      <svg className="auth-panel-paper" viewBox="0 0 568 686" preserveAspectRatio="none" aria-hidden="true" focusable="false">
        <path d="M370 0H568V129C491 103 476 67 439 39C414 20 390 10 370 0Z" />
        <path d="M0 607C96 609 105 664 203 657C294 650 321 654 371 686H0Z" />
      </svg>
      <div className="auth-panel-inner">
        {!isLogin && <a className="account-text-link auth-back" href={authRoute("login", next)}>← 返回登录</a>}
        <h1 id="auth-heading">{title[mode]}{isLogin && <span aria-hidden="true"> Final Review</span>}</h1>
        <p className="auth-description">
          {isLogin ? "使用邮箱和密码进入你的复习工作台" : proof
            ? "邮箱已验证。请设置登录密码。"
            : isRegistration ? "验证邮箱后，设置你的登录密码。" : "使用绑定邮箱验证身份，然后设置新密码。"}
        </p>
        {notice && isLogin && <p className="account-notice" role="status">{notice}</p>}
        {startupError && isLogin && <p className="account-error" role="alert">{startupError}</p>}
        {isLogin ? <>
          <form className="account-form auth-login-form" onSubmit={login}>
            <div className="auth-field">
              <label htmlFor="login-email" className="auth-field-label">邮箱</label>
              <svg className="auth-field-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <rect x="2.5" y="4.5" width="19" height="15" rx="2" /><path d="m3.5 6 8.5 7 8.5-7" />
              </svg>
              <input id="login-email" name="email" type="email" autoComplete="username" maxLength={320}
                value={email} onChange={event => setEmail(event.target.value)} placeholder="输入邮箱地址" required />
            </div>
            <div className="auth-field auth-password">
              <label htmlFor="login-password" className="auth-field-label">密码</label>
              <svg className="auth-field-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <rect x="4" y="9.5" width="16" height="12" rx="2" /><path d="M7.5 9.5v-3a4.5 4.5 0 0 1 9 0v3m-4.5 5v2" />
              </svg>
              <input id="login-password" name="password" type={showPassword ? "text" : "password"}
                autoComplete="current-password" maxLength={128} value={password}
                onChange={event => setPassword(event.target.value)} placeholder="输入登录密码" required />
              <button type="button" aria-label={showPassword ? "隐藏密码" : "显示密码"} aria-controls="login-password"
                onClick={() => setShowPassword(value => !value)}>
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                  {showPassword ? <>
                    <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z" /><circle cx="12" cy="12" r="2.5" />
                  </> : <path d="M3 3l18 18M10 6.2c.65-.13 1.32-.2 2-.2 6.5 0 10 6 10 6a16 16 0 0 1-2.2 2.8M6 7.7A16 16 0 0 0 2 12s3.5 6 10 6c1.75 0 3.28-.43 4.6-1.08m-7-7a3.4 3.4 0 0 0 4.8 4.8" />}
                </svg>
              </button>
            </div>
            <a className="account-text-link auth-forgot" href={authRoute("forgot-password", next)}>忘记密码？</a>
            {error && <p className="account-error" role="alert">{error}</p>}
            <button className="account-primary" type="submit" disabled={busy}>{busy ? "正在登录…" : "登录"}</button>
          </form>
          <div className="auth-footer"><span>还没有账号？ <a className="account-text-link" href={authRoute("register", next)}>立即注册</a></span></div>
        </> : !emailSelected ? <form className="account-form" onSubmit={chooseEmail}>
          <label htmlFor="verification-email">邮箱</label>
          <input id="verification-email" name="email" type="email" autoComplete="email" maxLength={320}
            value={email} onChange={event => setEmail(event.target.value)} placeholder="输入邮箱地址" required />
          <button className="account-primary" type="submit">继续</button>
        </form> : proof ? <NewPasswordForm submitLabel={isRegistration ? "创建账号" : "重置密码"} onSave={savePassword} />
          : <EmailCodeForm purpose={isRegistration ? "register" : "reset_password"} email={email} target={email} onVerified={setProof} />}
        {!isLogin && emailSelected && <button type="button" className="account-text-link auth-edit-email"
          onClick={() => { setEmailSelected(false); setProof(""); }}>重新填写邮箱</button>}
      </div>
    </section>
    <section className="auth-cover" aria-labelledby="auth-cover-heading">
      <h2 id="auth-cover-heading">你的复习工作<span className="auth-cover-title-end">台<svg viewBox="0 0 120 18" aria-hidden="true" focusable="false">
        <path d="M3 14C33 4 86 2 117 8" />
      </svg></span></h2>
      <p>整理课程资料、回看笔记、用测试检验复习进度，<br />让期末复习更高效、更有规划。</p>
      <ul className="auth-features">
        {features.map(([icon, label]) => <li key={icon}>
          {icon === "quiz" ? <svg className="navigation-icon" viewBox="0 0 32 32" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
            <rect x="3" y="3" width="26" height="26" rx="2" /><path d="m10 16 4 4 8-9" />
          </svg> : <NavigationIcon name={icon} />}
          {label}
        </li>)}
      </ul>
    </section>
  </main>;
}
