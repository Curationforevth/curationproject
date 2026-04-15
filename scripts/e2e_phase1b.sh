#!/usr/bin/env bash
# Phase 1B End-to-end 검증 시나리오
# 사용법: API=https://... JWT=... UID=... ./scripts/e2e_phase1b.sh
#
# test user JWT 발급:
# Supabase Dashboard → Authentication → Users → test 계정 생성 → Sign in
# → access_token 복사
set -euo pipefail

: "${API:?API env 필요}"
: "${JWT:?JWT env 필요}"
: "${UID:?UID env 필요}"

H="Authorization: Bearer $JWT"

echo "=== 1. Tier 0 (신규) 홈 호출 ==="
curl -sS -H "$H" "$API/home/$UID" | jq '{tier, sections: [.sections[].type]}'
echo ""

echo "=== 2. Tier 0 → Tier 1 전환을 위해 좋아요 3권 추가 ==="
echo "Supabase SQL Editor 에서 실행:"
echo "  INSERT INTO user_books (user_id, book_id, rating, status) VALUES"
echo "    ('$UID', (SELECT id FROM books ORDER BY loan_count DESC NULLS LAST LIMIT 1 OFFSET 0), 'good', 'finished'),"
echo "    ('$UID', (SELECT id FROM books ORDER BY loan_count DESC NULLS LAST LIMIT 1 OFFSET 1), 'good', 'finished'),"
echo "    ('$UID', (SELECT id FROM books ORDER BY loan_count DESC NULLS LAST LIMIT 1 OFFSET 2), 'good', 'finished');"
read -p "실행 완료 후 Enter..."

echo "=== 3. Tier 1 홈 호출 ==="
curl -sS -H "$H" "$API/home/$UID" | jq '{tier, sections: [.sections[].type]}'
echo ""

echo "=== 4. 좋아요 3권 더 추가 (총 6권) → Tier 2 ==="
read -p "추가 INSERT 완료 후 Enter..."

echo "=== 5. Tier 2 홈 호출 ==="
curl -sS -H "$H" "$API/home/$UID" | jq '{tier, sections: [.sections[].type]}'
echo ""

echo "=== 6. impression.curation_id 기록 확인 ==="
echo "Supabase SQL Editor:"
echo "  SELECT COUNT(*) FROM recommendation_impressions"
echo "  WHERE user_id='$UID' AND curation_id IS NOT NULL;"
echo "  기대: > 0"

echo ""
echo "=== 완료 ==="
