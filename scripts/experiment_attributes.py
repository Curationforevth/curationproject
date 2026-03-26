"""
구조화된 속성 기반 추천 실험 스크립트

1) 책 3권의 속성 추출 (LLM)
2) Eden 피드백 → 유저 속성 프로필 매핑
3) 임베딩만 vs 임베딩+속성 매칭 추천 비교

사용법:
  python3 scripts/experiment_attributes.py
"""

import json
import os
import re
import sys

import numpy as np
import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])
OPENAI_API_KEY = os.environ['OPENAI_API_KEY']


def call_openai(prompt, temperature=0.3):
    """OpenAI API 직접 호출 (패키지 호환 문제 우회)"""
    resp = requests.post(
        'https://api.openai.com/v1/chat/completions',
        headers={
            'Authorization': f'Bearer {OPENAI_API_KEY}',
            'Content-Type': 'application/json',
        },
        json={
            'model': 'gpt-4o-mini',
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': temperature,
            'response_format': {'type': 'json_object'},
        },
        timeout=30,
    )
    resp.raise_for_status()
    return json.loads(resp.json()['choices'][0]['message']['content'])

# 15개 속성
ATTRIBUTES = [
    '서사구조', '심리묘사', '세계관', '문체미', '유머',
    '속도감', '긴장감', '감정적깊이', '사회비평', '철학적깊이',
    '분위기', '독창성', '몰입감', '캐릭터매력', '여운',
]

# 실험 대상 책
BOOKS = {
    '해리포터': {
        'id': '76cd5e63-29de-4ca4-8414-9ee035e6aef0',
        'rating': 'good',
        'feedback': '세계관이 새롭고 디테일하고 생동감있어서 몰입이 되었고, 매력적인 내용이 많아서 읽는게 즐거웠어.',
    },
    '나미야잡화점': {
        'id': '83236f16-9bd6-477f-b835-e87466a68305',
        'rating': 'good',
        'feedback': '재밌는 소재라고 생각했고, 긴데도 금방 읽히더라.',
    },
    '1984': {
        'id': '7e312cb7-d5ab-46de-b97b-5681afde78b7',
        'rating': 'neutral',
        'feedback': '워낙 유명한 책이라 그 당시 그린 디스토피아와 지금을 비교해보는 재미가 있었어.',
    },
}

RATING_WEIGHT = {'good': 1.0, 'neutral': 0.5, 'bad': 0.0}


def extract_attributes(title, description, genre):
    """LLM으로 책의 구조화된 속성 추출 (0.0~1.0)"""
    prompt = f"""다음 책의 특성을 0.0~1.0 점수로 평가해주세요.
반드시 JSON만 출력하세요. 설명 없이 숫자만.

책: {title}
장르: {genre}
설명: {description[:500]}

평가할 속성: {json.dumps(ATTRIBUTES, ensure_ascii=False)}

출력 형식 (JSON만):
{{"서사구조": 0.8, "심리묘사": 0.5, ...}}"""

    return call_openai(prompt)


def map_feedback_to_attributes(title, feedback, rating):
    """유저 피드백을 속성 점수로 매핑"""
    prompt = f"""유저가 책 "{title}"에 대해 다음 피드백을 남겼습니다.
평점: {rating}
피드백: "{feedback}"

이 피드백에서 유저가 어떤 속성을 좋아하는지 0.0~1.0 점수로 매핑해주세요.
- 피드백에서 직접 언급하거나 암시하는 속성만 점수를 매기세요
- 언급하지 않은 속성은 null로 두세요
- 반드시 JSON만 출력하세요

속성 목록: {json.dumps(ATTRIBUTES, ensure_ascii=False)}

출력 형식 (JSON만):
{{"세계관": 0.9, "몰입감": 0.8, "서사구조": null, ...}}"""

    return call_openai(prompt)


