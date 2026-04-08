# Data Collection Phase 1A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 추천 시스템 v3가 Cold→Warm 전환을 위해 필요한 기초 데이터 수집 인프라(스키마 + 트리거 + 앱 로깅 + 집계 cron)를 구축한다.

**Architecture:** Supabase(Postgres) 마이그레이션으로 4개 스키마 변경 — (1) `user_books.status` 값 재정의 + (2) `user_books_history` 자동 audit, (3) `recommendation_impressions` 노출/액션 로그, (4) `user_state` Tier/active 캐시. Flutter 앱은 추천 카드가 렌더될 때 impressions row를 INSERT하고, 유저 액션 시 UPDATE한다. user_state는 매시간 Supabase Edge Function(pg_cron)으로 갱신.

**Tech Stack:** Supabase Postgres, plpgsql triggers, pg_cron, Flutter (Dart) + supabase_flutter, Riverpod.

**참고 spec:** `docs/superpowers/specs/2026-04-07-data-collection-design.md` §3-4, §9 (Phase 1A 항목 1-4).

**기존 스키마 메모:**
- `user_books.status` 현재 값: `'read' | 'reading' | 'want_to_read'` (default `'read'`) — `supabase/001_init_schema.sql:64`
- `user_books.rating`: `null | 'good' | 'neutral' | 'bad'` — `supabase/005_book_detail.sql:5-11`
- spec의 새 status 값: `'wishlist' | 'reading' | 'finished'` (rating은 `'neutral'` 제거, `null | 'good' | 'bad'`만 유지)
- 매핑: `read → finished`, `reading → reading`, `want_to_read → wishlist`, rating `'neutral' → null`

---

## File Structure

**Create:**
- `supabase/migrations/20260407_phase1a_status_normalize.sql` — user_books status/rating 값 정규화
- `supabase/migrations/20260407_phase1a_user_books_history.sql` — history 테이블 + trigger
- `supabase/migrations/20260407_phase1a_recommendation_impressions.sql` — 노출 로그 테이블
- `supabase/migrations/20260407_phase1a_user_state.sql` — 집계 캐시 테이블
- `supabase/migrations/20260407_phase1a_user_state_refresh.sql` — pg_cron 매시간 job
- `app/lib/core/services/impression_logger.dart` — Flutter impression 로깅 서비스
- `app/test/core/services/impression_logger_test.dart` — 유닛 테스트

**Modify:**
- `app/lib/core/services/recommendation_service.dart` — 추천 응답에서 impression 로깅 트리거
- `app/lib/features/home/widgets/book_detail_bottom_sheet.dart` — 책 상세 진입 시 click 액션 기록
- `app/lib/features/home/providers/recommendation_provider.dart` — service 주입 연결
- `app/lib/features/feedback/providers/feedback_flow_provider.dart` — 좋아요/저장 시 액션 기록
- `app/lib/features/bookshelf/providers/bookshelf_provider.dart` — status 값 변경 (`read→finished`, `want_to_read→wishlist`)

**Test (manual SQL verification):**
- `scripts/verify_phase1a.sql` — 마이그레이션 검증 쿼리 모음

---

### Task 1: user_books status/rating 값 정규화 마이그레이션

**Files:**
- Create: `supabase/migrations/20260407_phase1a_status_normalize.sql`

**Why:** spec §3.1 의 `wishlist|reading|finished` 모델로 전환. 기존 data 손실 없이 매핑.

- [ ] **Step 1: 마이그레이션 SQL 작성**

