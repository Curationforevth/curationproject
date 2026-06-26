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
  return url;
}