def build_user_attribute_profile(feedback_attrs_list):
    """여러 피드백의 속성을 합산하여 유저 속성 프로필 생성"""
    profile = {}
    counts = {}
    for attrs, weight in feedback_attrs_list:
        for attr, score in attrs.items():
            if score is not None and isinstance(score, (int, float)):
                weighted = score * weight
                profile[attr] = profile.get(attr, 0) + weighted
                counts[attr] = counts.get(attr, 0) + weight

    # 평균
    for attr in profile:
        if counts[attr] > 0:
            profile[attr] = round(profile[attr] / counts[attr], 2)
    return profile


def attribute_match_score(user_profile, book_attrs):
    """유저 프로필과 책 속성 간 매칭 점수 (0~1)"""
    if not user_profile or not book_attrs:
        return 0.0
    scores = []
    for attr, user_score in user_profile.items():
        book_score = book_attrs.get(attr)
        if book_score is not None and isinstance(book_score, (int, float)):
            # 유저가 중시하는 속성이 책에서 높으면 매칭
            scores.append(user_score * book_score)
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def extract_top_genre(genre_str):
    """장르 문자열에서 최상위 카테고리 추출 (예: '국내도서>소설/시/희곡>일본소설' → '소설/시/희곡')"""
    if not genre_str:
        return None
    parts = genre_str.split('>')
    # '국내도서' / '외국도서' 다음의 실제 장르 카테고리 반환
    if len(parts) >= 2:
        return parts[1].strip()
    return parts[0].strip()


def normalize_title(title):
    """제목 정규화 — 부제·권수·출판사 등을 제거하고 핵심만 남김"""
    if not title:
        return ''
    # 괄호 안 내용 제거 (예: "(양장)", "(개정판)")
    t = re.sub(r'[\(\（][^)）]*[\)\）]', '', title)
    # 콜론/대시 뒤 부제 제거
    t = re.split(r'[:\-–—]', t)[0]
    # 숫자 권수 제거 (예: "1권", "제1권")
    t = re.sub(r'(제?\d+권?)', '', t)
    # 공백·특수문자 정리
    t = re.sub(r'\s+', '', t).strip()
    return t.lower()


GENRE_PENALTY = 0.3  # 장르 불일치 시 유사도에 곱하는 패널티


def get_recommendations(book_ids, top_n=15):
    """임베딩 기반 추천 후보 가져오기 — 장르 필터링 + 중복판 제거"""
    all_candidates = {}

    # 입력 책 정보 조회 (장르·제목 파악)
    input_books = sb.table('books').select(
        'id, title, genre'
    ).in_('id', book_ids).execute().data
    input_map = {b['id']: b for b in input_books}

    # 유저 책의 최상위 장르 집합
    user_genres = set()
    for b in input_books:
        g = extract_top_genre(b.get('genre', ''))
        if g:
            user_genres.add(g)

    # 유저 책의 정규화된 제목 집합 (중복판 제거용)
    input_titles_norm = set()
    for b in input_books:
        nt = normalize_title(b.get('title', ''))
        if nt:
            input_titles_norm.add(nt)

    for book_id in book_ids:
        r = sb.rpc('match_books_by_similarity', {
            'target_book_id': book_id,
            'match_count': 30,
        }).execute()

        for c in r.data:
            cid = c['book_id']
            if cid not in book_ids:
                if cid not in all_candidates:
                    all_candidates[cid] = {'id': cid, 'similarity': c['similarity']}
                else:
                    all_candidates[cid]['similarity'] = max(
                        all_candidates[cid]['similarity'], c['similarity']
                    )

    # 책 상세 정보 일괄 조회
    if all_candidates:
        cids = list(all_candidates.keys())
        details = sb.table('books').select(
            'id, title, author, genre, description, enriched_description, rich_description'
        ).in_('id', cids).execute().data
        detail_map = {d['id']: d for d in details}

        to_remove = []
        for cid, c in all_candidates.items():
            d = detail_map.get(cid, {})
            c['title'] = d.get('title', '(제목 없음)')
            c['author'] = d.get('author', '')
            c['genre'] = d.get('genre', '')
            desc = d.get('rich_description') or d.get('enriched_description') or d.get('description') or ''
            c['description'] = re.sub(r'<[^>]+>', '', desc)

            # 중복판 제거: 정규화된 제목이 입력 책과 동일하면 제외
            if normalize_title(c['title']) in input_titles_norm:
                to_remove.append(cid)
                continue

            # 장르 패널티: 후보의 최상위 장르가 유저 장르와 하나도 겹치지 않으면 패널티
            cand_genre = extract_top_genre(c['genre'])
            if user_genres and cand_genre and cand_genre not in user_genres:
                c['similarity'] *= GENRE_PENALTY
                c['genre_filtered'] = True
            else:
                c['genre_filtered'] = False

        for cid in to_remove:
            del all_candidates[cid]

    return all_candidates


