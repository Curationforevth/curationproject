-- user_books.feedback_embedding 컬럼 추가.
--
-- feedback.py(쓰기) + home.py / recommend.py(읽기) 가 user_books.feedback_embedding
-- 을 참조하는데 이 컬럼을 만드는 DDL 이 한 번도 작성된 적이 없었다(스키마 드리프트).
-- 결과: /home 은 항상 feedback_embedding 을 select 하므로 전 유저 500,
-- Tier 2 /recommend 와 리뷰 동반 /feedback 도 500. 인증 버그(ES256)가 가려두다가
-- 인증 수정 후 드러남.
--
-- 타입 jsonb: 서버는 feedback_embedding 으로 DB-side 벡터연산을 하지 않고
-- numpy 로 읽어 처리한다(engine/utils.to_np 가 list/문자열 모두 파싱). feedback.py
-- 는 list[float] 를 그대로 upsert → jsonb 가 기존 read/write 코드와 무변경 호환되는
-- 최저위험 타입이다. nullable 추가라 테이블 재작성 없이 비잠금으로 적용된다.
ALTER TABLE public.user_books
  ADD COLUMN IF NOT EXISTS feedback_embedding jsonb;
