-- 배치 수집 상태 추적 테이블
-- 어떤 소스(카테고리×QueryType, 검색 키워드)를 몇 페이지까지 수집했는지 기록
-- 스크립트 중단/재시작 시 이어서 수집 가능

create table if not exists public.batch_collection_state (
  id uuid primary key default gen_random_uuid(),
  source_type text not null,           -- 'item_list' / 'item_search'
  query_type text,                     -- 'Bestseller', 'ItemNewAll' 등
  category_id int,                     -- 알라딘 카테고리 ID
  search_keyword text,                 -- ItemSearch용 검색어
  last_page_fetched int default 0,     -- 마지막으로 가져온 페이지 번호
  total_items_found int default 0,     -- API에서 받은 총 아이템 수
  unique_items_saved int default 0,    -- 실제 새로 저장된 수
  completed boolean default false,     -- 모든 페이지 수집 완료 여부
  created_at timestamptz default now(),
  updated_at timestamptz default now(),

  unique(source_type, query_type, category_id, search_keyword)
);

-- 분석용 인덱스
create index if not exists idx_books_source on public.books(source);
create index if not exists idx_books_created_at on public.books(created_at);
