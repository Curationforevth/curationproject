-- =============================================
-- Curation Project — MVP 초기 스키마
-- Supabase SQL Editor에서 실행
-- =============================================

-- pgvector 확장 활성화 (임베딩용, Phase 2~3 대비)
create extension if not exists vector;

-- =============================================
-- 1. users — 유저 프로필
-- =============================================
create table public.users (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  nickname text,
  avatar_url text,
  created_at timestamptz default now()
);

alter table public.users enable row level security;

create policy "유저는 본인 데이터만 조회" on public.users
  for select using (auth.uid() = id);

create policy "유저는 본인 데이터만 수정" on public.users
  for update using (auth.uid() = id);

create policy "회원가입 시 본인 프로필 생성" on public.users
  for insert with check (auth.uid() = id);

-- =============================================
-- 2. books — 책 정보 (카카오/알라딘에서 가져온 캐싱 데이터)
-- =============================================
create table public.books (
  id uuid primary key default gen_random_uuid(),
  isbn text unique,
  title text not null,
  author text,
  publisher text,
  cover_url text,
  page_count int,
  description text,
  genre text,
  source text check (source in ('kakao', 'aladin')),
  source_id text,
  created_at timestamptz default now()
);

alter table public.books enable row level security;

create policy "모든 유저가 책 조회 가능" on public.books
  for select using (true);

create policy "인증된 유저가 책 추가 가능" on public.books
  for insert with check (auth.role() = 'authenticated');

-- =============================================
-- 3. user_books — 유저의 서재
-- =============================================
create table public.user_books (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  book_id uuid not null references public.books(id) on delete cascade,
  status text not null default 'read' check (status in ('read', 'reading', 'want_to_read')),
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  unique(user_id, book_id)
);

alter table public.user_books enable row level security;

create policy "유저는 본인 서재만 조회" on public.user_books
  for select using (auth.uid() = user_id);

create policy "유저는 본인 서재에 추가" on public.user_books
  for insert with check (auth.uid() = user_id);

create policy "유저는 본인 서재만 수정" on public.user_books
  for update using (auth.uid() = user_id);

create policy "유저는 본인 서재에서 삭제" on public.user_books
  for delete using (auth.uid() = user_id);

-- =============================================
-- 4. feedbacks — 책에 대한 피드백
-- =============================================
create table public.feedbacks (
  id uuid primary key default gen_random_uuid(),
  user_book_id uuid not null references public.user_books(id) on delete cascade,
  category text not null check (category in ('character', 'writing_style', 'worldbuilding', 'plot', 'message', 'atmosphere')),
  sentiment text not null check (sentiment in ('positive', 'negative')),
  free_text text,
  created_at timestamptz default now()
);

alter table public.feedbacks enable row level security;

create policy "유저는 본인 피드백만 조회" on public.feedbacks
  for select using (
    auth.uid() = (select user_id from public.user_books where id = user_book_id)
  );

create policy "유저는 본인 피드백 추가" on public.feedbacks
  for insert with check (
    auth.uid() = (select user_id from public.user_books where id = user_book_id)
  );

create policy "유저는 본인 피드백 수정" on public.feedbacks
  for update using (
    auth.uid() = (select user_id from public.user_books where id = user_book_id)
  );

create policy "유저는 본인 피드백 삭제" on public.feedbacks
  for delete using (
    auth.uid() = (select user_id from public.user_books where id = user_book_id)
  );

-- =============================================
-- 5. book_embeddings — 책 벡터 (Phase 2~3)
-- =============================================
create table public.book_embeddings (
  id uuid primary key default gen_random_uuid(),
  book_id uuid not null references public.books(id) on delete cascade unique,
  embedding vector(1536),
  created_at timestamptz default now()
);

alter table public.book_embeddings enable row level security;

create policy "모든 유저가 책 임베딩 조회 가능" on public.book_embeddings
  for select using (true);

-- =============================================
-- 6. user_taste_vectors — 유저 취향 벡터 (Phase 2~3)
-- =============================================
create table public.user_taste_vectors (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  cluster_label text,
  vector vector(1536),
  updated_at timestamptz default now()
);

alter table public.user_taste_vectors enable row level security;

create policy "유저는 본인 취향 벡터만 조회" on public.user_taste_vectors
  for select using (auth.uid() = user_id);

-- =============================================
-- 인덱스
-- =============================================
create index idx_books_isbn on public.books(isbn);
create index idx_user_books_user_id on public.user_books(user_id);
create index idx_user_books_book_id on public.user_books(book_id);
create index idx_feedbacks_user_book_id on public.feedbacks(user_book_id);
create index idx_user_taste_vectors_user_id on public.user_taste_vectors(user_id);

-- =============================================
-- 자동 updated_at 트리거
-- =============================================
create or replace function public.handle_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create trigger on_user_books_updated
  before update on public.user_books
  for each row execute function public.handle_updated_at();

-- =============================================
-- 회원가입 시 자동 프로필 생성 트리거
-- =============================================
create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.users (id, email)
  values (new.id, new.email);
  return new;
end;
$$ language plpgsql security definer;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