```sql
-- 20260407_phase1a_status_normalize.sql
-- user_books.status / rating 값을 v3 spec 모델로 정규화

BEGIN;

-- 1. 기존 status check constraint 제거
ALTER TABLE public.user_books DROP CONSTRAINT IF EXISTS user_books_status_check;

-- 2. 값 매핑
UPDATE public.user_books SET status = 'finished' WHERE status = 'read';
UPDATE public.user_books SET status = 'wishlist' WHERE status = 'want_to_read';
-- 'reading' 은 그대로

-- 3. 새 default + check constraint
ALTER TABLE public.user_books ALTER COLUMN status SET DEFAULT 'wishlist';
ALTER TABLE public.user_books
  ADD CONSTRAINT user_books_status_check
  CHECK (status IN ('wishlist', 'reading', 'finished'));

-- 4. rating: 'neutral' 제거 → null
UPDATE public.user_books SET rating = NULL WHERE rating = 'neutral';

ALTER TABLE public.user_books DROP CONSTRAINT IF EXISTS user_books_rating_check;
ALTER TABLE public.user_books
  ADD CONSTRAINT user_books_rating_check
  CHECK (rating IS NULL OR rating IN ('good', 'bad'));

-- 5. wishlist 행은 rating null 강제
ALTER TABLE public.user_books
  ADD CONSTRAINT user_books_wishlist_no_rating
  CHECK (status <> 'wishlist' OR rating IS NULL);

COMMIT;
```

- [ ] **Step 2: Supabase에 적용**

Run (Supabase SQL Editor 또는 CLI):
```bash
# CLI 사용 시
supabase db push
# 또는 SQL Editor 에 직접 paste 후 실행
```

Expected: `Success. No rows returned.`

- [ ] **Step 3: 검증 쿼리**

Run:
```sql
SELECT status, COUNT(*) FROM public.user_books GROUP BY status;
SELECT rating, COUNT(*) FROM public.user_books GROUP BY rating;
SELECT conname FROM pg_constraint WHERE conrelid = 'public.user_books'::regclass;
```

Expected: status는 `wishlist|reading|finished`만, rating은 `null|good|bad`만, constraints 4개(status_check, rating_check, wishlist_no_rating, pk).

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/20260407_phase1a_status_normalize.sql
git commit -m "feat: user_books status/rating v3 모델로 정규화"
```

---

### Task 2: user_books_history 테이블 + 자동 audit trigger

**Files:**
- Create: `supabase/migrations/20260407_phase1a_user_books_history.sql`

**Why:** spec §3.2.4. wishlist→bad 같은 전환 추적용. trigger로 무중단 자동 캡처.

- [ ] **Step 1: SQL 작성**

```sql
-- 20260407_phase1a_user_books_history.sql
BEGIN;