def run_experiment():
    print("=" * 60)
    print("구조화된 속성 기반 추천 실험")
    print("=" * 60)

    # 1) 책 데이터 가져오기
    book_ids = [b['id'] for b in BOOKS.values()]
    db_books = sb.table('books').select(
        'id, title, description, genre, rich_description'
    ).in_('id', book_ids).execute().data

    db_map = {b['id']: b for b in db_books}

    # 2) 속성 추출
    print("\n[1단계] 책 속성 추출 (LLM)")
    print("-" * 40)
    book_attributes = {}
    for name, info in BOOKS.items():
        db = db_map[info['id']]
        desc = db.get('rich_description') or db.get('description') or ''
        desc = re.sub(r'<[^>]+>', '', desc)

        attrs = extract_attributes(db['title'], desc, db.get('genre', ''))
        book_attributes[info['id']] = attrs
        print(f"\n📖 {name}")
        top_attrs = sorted(attrs.items(), key=lambda x: x[1], reverse=True)[:5]
        for a, s in top_attrs:
            bar = "█" * int(s * 10) + "░" * (10 - int(s * 10))
            print(f"  {a:8s} {bar} {s}")

    # 3) 피드백 → 속성 매핑
    print("\n[2단계] 피드백 → 속성 매핑")
    print("-" * 40)
    feedback_attrs_list = []
    for name, info in BOOKS.items():
        db = db_map[info['id']]
        f_attrs = map_feedback_to_attributes(db['title'], info['feedback'], info['rating'])
        weight = RATING_WEIGHT[info['rating']]
        feedback_attrs_list.append((f_attrs, weight))

        mentioned = {k: v for k, v in f_attrs.items() if v is not None and isinstance(v, (int, float))}
        print(f"\n💬 {name} (weight={weight})")
        print(f"  피드백: \"{info['feedback'][:50]}...\"")
        for a, s in sorted(mentioned.items(), key=lambda x: x[1], reverse=True):
            bar = "█" * int(s * 10) + "░" * (10 - int(s * 10))
            print(f"  → {a:8s} {bar} {s}")

    # 4) 유저 속성 프로필
    user_profile = build_user_attribute_profile(feedback_attrs_list)
    print("\n[3단계] 유저 속성 프로필 (합산)")
    print("-" * 40)
    for a, s in sorted(user_profile.items(), key=lambda x: x[1], reverse=True):
        if s > 0.1:
            bar = "█" * int(s * 10) + "░" * (10 - int(s * 10))
            print(f"  {a:8s} {bar} {s}")

    # 유저 장르 출력
    print("\n[3.5단계] 유저 장르 분석")
    print("-" * 40)
    input_books_db = sb.table('books').select('id, title, genre').in_('id', book_ids).execute().data
    user_genres_display = set()
    for b in input_books_db:
        g = extract_top_genre(b.get('genre', ''))
        if g:
            user_genres_display.add(g)
        print(f"  {b['title'][:25]} → {b.get('genre', '?')}")
    print(f"  → 유저 최상위 장르: {user_genres_display}")

    # 5) 추천 후보 가져오기 (장르 필터링 + 중복판 제거 적용)
    print("\n[4단계] 추천 후보 수집 (장르 필터링 + 중복판 제거)")
    print("-" * 40)
    candidates = get_recommendations(book_ids)
    genre_filtered_count = sum(1 for c in candidates.values() if c.get('genre_filtered'))
    print(f"  후보: {len(candidates)}권 (장르 패널티 적용: {genre_filtered_count}권)")

    # 6) 후보 책 속성 추출 (Top-20만)
    sorted_by_sim = sorted(candidates.values(), key=lambda x: x['similarity'], reverse=True)[:20]
    print(f"\n[5단계] Top-20 후보 속성 추출")
    print("-" * 40)
    candidate_attrs = {}
    for i, c in enumerate(sorted_by_sim):
        desc = c.get('description', '')
        attrs = extract_attributes(c['title'], desc, c.get('genre', ''))
        candidate_attrs[c['id']] = attrs
        sys.stdout.write(f"\r  추출 중... {i+1}/20")
        sys.stdout.flush()
    print()

    # 7) 비교: 임베딩만 vs 임베딩+속성
    ALPHA = 0.3  # 속성 매칭 가중치

    print("\n" + "=" * 60)
    print("📊 추천 결과 비교")
    print("=" * 60)

    results = []
    for c in sorted_by_sim:
        sim = c['similarity']
        attrs = candidate_attrs.get(c['id'], {})
        attr_score = attribute_match_score(user_profile, attrs)
        combined = sim * (1 + ALPHA * attr_score)
        results.append({
            **c,
            'attr_score': attr_score,
            'combined_score': combined,
            'attrs': attrs,
        })

    # 임베딩만 (원래 순서)
    by_embedding = sorted(results, key=lambda x: x['similarity'], reverse=True)
    # 임베딩+속성
    by_combined = sorted(results, key=lambda x: x['combined_score'], reverse=True)

    print("\n[A] 임베딩 유사도만 (장르 필터링 적용)")
    print("-" * 60)
    for i, r in enumerate(by_embedding[:10], 1):
        title = r['title'][:30]
        genre_tag = extract_top_genre(r.get('genre', '')) or '?'
        penalty_mark = ' ⚠️장르↓' if r.get('genre_filtered') else ''
        print(f"  {i:2d}. [{r['similarity']:.3f}] {title}  [{genre_tag}]{penalty_mark}")

    print("\n[B] 임베딩 + 속성 매칭 (α=0.3, 장르 필터링 적용)")
    print("-" * 60)
    for i, r in enumerate(by_combined[:10], 1):
        title = r['title'][:30]
        genre_tag = extract_top_genre(r.get('genre', '')) or '?'
        penalty_mark = ' ⚠️장르↓' if r.get('genre_filtered') else ''
        attr_top = sorted(
            {k: v for k, v in r['attrs'].items() if isinstance(v, (int, float))}.items(),
            key=lambda x: x[1], reverse=True
        )[:3]
        attr_str = ', '.join(f"{a}={s}" for a, s in attr_top)
        print(f"  {i:2d}. [{r['combined_score']:.3f}] {title}  [{genre_tag}]{penalty_mark}")
        print(f"      sim={r['similarity']:.3f} attr={r['attr_score']:.3f} | {attr_str}")

    # 순위 변화
    print("\n[C] 순위 변화 (속성 매칭으로 올라온 책)")
    print("-" * 60)
    emb_rank = {r['id']: i for i, r in enumerate(by_embedding)}
    for i, r in enumerate(by_combined[:10]):
        old_rank = emb_rank[r['id']]
        diff = old_rank - i
        if diff > 0:
            print(f"  ↑{diff:+d} {r['title'][:35]} (attr_score={r['attr_score']:.3f})")
        elif diff < 0:
            print(f"  ↓{diff:+d} {r['title'][:35]}")


if __name__ == '__main__':
    run_experiment()
