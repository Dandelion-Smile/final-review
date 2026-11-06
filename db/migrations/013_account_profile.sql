alter table public.app_users
  add column nickname text check (char_length(nickname) between 1 and 40),
  add column avatar_key uuid,
  add column avatar_image bytea check (octet_length(avatar_image) <= 262144),
  add column email_verified_at timestamptz,
  add constraint app_users_avatar_complete check ((avatar_key is null) = (avatar_image is null));

create table public.auth_email_challenges (
  id uuid primary key,
  purpose text not null check (purpose in (
    'register', 'reset_password', 'change_password', 'verify_email',
    'change_email_old', 'change_email_new'
  )),
  email text not null,
  user_id uuid references public.app_users(id) on delete cascade,
  session_hash text,
  request_ip inet,
  code_digest text not null,
  attempts smallint not null default 0 check (attempts between 0 and 5),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  proof_digest text unique,
  proof_expires_at timestamptz,
  consumed_at timestamptz
);

create index auth_email_challenges_email_idx
  on public.auth_email_challenges (email, created_at desc);
create index auth_email_challenges_ip_idx
  on public.auth_email_challenges (request_ip, created_at desc);
create index auth_email_challenges_created_idx on public.auth_email_challenges (created_at);
create index auth_email_challenges_user_idx on public.auth_email_challenges (user_id);