CREATE TABLE IF NOT EXISTS public.user_books_history (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID NOT NULL,
  book_id UUID NOT NULL,
  old_status TEXT,
  new_status TEXT,
  old_rating TEXT,
  new_rating TEXT,
  changed_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_user_book
  ON public.user_books_history (user_id, book_id, changed_at DESC);

-- RLS: 본인 기록만 read
ALTER TABLE public.user_books_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "유저는 본인 history만 조회" ON public.user_books_history
  FOR SELECT USING (auth.uid() = user_id);

-- trigger function: status 또는 rating 이 바뀐 경우만 기록
CREATE OR REPLACE FUNCTION public.log_user_books_change()
RETURNS TRIGGER AS $$
BEGIN
  IF (OLD.status IS DISTINCT FROM NEW.status)
     OR (OLD.rating IS DISTINCT FROM NEW.rating) THEN
    INSERT INTO public.user_books_history
      (user_id, book_id, old_status, new_status, old_rating, new_rating)
    VALUES
      (NEW.user_id, NEW.book_id, OLD.status, NEW.status, OLD.rating, NEW.rating);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS user_books_audit ON public.user_books;
CREATE TRIGGER user_books_audit
  AFTER UPDATE ON public.user_books
  FOR EACH ROW EXECUTE FUNCTION public.log_user_books_change();

-- INSERT 시에도 첫 상태 기록 (분석 일관성)
CREATE OR REPLACE FUNCTION public.log_user_books_insert()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO public.user_books_history
    (user_id, book_id, old_status, new_status, old_rating, new_rating)
  VALUES
    (NEW.user_id, NEW.book_id, NULL, NEW.status, NULL, NEW.rating);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS user_books_audit_insert ON public.user_books;
CREATE TRIGGER user_books_audit_insert
  AFTER INSERT ON public.user_books
  FOR EACH ROW EXECUTE FUNCTION public.log_user_books_insert();

COMMIT;
```

- [ ] **Step 2: 적용**

Supabase SQL Editor에 paste & run.
Expected: success.

- [ ] **Step 3: trigger 동작 검증**

Run (테스트 user_id 하나 선택해서):
```sql
-- 1. 임의 row update 트리거 확인
UPDATE public.user_books
SET rating = 'good'
WHERE id = (SELECT id FROM public.user_books LIMIT 1)
RETURNING id;

-- 2. history row 생성됐는지
SELECT * FROM public.user_books_history
ORDER BY changed_at DESC LIMIT 5;
```

Expected: history에 새 row 1개, `new_rating='good'`.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/20260407_phase1a_user_books_history.sql
git commit -m "feat: user_books_history 테이블 + audit trigger"
```

---

### Task 3: recommendation_impressions 테이블

**Files:**
- Create: `supabase/migrations/20260407_phase1a_recommendation_impressions.sql`

**Why:** spec §3.2.1. 추천 노출/액션 로그. CF 학습 기반 데이터.

- [ ] **Step 1: SQL 작성**

```sql
-- 20260407_phase1a_recommendation_impressions.sql
BEGIN;

CREATE TABLE IF NOT EXISTS public.recommendation_impressions (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  book_id UUID NOT NULL REFERENCES public.books(id) ON DELETE CASCADE,
  position INT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('home_recommend','similar','curation','search')),
  algorithm_version TEXT NOT NULL,
  shown_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
  action TEXT CHECK (action IS NULL OR action IN ('clicked','saved','liked','disliked','ignored')),
  action_at TIMESTAMPTZ,
  session_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_imp_user_time
  ON public.recommendation_impressions (user_id, shown_at DESC);
CREATE INDEX IF NOT EXISTS idx_imp_book
  ON public.recommendation_impressions (book_id);
CREATE INDEX IF NOT EXISTS idx_imp_unactioned
  ON public.recommendation_impressions (user_id, shown_at DESC)
  WHERE action IS NULL;

ALTER TABLE public.recommendation_impressions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "유저는 본인 impression INSERT" ON public.recommendation_impressions
  FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "유저는 본인 impression UPDATE" ON public.recommendation_impressions
  FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "유저는 본인 impression SELECT" ON public.recommendation_impressions
  FOR SELECT USING (auth.uid() = user_id);

COMMIT;
```

- [ ] **Step 2: 적용 + 검증**

Run in SQL Editor. Then:
```sql
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'recommendation_impressions';
SELECT indexname FROM pg_indexes WHERE tablename = 'recommendation_impressions';
```

Expected: 9 컬럼, 4 인덱스(pk + 3개).

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/20260407_phase1a_recommendation_impressions.sql
git commit -m "feat: recommendation_impressions 테이블"
```

---

### Task 4: ImpressionLogger Flutter 서비스 (테스트 먼저)

**Files:**
- Create: `app/lib/core/services/impression_logger.dart`
- Test: `app/test/core/services/impression_logger_test.dart`

**Why:** Flutter 측에서 추천 카드 노출/액션을 비동기로 로깅. 스펙 §5: 응답 경로 밖.

- [ ] **Step 1: 실패 테스트 작성**

```dart
// app/test/core/services/impression_logger_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:app/core/services/impression_logger.dart';

void main() {
  group('ImpressionLogger', () {
    test('logImpressions builds rows with correct shape', () {
      final rows = ImpressionLogger.buildImpressionRows(
        userId: 'u1',
        bookIds: const ['b1', 'b2', 'b3'],
        source: 'home_recommend',
        algorithmVersion: 'h10_stage0',
        sessionId: 'sess-1',
      );
      expect(rows, hasLength(3));
      expect(rows[0]['user_id'], 'u1');
      expect(rows[0]['book_id'], 'b1');
      expect(rows[0]['position'], 0);
      expect(rows[1]['position'], 1);
      expect(rows[0]['source'], 'home_recommend');
      expect(rows[0]['algorithm_version'], 'h10_stage0');
      expect(rows[0]['session_id'], 'sess-1');
      expect(rows[0].containsKey('action'), isFalse);
    });

    test('buildActionUpdate returns action + action_at fields', () {
      final patch = ImpressionLogger.buildActionUpdate(action: 'clicked');
      expect(patch['action'], 'clicked');
      expect(patch['action_at'], isA<String>());
    });

    test('buildActionUpdate rejects invalid action', () {
      expect(
        () => ImpressionLogger.buildActionUpdate(action: 'wat'),
        throwsArgumentError,
      );
    });
  });
}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd app && flutter test test/core/services/impression_logger_test.dart`
Expected: FAIL — `impression_logger.dart` 없음.

- [ ] **Step 3: 구현**

```dart
// app/lib/core/services/impression_logger.dart
import 'package:supabase_flutter/supabase_flutter.dart';

/// 추천 노출/액션을 recommendation_impressions 테이블에 비동기 로깅.
/// 모든 메서드는 fire-and-forget — 실패해도 UI 응답을 막지 않는다.
class ImpressionLogger {
  ImpressionLogger(this._client);
  final SupabaseClient _client;

  static const _validActions = {
    'clicked',
    'saved',
    'liked',
    'disliked',
    'ignored',
  };

  /// 노출 row 빌드 (테스트 가능, Supabase 호출 X)
  static List<Map<String, dynamic>> buildImpressionRows({
    required String userId,
    required List<String> bookIds,
    required String source,
    required String algorithmVersion,
    String? sessionId,
  }) {
    return [
      for (var i = 0; i < bookIds.length; i++)
        {
          'user_id': userId,
          'book_id': bookIds[i],
          'position': i,
          'source': source,
          'algorithm_version': algorithmVersion,
          if (sessionId != null) 'session_id': sessionId,
        },
    ];
  }

  static Map<String, dynamic> buildActionUpdate({required String action}) {
    if (!_validActions.contains(action)) {
      throw ArgumentError.value(action, 'action', 'invalid action');
    }
    return {
      'action': action,
      'action_at': DateTime.now().toUtc().toIso8601String(),
    };
  }

  /// 추천 카드 N개 노출 로깅 (fire-and-forget)
  Future<void> logImpressions({
    required List<String> bookIds,
    required String source,
    required String algorithmVersion,
    String? sessionId,
  }) async {
    final userId = _client.auth.currentUser?.id;
    if (userId == null || bookIds.isEmpty) return;
    final rows = buildImpressionRows(
      userId: userId,
      bookIds: bookIds,
      source: source,
      algorithmVersion: algorithmVersion,
      sessionId: sessionId,
    );
    try {
      await _client.from('recommendation_impressions').insert(rows);
    } catch (_) {
      // 비동기 로깅 — 실패는 무시 (스펙 §5)
    }
  }

  /// 가장 최근 노출 row 의 action 업데이트.
  /// (user_id, book_id) 기준 가장 최근 unactioned row 1건만 갱신.
  Future<void> logAction({
    required String bookId,
    required String action,
  }) async {
    final userId = _client.auth.currentUser?.id;
    if (userId == null) return;
    final patch = buildActionUpdate(action: action);
    try {
      // 가장 최근 unactioned row 1건만 update
      final latest = await _client
          .from('recommendation_impressions')
          .select('id')
          .eq('user_id', userId)
          .eq('book_id', bookId)
          .filter('action', 'is', null)
          .order('shown_at', ascending: false)
          .limit(1)
          .maybeSingle();
      if (latest == null) return;
      await _client
          .from('recommendation_impressions')
          .update(patch)
          .eq('id', latest['id']);
    } catch (_) {
      // ignore
    }
  }
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd app && flutter test test/core/services/impression_logger_test.dart`
Expected: All tests passed.

- [ ] **Step 5: Commit**

```bash
git add app/lib/core/services/impression_logger.dart app/test/core/services/impression_logger_test.dart
git commit -m "feat: ImpressionLogger 서비스 + 유닛 테스트"
```

---

### Task 5: 추천 화면에서 ImpressionLogger 호출

**Files:**
- Modify: `app/lib/features/home/providers/recommendation_provider.dart`
- Modify: `app/lib/features/home/widgets/book_detail_bottom_sheet.dart`
- Modify: `app/lib/features/feedback/providers/feedback_flow_provider.dart`

**Why:** 실제 노출/액션 데이터가 흐르도록 연결. 비동기, 응답 안 막음.

- [ ] **Step 1: recommendation_provider 에서 노출 로깅**

`app/lib/features/home/providers/recommendation_provider.dart` 의 추천 fetch 직후, 결과를 반환하기 전에 다음을 추가:

```dart
// import 추가
import 'package:supabase_flutter/supabase_flutter.dart';
import '../../../core/services/impression_logger.dart';

// fetch 메서드 안, recommendations 받아온 후:
final logger = ImpressionLogger(Supabase.instance.client);
unawaited(logger.logImpressions(
  bookIds: result.recommendations.map((r) => r.bookId).toList(),
  source: 'home_recommend',
  algorithmVersion: 'h10_stage0',
));
```

`unawaited` 는 `import 'dart:async';` 필요.

- [ ] **Step 2: book_detail_bottom_sheet 에서 click 액션**

`book_detail_bottom_sheet.dart` 가 추천에서 열릴 때, 첫 build 직후:

```dart
// initState 또는 build 첫 호출 위치
ImpressionLogger(Supabase.instance.client).logAction(
  bookId: book.id,
  action: 'clicked',
);
```

(stateful 위젯이 아니라면 stateless 안에서는 `WidgetsBinding.instance.addPostFrameCallback` 으로 감싼다.)

- [ ] **Step 3: feedback_flow_provider 에서 liked/disliked**

`feedback_flow_provider.dart` 에서 유저가 평가를 저장할 때(rating='good'/'bad' 반영 직후):

```dart
final action = rating == 'good' ? 'liked' : 'disliked';
unawaited(ImpressionLogger(Supabase.instance.client)
    .logAction(bookId: bookId, action: action));
```

- [ ] **Step 4: 빌드 + 정적 검사**

Run:
```bash
cd app && flutter analyze
```
Expected: 0 errors, 0 warnings 추가.

- [ ] **Step 5: 수동 smoke 테스트 안내**

실기기 또는 시뮬레이터에서:
1. 홈 진입 → 추천 섹션 로드
2. Supabase 대시보드 SQL Editor:
```sql
SELECT source, action, count(*) FROM recommendation_impressions
WHERE user_id = '<나의 user_id>'
GROUP BY 1,2 ORDER BY 1,2;
```
Expected: `home_recommend / null` row N개, 책 클릭하면 `clicked` 1건 등장.

- [ ] **Step 6: Commit**

```bash
git add app/lib/features/home/providers/recommendation_provider.dart \
        app/lib/features/home/widgets/book_detail_bottom_sheet.dart \
        app/lib/features/feedback/providers/feedback_flow_provider.dart
git commit -m "feat: 추천 노출/액션 ImpressionLogger 연동"
```

---

### Task 6: status 값 변경에 따른 Flutter 코드 마이그레이션

**Files:**
- Modify: `app/lib/features/bookshelf/providers/bookshelf_provider.dart`
- Modify: 그 외 `'read'`, `'want_to_read'` literal 사용처 (grep 후 수정)

**Why:** Task 1에서 DB 값이 바뀜. 앱이 옛 값을 INSERT/SELECT 하면 깨진다.

- [ ] **Step 1: 사용처 식별**

Run:
```bash
cd "/Users/eden.huh/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second Brain/00 Inbox/curation"
grep -rn "'read'\|'want_to_read'\|\"read\"\|\"want_to_read\"" app/lib --include="*.dart"
```
모든 매치 위치 기록.

- [ ] **Step 2: 매핑 적용**

각 파일에서:
- `'read'` → `'finished'`
- `'want_to_read'` → `'wishlist'`
- `'reading'` 은 그대로

**주의:** `'read'` 가 변수명/주석/다른 의미인 경우 건드리지 말 것. status 컨텍스트만.

- [ ] **Step 3: rating 'neutral' 사용처도 정리**

```bash
grep -rn "'neutral'\|\"neutral\"" app/lib --include="*.dart"
```
status='neutral'이 rating 으로 들어가는 곳이 있으면 `null`로 바꾸고, UI 에서 `neutral` 선택지가 있으면 제거.

- [ ] **Step 4: 빌드 + 분석**

Run:
```bash
cd app && flutter analyze && flutter test
```
Expected: 0 errors, 모든 기존 테스트 통과.

- [ ] **Step 5: Commit**

```bash
git add app/lib
git commit -m "refactor: user_books status/rating 값 v3 모델로 동기화"
```

---

### Task 7: user_state 테이블

**Files:**
- Create: `supabase/migrations/20260407_phase1a_user_state.sql`

**Why:** spec §3.2.5. Tier 분류 + 활성 여부 캐시. 매시간 갱신.

- [ ] **Step 1: SQL 작성**

```sql
-- 20260407_phase1a_user_state.sql
BEGIN;

CREATE TABLE IF NOT EXISTS public.user_state (
  user_id UUID PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
  total_likes INT DEFAULT 0 NOT NULL,
  total_saves INT DEFAULT 0 NOT NULL,
  total_finished INT DEFAULT 0 NOT NULL,
  consecutive_ignores INT DEFAULT 0 NOT NULL,
  last_active_at TIMESTAMPTZ,
  is_active BOOLEAN DEFAULT FALSE NOT NULL,
  current_tier INT DEFAULT 0 NOT NULL CHECK (current_tier IN (0,1,2)),
  updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_state_active
  ON public.user_state (is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_user_state_tier
  ON public.user_state (current_tier);

ALTER TABLE public.user_state ENABLE ROW LEVEL SECURITY;
CREATE POLICY "유저는 본인 state 조회" ON public.user_state
  FOR SELECT USING (auth.uid() = user_id);

COMMIT;
```

- [ ] **Step 2: 적용 + 검증**

```sql
SELECT column_name, data_type, column_default
FROM information_schema.columns WHERE table_name = 'user_state';
```
Expected: 9 컬럼.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/20260407_phase1a_user_state.sql
git commit -m "feat: user_state 캐시 테이블"
```

---

### Task 8: user_state 갱신 함수 + pg_cron 매시간 job

**Files:**
- Create: `supabase/migrations/20260407_phase1a_user_state_refresh.sql`

**Why:** spec §4 — user_state 매시간 갱신. Tier 임계: 3권 / 6권 (spec project memory + algorithm spec). is_active = 30일 내 활동.

- [ ] **Step 1: SQL 작성**

```sql
-- 20260407_phase1a_user_state_refresh.sql
BEGIN;

-- 갱신 함수: 모든 유저의 state 를 user_books / impressions 에서 재계산
CREATE OR REPLACE FUNCTION public.refresh_user_state()
RETURNS void AS $$
BEGIN
  INSERT INTO public.user_state (
    user_id, total_likes, total_saves, total_finished,
    consecutive_ignores, last_active_at, is_active, current_tier, updated_at
  )
  SELECT
    u.id AS user_id,
    COALESCE(ub.likes, 0) AS total_likes,
    COALESCE(ub.saves, 0) AS total_saves,
    COALESCE(ub.finished, 0) AS total_finished,
    0 AS consecutive_ignores, -- Phase 1B 에서 impressions 로 계산
    ub.last_active_at,
    (ub.last_active_at IS NOT NULL AND ub.last_active_at > NOW() - INTERVAL '30 days') AS is_active,
    CASE
      WHEN COALESCE(ub.likes, 0) >= 6 THEN 2
      WHEN COALESCE(ub.likes, 0) >= 3 THEN 1
      ELSE 0
    END AS current_tier,
    NOW() AS updated_at
  FROM public.users u
  LEFT JOIN (
    SELECT
      user_id,
      COUNT(*) FILTER (WHERE rating = 'good')              AS likes,
      COUNT(*) FILTER (WHERE status = 'wishlist')          AS saves,
      COUNT(*) FILTER (WHERE status = 'finished')          AS finished,
      MAX(updated_at)                                       AS last_active_at
    FROM public.user_books
    GROUP BY user_id
  ) ub ON ub.user_id = u.id
  ON CONFLICT (user_id) DO UPDATE SET
    total_likes = EXCLUDED.total_likes,
    total_saves = EXCLUDED.total_saves,
    total_finished = EXCLUDED.total_finished,
    last_active_at = EXCLUDED.last_active_at,
    is_active = EXCLUDED.is_active,
    current_tier = EXCLUDED.current_tier,
    updated_at = NOW();
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- pg_cron extension (이미 켜져있으면 무시)
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- 기존 동일 job 제거 후 등록
SELECT cron.unschedule('refresh_user_state_hourly')
  WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'refresh_user_state_hourly');

SELECT cron.schedule(
  'refresh_user_state_hourly',
  '0 * * * *',
  $$SELECT public.refresh_user_state();$$
);

COMMIT;
```

- [ ] **Step 2: 적용**

Supabase SQL Editor 에서 실행. pg_cron 이 기본 활성화돼있지 않으면 Project Settings → Database → Extensions 에서 `pg_cron` 활성화 후 재실행.

- [ ] **Step 3: 함수 수동 실행 & 검증**

```sql
SELECT public.refresh_user_state();
SELECT user_id, total_likes, total_finished, current_tier, is_active
FROM public.user_state ORDER BY total_likes DESC LIMIT 10;
SELECT * FROM cron.job WHERE jobname = 'refresh_user_state_hourly';
```
Expected: user_state 에 모든 유저 row 존재, tier 분포 확인 가능, cron job 1건 등록.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/20260407_phase1a_user_state_refresh.sql
git commit -m "feat: refresh_user_state 함수 + 매시간 pg_cron job"
```

---

### Task 9: 검증 스크립트 + 마무리

**Files:**
- Create: `scripts/verify_phase1a.sql`

- [ ] **Step 1: 검증 쿼리 묶음 작성**

```sql
-- scripts/verify_phase1a.sql
-- Phase 1A 인프라 동작 확인

-- 1. user_books 정규화
SELECT 'status' AS k, status AS v, COUNT(*) FROM public.user_books GROUP BY status
UNION ALL
SELECT 'rating', COALESCE(rating, '(null)'), COUNT(*) FROM public.user_books GROUP BY rating;

-- 2. history trigger 살아있는지
SELECT tgname FROM pg_trigger WHERE tgrelid = 'public.user_books'::regclass;

-- 3. impressions 인덱스
SELECT indexname FROM pg_indexes WHERE tablename = 'recommendation_impressions';

-- 4. user_state 갱신 결과
SELECT current_tier, COUNT(*), SUM(total_likes) AS sum_likes
FROM public.user_state GROUP BY current_tier ORDER BY current_tier;

-- 5. cron job
SELECT jobname, schedule FROM cron.job WHERE jobname LIKE '%user_state%';
```

- [ ] **Step 2: 실행**

Supabase SQL Editor 에 paste & run. Expected: 모든 섹션 정상 결과.

- [ ] **Step 3: Memory 업데이트 + Commit**

`project_recommendation_v3.md` 의 "다음 단계" 섹션에 "Phase 1A 완료 (2026-04-07)" 추가하도록 사용자에게 안내.

```bash
git add scripts/verify_phase1a.sql
git commit -m "chore: Phase 1A 검증 SQL 스크립트"
```

---

## Self-Review

- 스펙 §9 즉시 항목 4개 모두 커버: status 정규화(T1) + history(T2) + impressions(T3-5) + user_state(T7-8). ✅
- Flutter 측 status 값 일관성: T1(DB 값 변경) → T6(앱 코드 동기화). 순서가 중요 — T6 가 T1 직후가 아니어도 같은 PR/세션 안에서 끝나야 앱이 깨지지 않음.
- 함수명/필드명 일관성: `recommendation_impressions` 컬럼은 spec §3.2.1 그대로, `ImpressionLogger.buildImpressionRows / buildActionUpdate / logImpressions / logAction` 일관.
- Tier 임계값(3/6) 은 project memory `project_recommendation_v3.md` 와 일치.
- consecutive_ignores 는 Phase 1B (impressions 누적 후) 에서 계산 — Task 8에서 0 으로 둠을 명시.

---

Plan complete and saved to `docs/superpowers/plans/2026-04-07-data-collection-phase1a.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks
**2. Inline Execution** — execute in this session with checkpoints

Which approach?
