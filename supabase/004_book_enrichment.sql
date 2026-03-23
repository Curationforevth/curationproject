-- 책 메타데이터 확장 (색상, 폰트, 무드태그) + 서가 정렬

ALTER TABLE public.books ADD COLUMN IF NOT EXISTS dominant_colors jsonb DEFAULT NULL;
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS spine_font text DEFAULT NULL;
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS mood_tags jsonb DEFAULT NULL;

ALTER TABLE public.user_books ADD COLUMN IF NOT EXISTS shelf_order int DEFAULT NULL;

-- books UPDATE RLS 정책
CREATE POLICY "인증된 유저가 책 메타데이터 업데이트"
  ON public.books FOR UPDATE
  USING (true)
  WITH CHECK (auth.role() = 'authenticated');
