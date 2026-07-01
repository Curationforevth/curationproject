-- 화차 중복 정리: 추천/홈에서 담을 때 registerBook 이 book.id 를 무시하고 만든
-- null-isbn thin 복제행(Y)을 정본 isbn'd 카탈로그 행(X)으로 되돌린다.
--
-- 배경: RecommendedBook.toBook()/HomeBook.toBook() 은 기존 books.id 는 싣지만
-- isbn·source 는 null 로 둔다. 구(舊) registerBook 은 isbn 만 봐서 insert 경로로
-- 새 UUID 의 null-isbn 복제행을 만들었다. 코드는 book.id 재사용으로 이미 근본 수정됨
-- (book_registration_service.dart resolveBookRef). 이 마이그레이션은 이미 쌓인 데이터를 정리한다.
--
-- 설계 주의:
-- * DELETE FROM books 는 하지 않는다 — Y 행은 book_v3_vectors.book_id(RESTRICT FK) 에
--   존재해 삭제가 중단된다. 대신 user_books 를 정본 X 로 repoint 하고 Y 를 canonical 로 마킹해
--   serving dedup(engine/dedup.py)·dedup RPC 가 접게 한다. 물리 삭제/벡터 정리는 scripts/dedup_books.py 몫.
-- * 매칭은 btrim(title)+coalesce(author,'') exact. 각 Y 는 유일한 isbn'd twin 을 가짐(검증됨).
-- * repoint 는 book_id 만 UPDATE → rating/review_text/status/shelf_order/feedback_embedding 보존.
--   status/rating 미변경이라 CHECK·audit 트리거 무발화. user_books_state_sync 는 발화(의도된 taste 재계산).
-- * idempotent: WHERE 절이 null-isbn/null-source 로 self-limit 되어 재실행 시 no-op.

-- 1) repoint: null-isbn 서재책(Y) → isbn'd 정본(X). 유저가 X 를 아직 안 가진 경우만.
UPDATE public.user_books ub
SET book_id = x.id
FROM public.books y,
LATERAL (
  SELECT b.id
  FROM public.books b
  WHERE b.isbn IS NOT NULL
    AND btrim(b.title) = btrim(y.title)
    AND coalesce(b.author, '') = coalesce(y.author, '')
  ORDER BY (b.source = 'aladin') DESC,
           (b.source = 'data4library') DESC,
           (b.source = 'kakao') DESC,
           b.created_at
  LIMIT 1
) x
WHERE ub.book_id = y.id
  AND y.isbn IS NULL
  AND y.source IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM public.user_books u2
    WHERE u2.user_id = ub.user_id AND u2.book_id = x.id
  );

-- 2) 충돌(화차): 유저가 정본 X 를 이미 가짐 → 남은 null-Y user_book 삭제.
DELETE FROM public.user_books ub
USING public.books y
WHERE ub.book_id = y.id
  AND y.isbn IS NULL
  AND y.source IS NULL
  AND EXISTS (
    SELECT 1 FROM public.books x
    WHERE x.isbn IS NOT NULL
      AND btrim(x.title) = btrim(y.title)
      AND coalesce(x.author, '') = coalesce(y.author, '')
      AND EXISTS (
        SELECT 1 FROM public.user_books u2
        WHERE u2.user_id = ub.user_id AND u2.book_id = x.id
      )
  );

-- 3) Y 를 X 의 dup 으로 canonical 마킹 → serving dedup·dedup RPC 가 정본으로 해소.
--    UPDATE 타깃(books y)은 FROM-절 LATERAL 이 참조할 수 없으므로, 별칭 y2 로 twin 을
--    먼저 계산한 서브쿼리(sub)를 만들어 id 로 조인한다.
UPDATE public.books y
SET canonical_book_id = sub.x_id
FROM (
  SELECT y2.id AS y_id,
         (SELECT b.id
          FROM public.books b
          WHERE b.isbn IS NOT NULL
            AND btrim(b.title) = btrim(y2.title)
            AND coalesce(b.author, '') = coalesce(y2.author, '')
          ORDER BY (b.source = 'aladin') DESC,
                   (b.source = 'data4library') DESC,
                   (b.source = 'kakao') DESC,
                   b.created_at
          LIMIT 1) AS x_id
  FROM public.books y2
  WHERE y2.isbn IS NULL AND y2.source IS NULL AND y2.canonical_book_id IS NULL
) sub
WHERE y.id = sub.y_id
  AND sub.x_id IS NOT NULL;
