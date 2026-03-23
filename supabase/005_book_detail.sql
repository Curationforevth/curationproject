-- supabase/005_book_detail.sql
-- 책 상세 화면: 호오 평가 + 감성태그 + 리뷰 텍스트

-- 1. user_books 컬럼 추가
ALTER TABLE public.user_books ADD COLUMN IF NOT EXISTS rating text DEFAULT NULL;
ALTER TABLE public.user_books ADD COLUMN IF NOT EXISTS emotion_tags jsonb DEFAULT NULL;
ALTER TABLE public.user_books ADD COLUMN IF NOT EXISTS review_text text DEFAULT NULL;

-- rating 값 제약
ALTER TABLE public.user_books ADD CONSTRAINT user_books_rating_check
  CHECK (rating IS NULL OR rating IN ('good', 'neutral', 'bad'));

-- 2. 감성태그 옵션 테이블
CREATE TABLE IF NOT EXISTS public.emotion_tag_options (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  label text NOT NULL,
  sort_order int NOT NULL DEFAULT 0,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE public.emotion_tag_options ENABLE ROW LEVEL SECURITY;

CREATE POLICY "누구나 감성태그 옵션 조회"
  ON public.emotion_tag_options FOR SELECT
  USING (true);

-- 3. 리플렉션 질문 테이블
CREATE TABLE IF NOT EXISTS public.reflection_prompts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  question text NOT NULL,
  category text DEFAULT NULL,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE public.reflection_prompts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "누구나 리플렉션 질문 조회"
  ON public.reflection_prompts FOR SELECT
  USING (true);

-- 4. 시드 데이터: 감성태그 옵션
INSERT INTO public.emotion_tag_options (label, sort_order) VALUES
  ('잔잔한', 1),
  ('따뜻한', 2),
  ('긴장감', 3),
  ('몰입', 4),
  ('여운', 5),
  ('유쾌한', 6),
  ('무거운', 7),
  ('서정적', 8),
  ('속도감', 9),
  ('생각할거리', 10);

-- 5. 시드 데이터: 리플렉션 질문
INSERT INTO public.reflection_prompts (question, category) VALUES
  ('가장 기억에 남는 장면이 있나요?', NULL),
  ('이 책을 읽고 떠오른 생각이나 감정이 있다면?', NULL),
  ('누군가에게 이 책을 추천한다면 어떻게 소개할 것 같나요?', NULL),
  ('주인공의 어떤 선택이 인상적이었나요?', 'character'),
  ('마음에 드는 캐릭터가 있었나요?', 'character'),
  ('이 책의 문장이 어떻게 느껴졌나요?', 'writing_style'),
  ('특별히 좋았던 문장이나 표현이 있나요?', 'writing_style'),
  ('이야기의 전개가 어떻게 느껴졌나요?', 'plot'),
  ('예상치 못한 전개가 있었나요?', 'plot'),
  ('이 책이 그리는 세계가 어떻게 느껴졌나요?', 'worldbuilding'),
  ('이 책의 분위기를 한 단어로 표현한다면?', 'atmosphere'),
  ('이 책이 전하는 메시지가 있다면 무엇일까요?', 'message');

-- 6. user_books UPDATE 정책 (rating, emotion_tags, review_text 수정 허용)
CREATE POLICY "유저가 자신의 user_books 업데이트"
  ON public.user_books FOR UPDATE
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);
