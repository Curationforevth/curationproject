-- 추천 화면 행동 신호 테이블 (설계: docs/superpowers/specs/2026-07-02-shelf-remove-not-interested-design.md)
--
-- "관심 없어요" 같은 신호는 서재(user_books)가 아니라 여기에 둔다 — 서재는
-- "내가 꽂은 책"의 감성 공간(핵심가치 1)이라 숨김용 행으로 오염시키지 않는다.
-- 노출 로그(book_impressions)와도 분리: 노출 없이도 마킹 가능해야 하고,
-- 취향 정본은 컴팩트해야 재계산 읽기가 싸다.
--
-- v1 signal 값은 'not_interested' 하나. 추후 신호가 늘 수 있어 CHECK 로 좁혀두고
-- 마이그레이션으로 확장한다. wishlist 긍정 신호는 user_books.status 가 정본이라
-- 여기 저장하지 않는다.

CREATE TABLE IF NOT EXISTS public.user_book_signals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  book_id uuid NOT NULL REFERENCES public.books(id) ON DELETE CASCADE,
  signal text NOT NULL CHECK (signal IN ('not_interested')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, book_id, signal)
);

-- 재계산/서빙이 유저 단위로 전량 읽는다 (유저당 수십 행 수준).
CREATE INDEX IF NOT EXISTS idx_user_book_signals_user
  ON public.user_book_signals (user_id);

ALTER TABLE public.user_book_signals ENABLE ROW LEVEL SECURITY;

-- 앱은 본인 행만 읽고/쓰고/지운다 (서버는 service_role 로 RLS 우회).
DROP POLICY IF EXISTS user_book_signals_select_own ON public.user_book_signals;
CREATE POLICY user_book_signals_select_own ON public.user_book_signals
  FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS user_book_signals_insert_own ON public.user_book_signals;
CREATE POLICY user_book_signals_insert_own ON public.user_book_signals
  FOR INSERT WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS user_book_signals_delete_own ON public.user_book_signals;
CREATE POLICY user_book_signals_delete_own ON public.user_book_signals
  FOR DELETE USING (auth.uid() = user_id);
