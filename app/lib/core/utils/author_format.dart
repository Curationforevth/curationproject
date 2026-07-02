/// 대표 저자 표시 정규화 — DB 함수 normalize_primary_author(마이그레이션
/// 20260702000000)·서버 generate_curation_themes.normalize_primary_author 와
/// 동일 규칙(3층 동기). 소스별 표기('한강' / '이해 (지은이)' / '요한 하리 지음' /
/// '애거서 크리스티 (지은이), 공경희 (옮긴이)')를 화면에선 대표 저자 하나로 보여준다.
String displayAuthor(String? raw) {
  if (raw == null || raw.trim().isEmpty) return '';
  var primary = raw.split(',').first;
  primary = primary.replaceAll(RegExp(r'\s*\([^)]*\)'), '');
  primary = primary.replaceAll(RegExp(r'\s+(지음|옮김|엮음|글|그림)\s*$'), '').trim();
  return primary;
}
