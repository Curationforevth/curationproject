/// 장르/무드 기반 책등 폰트 자동 배정
///
/// 5~6개 한국어 폰트 풀에서 책 메타데이터에 따라 적합한 폰트 선택.
/// Phase 1: 장르 키워드 매칭. Phase 2: LLM 자동 선택.
class FontAssigner {
  /// 사용 가능한 폰트 풀
  static const fontPool = {
    'Nanum Myeongjo': ['문학', '소설', '고전', '순수문학'],
    'Noto Serif KR': ['역사', '논픽션', '사회', '전쟁'],
    'Black Han Sans': ['스릴러', '추리', '범죄', '사회고발'],
    'Gowun Batang': ['시', '에세이', '산문', '수필'],
    'Do Hyeon': ['SF', 'IT', '과학', '현대'],
    'Jua': ['힐링', '일상', '가족', '요리', '여행'],
    'Gaegu': ['판타지', '동화', '청소년', '만화'],
  };

  /// 기본 폰트 (매칭되는 장르가 없을 때)
  static const defaultFont = 'Pretendard';

  /// 장르/설명에서 키워드 매칭으로 폰트 결정
  ///
  /// TODO: Phase 2에서 LLM 기반 자동 선택으로 업그레이드
  static String assignFont({String? genre, String? description}) {
    final text = '${genre ?? ''} ${description ?? ''}'.toLowerCase();

    for (final entry in fontPool.entries) {
      for (final keyword in entry.value) {
        if (text.contains(keyword)) {
          return entry.key;
        }
      }
    }

    return defaultFont;
  }
}
