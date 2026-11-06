import { api, apiResponse } from "./workbench-api";

export type Account = {
  id: string;
  email: string | null;
  local: boolean;
  nickname: string | null;
  avatar_url: string | null;
  email_verified: boolean;
  deletion_due_at?: string | null;
};

export type EmailPurpose =
  | "register" | "reset_password" | "change_password" | "verify_email"
  | "change_email_old" | "change_email_new" | "delete_account" | "cancel_deletion";

function codeBase(purpose: EmailPurpose) {
  return purpose === "register" || purpose === "reset_password" ? "/api/auth" : "/api/account";
}

export function sendEmailCode(purpose: EmailPurpose, email?: string, oldProof?: string) {
  if (purpose === "cancel_deletion") {
    return api<{ challenge_id: string; retry_after: number }>("/api/account/recovery/code", "POST");
  }
  return api<{ challenge_id: string; retry_after: number }>(
    `${codeBase(purpose)}/email-code`, "POST", { purpose, email, old_proof: oldProof },
  );
}

export function verifyEmailCode(purpose: EmailPurpose, challengeId: string, code: string) {
  if (purpose === "cancel_deletion") {
    return api<{ proof: string }>("/api/account/recovery/verify", "POST", { challenge_id: challengeId, code });
  }
  return api<{ proof: string }>(
    `${codeBase(purpose)}/verify-code`, "POST", { purpose, challenge_id: challengeId, code },
  );
}

export async function uploadAvatar(file: File) {
  const path = "/api/account/avatar";
  const form = new FormData();
  form.append("file", file);
  return apiResponse<Account>(
    await fetch(path, { method: "PUT", credentials: "same-origin", body: form }), path,
  );
}

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "请求失败，请稍后重试";
}
