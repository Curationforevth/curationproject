/// 저해상도 표지 URL을 가능한 한 고해상도로 변환한다.
///
/// 카카오 책 검색 API의 `thumbnail` 은 CDN 리사이즈 썸네일
/// (`https://search1.kakaocdn.net/thumb/R120x174.q85/?fname=<원본URL>`) 이라
/// 크게 표시하면 흐릿하다. `fname` 의 원본 URL이 풀해상도이므로 그것을 사용한다.
///
/// 그 외 URL(이미 원본/알라딘 등)은 그대로 반환한다(idempotent — 여러 번 호출해도 안전).
String? highResCoverUrl(String? url) {
  if (url == null || url.isEmpty) return url;
  final uri = Uri.tryParse(url);
  if (uri != null && uri.host.contains('kakaocdn')) {
    final fname = uri.queryParameters['fname'];
    if (fname != null && fname.isNotEmpty) return fname;
  }
  // 알라딘 CDN 썸네일은 같은 경로에 cover500 원본이 있다 — 크게 그릴 때
  // 업스케일 흐림 방지. 변형 전수(DB 실측): coversum(~150px)·cover150·
  // cover200·**cover(접미사 없음, 구형 ~200px — '이기적 유전자' 실기기 흐림
  // 재발 원인)**. cover500 자신은 옵션 그룹 불일치로 건드리지 않는다.
  if (uri != null && uri.host == 'image.aladin.co.kr') {
    return url.replaceFirst(RegExp(r'/cover(sum|150|200)?/'), '/cover500/');
  }
  return url;
}
