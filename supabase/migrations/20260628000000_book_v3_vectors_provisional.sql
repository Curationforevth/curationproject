-- 유저가 추가한 책을 가용 텍스트(카카오 contents 등)로 임시 임베딩한 경우 TRUE.
-- 후속 보강 배치가 rich_description 확보 후 재임베딩할 대상을 식별한다.
ALTER TABLE book_v3_vectors
  ADD COLUMN IF NOT EXISTS provisional BOOLEAN NOT NULL DEFAULT FALSE;
