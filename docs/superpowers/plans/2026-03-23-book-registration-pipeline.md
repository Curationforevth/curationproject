# 책 등록 파이프라인 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 검색→서재 추가 전체 워크플로우를 구현하여, 책 등록 시 dominant color 추출 + spine font 배정이 자동으로 처리되도록 한다.

**Architecture:** BookRegistrationService가 파이프라인을 오케스트레이션. 동기 단계(books upsert + user_books insert)로 즉시 등록 완료 후, 비동기 단계(color extraction + font assignment)를 백그라운드로 실행. 검색 화면에서 이미 추가된 책을 표시하여 중복 방지.

**Tech Stack:** Flutter, Supabase, Riverpod, palette_generator, google_fonts

**Spec:** `docs/superpowers/specs/2026-03-23-book-registration-pipeline-design.md`

---

## 파일 구조

### 신규 파일
| 파일 | 책임 |
|------|------|
| `supabase/004_book_enrichment.sql` | DB 마이그레이션 (컬럼 추가 + RLS) |
| `app/lib/core/services/book_registration_service.dart` | 등록 파이프라인 오케스트레이션 |
| `app/test/book_registration_test.dart` | 등록 서비스 + 색상 추출 + 폰트 테스트 |

### 수정 파일
| 파일 | 변경 내용 |
|------|-----------|
| `app/pubspec.yaml` | palette_generator, google_fonts 추가 |
| `app/lib/core/models/book.dart` | `toJsonForUpsert()` 메서드 추가 (id 제외) |
| `app/lib/core/utils/color_extractor.dart` | palette_generator 실제 구현 |
| `app/lib/core/widgets/book_spine.dart` | google_fonts TextStyle 연동 |
| `app/lib/features/search/widgets/book_search_result_card.dart` | isAdded 뱃지 |
| `app/lib/features/search/providers/book_search_provider.dart` | 서재 ISBN 교차 확인 |
| `app/lib/features/search/screens/book_search_screen.dart` | 바텀시트 + 등록 연결 |
| `app/lib/features/bookshelf/providers/bookshelf_provider.dart` | addBook mutation |

---

## Task 1: DB 마이그레이션 + 패키지 추가

**Files:**
- Create: `supabase/004_book_enrichment.sql`
- Modify: `app/pubspec.yaml`

- [ ] **Step 1: 마이그레이션 파일 작성**

```sql
-- supabase/004_book_enrichment.sql
-- 책 메타데이터 확장 (색상, 폰트, 무드태그) + 서가 정렬

ALTER TABLE public.books ADD COLUMN IF NOT EXISTS dominant_colors jsonb DEFAULT NULL;
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS spine_font text DEFAULT NULL;
ALTER TABLE public.books ADD COLUMN IF NOT EXISTS mood_tags jsonb DEFAULT NULL;

ALTER TABLE public.user_books ADD COLUMN IF NOT EXISTS shelf_order int DEFAULT NULL;

-- books UPDATE RLS 정책
CREATE POLICY "인증된 유저가 책 메타데이터 업데이트"
  ON public.books FOR UPDATE
  USING (true)
  WITH CHECK (auth.role() = 'authenticated');
```

- [ ] **Step 2: pubspec.yaml에 패키지 추가**

`app/pubspec.yaml`의 dependencies 섹션에 추가:
```yaml
  palette_generator: ^0.3.0
  google_fonts: ^6.0.0
```

- [ ] **Step 3: flutter pub get 실행**

Run: `cd app && flutter pub get`
Expected: 의존성 해결 성공

- [ ] **Step 4: 커밋**

```bash
git add supabase/004_book_enrichment.sql app/pubspec.yaml app/pubspec.lock
git commit -m "chore: DB 마이그레이션 004 + palette_generator, google_fonts 추가"
```

---

## Task 2: Book 모델 — upsert용 메서드 추가

**Files:**
- Modify: `app/lib/core/models/book.dart:69-86`
- Test: `app/test/models_test.dart`

- [ ] **Step 1: 테스트 작성**

`app/test/models_test.dart`의 `'Book'` 그룹 안에 추가:
```dart
    test('toJsonForUpsert excludes id and createdAt', () {
      final book = Book(
        id: 'temp-id',
        isbn: '9788936434267',
        title: '채식주의자',
        author: '한강',
        source: 'kakao',
      );

      final json = book.toJsonForUpsert();
      expect(json.containsKey('id'), false);
      expect(json.containsKey('created_at'), false);
      expect(json['isbn'], '9788936434267');
      expect(json['title'], '채식주의자');
      expect(json['source'], 'kakao');
    });
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd app && flutter test test/models_test.dart`
Expected: FAIL — `toJsonForUpsert` 메서드 없음

- [ ] **Step 3: Book.toJsonForUpsert() 구현**

`app/lib/core/models/book.dart`의 `toJson()` 메서드 아래에 추가:
```dart
  /// Supabase upsert용 (id, created_at 제외 — DB가 자동 생성)
  Map<String, dynamic> toJsonForUpsert() {
    return {
      'isbn': isbn,
      'title': title,
      'author': author,
      'publisher': publisher,
      'cover_url': coverUrl,
      'page_count': pageCount,
      'description': description,
      'genre': genre,
      'source': source,
      'source_id': sourceId,
      'dominant_colors': dominantColors,
      'mood_tags': moodTags,
      'spine_font': spineFont,
    };
  }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd app && flutter test test/models_test.dart`
Expected: ALL PASS

- [ ] **Step 5: 커밋**

```bash
git add app/lib/core/models/book.dart app/test/models_test.dart
git commit -m "feat: Book.toJsonForUpsert() — id 제외 upsert 지원"
```

---

## Task 3: ColorExtractor — palette_generator 구현

**Files:**
- Modify: `app/lib/core/utils/color_extractor.dart`
- Test: `app/test/book_registration_test.dart`

- [ ] **Step 1: 테스트 파일 생성 + ColorExtractor 테스트 작성**

```dart
// app/test/book_registration_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:curation_app/core/utils/color_extractor.dart';
import 'package:curation_app/core/utils/font_assigner.dart';
import 'dart:ui';

void main() {
  group('ColorExtractor', () {
    test('colorToHex converts Color to hex string', () {
      final hex = ColorExtractor.colorToHex(const Color(0xFFFF5733));
      expect(hex, '#FF5733');
    });

    test('hexToColor converts valid hex to Color', () {
      final color = ColorExtractor.hexToColor('#FF5733');
      expect(color, isNotNull);
      expect(color, const Color(0xFFFF5733));
    });

    test('hexToColor returns null for invalid hex', () {
      expect(ColorExtractor.hexToColor('invalid'), isNull);
      expect(ColorExtractor.hexToColor('#GG0000'), isNull);
      expect(ColorExtractor.hexToColor(''), isNull);
    });

    test('extractFromUrl returns empty list for empty URL', () async {
      final colors = await ColorExtractor.extractFromUrl('');
      expect(colors, isEmpty);
    });
  });

  group('FontAssigner', () {
    test('assigns Nanum Myeongjo for literary genres', () {
      final font = FontAssigner.assignFont(genre: '소설');
      expect(font, 'Nanum Myeongjo');
    });

    test('assigns Black Han Sans for thriller genres', () {
      final font = FontAssigner.assignFont(genre: '스릴러');
      expect(font, 'Black Han Sans');
    });

    test('assigns Gowun Batang for essay genres', () {
      final font = FontAssigner.assignFont(genre: '에세이');
      expect(font, 'Gowun Batang');
    });

    test('returns Pretendard as default for unknown genres', () {
      final font = FontAssigner.assignFont(genre: '알수없는장르');
      expect(font, 'Pretendard');
    });

    test('matches keywords in description when genre is null', () {
      final font = FontAssigner.assignFont(description: '이 책은 SF 세계관을 다룬다');
      expect(font, 'Do Hyeon');
    });
  });
}
```

- [ ] **Step 2: 테스트 실행 — 통과 확인 (기존 유틸리티 테스트)**

Run: `cd app && flutter test test/book_registration_test.dart`
Expected: ALL PASS (아직 기존 코드만 테스트)

- [ ] **Step 3: ColorExtractor.extractFromUrl() 구현**

`app/lib/core/utils/color_extractor.dart` 전체 교체:
```dart
import 'dart:ui';
import 'package:flutter/painting.dart';
import 'package:palette_generator/palette_generator.dart';

/// 표지 이미지에서 dominant color 2~3개를 추출
class ColorExtractor {
  /// 이미지 URL에서 dominant colors를 hex 문자열 리스트로 반환
  ///
  /// 실패 시 빈 리스트 반환 → BookSpine이 해시 기반 폴백 사용
  static Future<List<String>> extractFromUrl(String imageUrl) async {
    if (imageUrl.isEmpty) return [];

    try {
      final paletteGenerator = await PaletteGenerator.fromImageProvider(
        NetworkImage(imageUrl),
        maximumColorCount: 3,
        timeout: const Duration(seconds: 10),
      );

      final colors = paletteGenerator.colors.toList();
      if (colors.isEmpty) return [];

      return colors.take(3).map((c) => colorToHex(c)).toList();
    } catch (e) {
      // 네트워크 에러, 이미지 파싱 실패 등 — 폴백 사용
      return [];
    }
  }

  /// Color → hex 문자열 (비트 시프팅으로 정밀도 보장)
  static String colorToHex(Color color) {
    final value = color.value;
    final r = (value >> 16) & 0xFF;
    final g = (value >> 8) & 0xFF;
    final b = value & 0xFF;
    return '#${r.toRadixString(16).padLeft(2, '0').toUpperCase()}'
           '${g.toRadixString(16).padLeft(2, '0').toUpperCase()}'
           '${b.toRadixString(16).padLeft(2, '0').toUpperCase()}';
  }

  /// hex 문자열 → Color
  static Color? hexToColor(String hex) {
    try {
      final clean = hex.replaceAll('#', '');
      if (clean.length == 6) {
        return Color(int.parse('FF$clean', radix: 16));
      }
    } catch (_) {}
    return null;
  }
}
```

> **참고:** `colorToHex`는 `color.value` 비트 시프팅을 사용하여 Flutter 버전에 관계없이 정확한 값 추출. 구현 시 `flutter --version`에 따라 `color.value` 지원 여부만 확인.

- [ ] **Step 4: 테스트 재실행**

Run: `cd app && flutter test test/book_registration_test.dart`
Expected: ALL PASS

- [ ] **Step 5: 커밋**

```bash
git add app/lib/core/utils/color_extractor.dart app/test/book_registration_test.dart
git commit -m "feat: ColorExtractor palette_generator 구현 + 유틸 테스트"
```

---

## Task 4: BookSpine — google_fonts 연동

**Files:**
- Modify: `app/lib/core/widgets/book_spine.dart:56-57, 107-113`

- [ ] **Step 1: book_spine.dart에 google_fonts import 추가**

파일 상단에 추가:
```dart
import 'package:google_fonts/google_fonts.dart';
```

- [ ] **Step 2: _fontFamily getter → _titleTextStyle 메서드로 교체**

기존 코드 (line 56-57):
```dart
  String get _fontFamily => book.spineFont ?? 'Pretendard';
```

교체:
```dart
  /// 책등 제목 TextStyle (google_fonts 사용, 실패 시 기본 폰트 폴백)
  TextStyle _titleTextStyle({required Color color, required double fontSize}) {
    final fontName = book.spineFont ?? 'Pretendard';
    try {
      return GoogleFonts.getFont(
        fontName,
        color: color,
        fontSize: fontSize,
        fontWeight: FontWeight.w700,
        letterSpacing: 0.5,
      );
    } catch (_) {
      return TextStyle(
        color: color,
        fontSize: fontSize,
        fontWeight: FontWeight.w700,
        letterSpacing: 0.5,
      );
    }
  }
```

- [ ] **Step 3: build()에서 TextStyle 적용**

기존 코드 (lines 105-113):
```dart
                  child: Text(
                    book.title,
                    style: TextStyle(
                      color: titleColor,
                      fontSize: 10,
                      fontWeight: FontWeight.w700,
                      fontFamily: _fontFamily,
                      letterSpacing: 0.5,
                    ),
                  ),
```

교체:
```dart
                  child: Text(
                    book.title,
                    style: _titleTextStyle(color: titleColor, fontSize: 10),
                  ),
```

- [ ] **Step 4: flutter analyze 실행**

Run: `cd app && flutter analyze`
Expected: No issues found

- [ ] **Step 5: 기존 bookshelf 테스트 통과 확인**

Run: `cd app && flutter test test/bookshelf_test.dart`
Expected: ALL PASS

- [ ] **Step 6: 커밋**

```bash
git add app/lib/core/widgets/book_spine.dart
git commit -m "feat: BookSpine google_fonts 연동 — 런타임 폰트 로드"
```

---

## Task 5: BookRegistrationService 구현

**Files:**
- Create: `app/lib/core/services/book_registration_service.dart`
- Test: `app/test/book_registration_test.dart` (추가)

- [ ] **Step 1: BookRegistrationService 구현**

```dart
// app/lib/core/services/book_registration_service.dart
import 'package:flutter/foundation.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../models/book.dart';
import '../models/user_book.dart';
import '../utils/color_extractor.dart';
import '../utils/font_assigner.dart';

class BookRegistrationService {
  final SupabaseClient _supabase;

  BookRegistrationService([SupabaseClient? client])
      : _supabase = client ?? Supabase.instance.client;

  /// 책 등록 파이프라인 (동기 + 비동기 백그라운드)
  ///
  /// 1. books upsert (ISBN 중복 방지)
  /// 2. user_books insert
  /// 3. (비동기) dominant color 추출 + spine font 배정
  Future<void> registerBook(Book book, BookStatus status) async {
    final userId = _supabase.auth.currentUser?.id;
    if (userId == null) throw Exception('로그인이 필요합니다');

    // 1. books upsert — id 제외하여 DB가 UUID 자동 생성
    final upsertData = book.toJsonForUpsert();
    final List<dynamic> upsertResult;

    if (book.isbn != null && book.isbn!.isNotEmpty) {
      upsertResult = await _supabase
          .from('books')
          .upsert(upsertData, onConflict: 'isbn')
          .select('id');
    } else {
      upsertResult = await _supabase
          .from('books')
          .insert(upsertData)
          .select('id');
    }

    final bookId = upsertResult.first['id'] as String;

    // 2. user_books insert
    await _supabase.from('user_books').insert({
      'user_id': userId,
      'book_id': bookId,
      'status': status.toJson(),
    });

    // 3. 비동기 백그라운드 — 실패해도 사용자 흐름 차단하지 않음
    _enrichBookAsync(bookId, book);
  }

  /// 유저 서재에 해당 ISBN의 책이 있는지 확인
  Future<bool> isBookInShelf(String? isbn) async {
    if (isbn == null || isbn.isEmpty) return false;

    final userId = _supabase.auth.currentUser?.id;
    if (userId == null) return false;

    final result = await _supabase
        .from('user_books')
        .select('id, books!inner(isbn)')
        .eq('user_id', userId)
        .eq('books.isbn', isbn)
        .limit(1);

    return (result as List).isNotEmpty;
  }

  /// 유저 서재의 모든 ISBN 목록 조회
  Future<Set<String>> getShelfIsbns() async {
    final userId = _supabase.auth.currentUser?.id;
    if (userId == null) return {};

    final result = await _supabase
        .from('user_books')
        .select('books(isbn)')
        .eq('user_id', userId);

    return (result as List)
        .map((row) => row['books']?['isbn'] as String?)
        .whereType<String>()
        .where((isbn) => isbn.isNotEmpty)
        .toSet();
  }

  /// 백그라운드: 색상 추출 + 폰트 배정
  Future<void> _enrichBookAsync(String bookId, Book book) async {
    try {
      final updates = <String, dynamic>{};

      // dominant color 추출
      if (book.coverUrl != null && book.coverUrl!.isNotEmpty) {
        final colors = await ColorExtractor.extractFromUrl(book.coverUrl!);
        if (colors.isNotEmpty) {
          updates['dominant_colors'] = colors;
        }
      }

      // spine font 배정
      final font = FontAssigner.assignFont(
        genre: book.genre,
        description: book.description,
      );
      if (font != FontAssigner.defaultFont) {
        updates['spine_font'] = font;
      }

      // DB 업데이트
      if (updates.isNotEmpty) {
        await _supabase.from('books').update(updates).eq('id', bookId);
      }
    } catch (e) {
      debugPrint('책 메타데이터 보강 실패 (bookId: $bookId): $e');
    }
  }
}
```

- [ ] **Step 2: flutter analyze**

Run: `cd app && flutter analyze`
Expected: No issues found

- [ ] **Step 3: 테스트 실행**

Run: `cd app && flutter test test/book_registration_test.dart`
Expected: ALL PASS

- [ ] **Step 4: 커밋**

```bash
git add app/lib/core/services/book_registration_service.dart
git commit -m "feat: BookRegistrationService — 등록 파이프라인 오케스트레이션"
```

---

## Task 6: BookshelfProvider — addBook mutation 추가

**Files:**
- Modify: `app/lib/features/bookshelf/providers/bookshelf_provider.dart`

- [ ] **Step 1: import 추가 + registrationServiceProvider 생성**

파일 상단에 추가:
```dart
import 'package:flutter_riverpod/flutter_riverpod.dart' show WidgetRef;
import '../../../core/services/book_registration_service.dart';
import '../../../core/models/book.dart';
```

파일 끝에 추가:
```dart
final registrationServiceProvider =
    Provider<BookRegistrationService>((ref) => BookRegistrationService());

/// 서재에 책 추가 (등록 파이프라인 실행 + 리프레시)
Future<void> addBookToShelf(WidgetRef ref, Book book, BookStatus status) async {
  final service = ref.read(registrationServiceProvider);
  await service.registerBook(book, status);
  ref.invalidate(bookshelfProvider);
}
```

- [ ] **Step 3: flutter analyze**

Run: `cd app && flutter analyze`
Expected: No issues found

- [ ] **Step 4: 커밋**

```bash
git add app/lib/features/bookshelf/providers/bookshelf_provider.dart
git commit -m "feat: bookshelf provider — addBookToShelf mutation 추가"
```

---

## Task 7: 검색 결과 — "추가됨" 뱃지 + 바텀시트

**Files:**
- Modify: `app/lib/features/search/widgets/book_search_result_card.dart`
- Modify: `app/lib/features/search/providers/book_search_provider.dart`
- Modify: `app/lib/features/search/screens/book_search_screen.dart`

- [ ] **Step 1: BookSearchResultCard에 isAdded 파라미터 추가**

`app/lib/features/search/widgets/book_search_result_card.dart` 수정:

기존:
```dart
class BookSearchResultCard extends StatelessWidget {
  final Book book;
  final VoidCallback? onTap;

  const BookSearchResultCard({
    super.key,
    required this.book,
    this.onTap,
  });
```

교체:
```dart
class BookSearchResultCard extends StatelessWidget {
  final Book book;
  final VoidCallback? onTap;
  final bool isAdded;

  const BookSearchResultCard({
    super.key,
    required this.book,
    this.onTap,
    this.isAdded = false,
  });
```

build()의 Row children 마지막에 추가 (Expanded 위젯 뒤, `]` 앞):
```dart
            // 추가됨 뱃지
            if (isAdded)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: Colors.grey[200],
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  '추가됨',
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        color: Colors.grey[600],
                      ),
                ),
              ),
```

InkWell의 onTap을 조건부로 변경:
```dart
      onTap: isAdded ? null : onTap,
```

- [ ] **Step 2: BookSearchProvider에 서재 ISBN 추적 추가**

`app/lib/features/search/providers/book_search_provider.dart` 수정:

import 추가:
```dart
import '../../../core/services/book_registration_service.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';
```

BookSearchState에 shelfIsbns 필드 추가:
```dart
class BookSearchState {
  final BookSearchStatus status;
  final List<Book> results;
  final String? errorMessage;
  final Set<String> shelfIsbns;

  const BookSearchState({
    this.status = BookSearchStatus.idle,
    this.results = const [],
    this.errorMessage,
    this.shelfIsbns = const {},
  });

  BookSearchState copyWith({
    BookSearchStatus? status,
    List<Book>? results,
    String? errorMessage,
    Set<String>? shelfIsbns,
  }) {
    return BookSearchState(
      status: status ?? this.status,
      results: results ?? this.results,
      errorMessage: errorMessage,
      shelfIsbns: shelfIsbns ?? this.shelfIsbns,
    );
  }
}
```

BookSearchNotifier에 서재 ISBN 로드 + 추가 시 갱신 메서드 추가:
```dart
class BookSearchNotifier extends StateNotifier<BookSearchState> {
  final BookSearchService _service;
  final BookRegistrationService _registrationService;
  Timer? _debounce;

  BookSearchNotifier(this._service, this._registrationService)
      : super(const BookSearchState()) {
    _loadShelfIsbns();
  }

  Future<void> _loadShelfIsbns() async {
    try {
      final isbns = await _registrationService.getShelfIsbns();
      state = state.copyWith(shelfIsbns: isbns);
    } catch (_) {}
  }

  /// 책 추가 후 ISBN을 로컬 상태에 반영
  void markAsAdded(String? isbn) {
    if (isbn == null || isbn.isEmpty) return;
    state = state.copyWith(shelfIsbns: {...state.shelfIsbns, isbn});
  }

  // 기존 메서드들 — shelfIsbns 보존하도록 수정:

  Future<void> _performSearch(String query) async {
    state = state.copyWith(status: BookSearchStatus.loading);

    try {
      final results = await _service.search(query);
      state = state.copyWith(
        status: BookSearchStatus.loaded,
        results: results,
      );
    } catch (e) {
      state = state.copyWith(
        status: BookSearchStatus.error,
        errorMessage: e.toString(),
      );
    }
  }

  void clear() {
    _debounce?.cancel();
    state = BookSearchState(shelfIsbns: state.shelfIsbns);
  }

  // search(), dispose() 기존 유지
```

bookSearchProvider 생성자 수정 (provider 주입으로 테스트 호환):
```dart
final bookSearchProvider =
    StateNotifierProvider<BookSearchNotifier, BookSearchState>((ref) {
  return BookSearchNotifier(
    ref.watch(bookSearchServiceProvider),
    ref.watch(registrationServiceProvider),
  );
});
```

- [ ] **Step 3: BookSearchScreen — 바텀시트 + 등록 연결**

`app/lib/features/search/screens/book_search_screen.dart` 전체 교체:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/models/book.dart';
import '../../../core/models/user_book.dart';
import '../../bookshelf/providers/bookshelf_provider.dart';
import '../providers/book_search_provider.dart';
import '../widgets/book_search_result_card.dart';

class BookSearchScreen extends ConsumerStatefulWidget {
  const BookSearchScreen({super.key});

  @override
  ConsumerState<BookSearchScreen> createState() => _BookSearchScreenState();
}

class _BookSearchScreenState extends ConsumerState<BookSearchScreen> {
  final _controller = TextEditingController();
  bool _isRegistering = false;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _showStatusBottomSheet(Book book) async {
    if (_isRegistering) return;

    final status = await showModalBottomSheet<BookStatus>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text('읽기 상태 선택', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            ),
            ListTile(
              leading: const Icon(Icons.auto_stories),
              title: const Text('읽는 중'),
              onTap: () => Navigator.pop(context, BookStatus.reading),
            ),
            ListTile(
              leading: const Icon(Icons.check_circle_outline),
              title: const Text('다 읽었어요'),
              onTap: () => Navigator.pop(context, BookStatus.read),
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );

    if (status == null || !mounted) return;

    setState(() => _isRegistering = true);

    try {
      await addBookToShelf(ref, book, status);
      ref.read(bookSearchProvider.notifier).markAsAdded(book.isbn);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${book.title} 서재에 추가됨')),
        );
      }
    } catch (e) {
      if (mounted) {
        final message = e.toString().contains('unique')
            ? '이미 서재에 있어요'
            : '추가 실패: $e';
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(message)),
        );
      }
    } finally {
      if (mounted) setState(() => _isRegistering = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final searchState = ref.watch(bookSearchProvider);

    return Scaffold(
      appBar: AppBar(
        title: TextField(
          controller: _controller,
          autofocus: true,
          decoration: const InputDecoration(
            hintText: '책 제목 또는 저자 검색',
            border: InputBorder.none,
          ),
          onChanged: (query) {
            ref.read(bookSearchProvider.notifier).search(query);
          },
        ),
        actions: [
          if (_controller.text.isNotEmpty)
            IconButton(
              icon: const Icon(Icons.clear),
              onPressed: () {
                _controller.clear();
                ref.read(bookSearchProvider.notifier).clear();
              },
            ),
        ],
      ),
      body: switch (searchState.status) {
        BookSearchStatus.idle => const Center(
            child: Text('책을 검색해보세요'),
          ),
        BookSearchStatus.loading => const Center(
            child: CircularProgressIndicator(),
          ),
        BookSearchStatus.error => Center(
            child: Text('검색 실패: ${searchState.errorMessage}'),
          ),
        BookSearchStatus.loaded => searchState.results.isEmpty
            ? const Center(child: Text('검색 결과가 없습니다'))
            : ListView.separated(
                itemCount: searchState.results.length,
                separatorBuilder: (context, index) => const Divider(height: 1),
                itemBuilder: (context, index) {
                  final book = searchState.results[index];
                  final isAdded = book.isbn != null &&
                      searchState.shelfIsbns.contains(book.isbn);
                  return BookSearchResultCard(
                    book: book,
                    isAdded: isAdded,
                    onTap: () => _showStatusBottomSheet(book),
                  );
                },
              ),
      },
    );
  }
}
```

- [ ] **Step 4: flutter analyze**

Run: `cd app && flutter analyze`
Expected: No issues found

- [ ] **Step 5: 기존 테스트 통과 확인**

Run: `cd app && flutter test`
Expected: ALL PASS

- [ ] **Step 6: 커밋**

```bash
git add app/lib/features/search/ app/lib/features/bookshelf/providers/bookshelf_provider.dart
git commit -m "feat: 검색→서재 추가 연결 — 바텀시트 + 추가됨 뱃지 + 중복 방지"
```

---

## Task 8: 위젯 테스트 추가 + 최종 검증

**Files:**
- Modify: `app/test/book_search_test.dart`

- [ ] **Step 1: 검색 결과 카드 "추가됨" 뱃지 테스트**

`app/test/book_search_test.dart`에 추가:
```dart
  testWidgets('BookSearchResultCard shows 추가됨 badge when isAdded', (tester) async {
    final book = Book(id: '1', title: '테스트 책', isbn: '1234567890');

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: BookSearchResultCard(
            book: book,
            isAdded: true,
          ),
        ),
      ),
    );

    expect(find.text('추가됨'), findsOneWidget);
  });

  testWidgets('BookSearchResultCard hides badge when not added', (tester) async {
    final book = Book(id: '1', title: '테스트 책', isbn: '1234567890');

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: BookSearchResultCard(
            book: book,
            isAdded: false,
          ),
        ),
      ),
    );

    expect(find.text('추가됨'), findsNothing);
  });
```

- [ ] **Step 2: 전체 테스트 실행**

Run: `cd app && flutter test`
Expected: ALL PASS

- [ ] **Step 3: flutter analyze**

Run: `cd app && flutter analyze`
Expected: No issues found

- [ ] **Step 4: 커밋**

```bash
git add app/test/
git commit -m "test: 검색 결과 카드 추가됨 뱃지 위젯 테스트"
```

---

## 작업 순서 요약

| Task | 내용 | 의존성 |
|------|------|--------|
| 1 | DB 마이그레이션 + 패키지 추가 | 없음 |
| 2 | Book.toJsonForUpsert() | 없음 |
| 3 | ColorExtractor 실제 구현 | Task 1 (palette_generator) |
| 4 | BookSpine google_fonts 연동 | Task 1 (google_fonts) |
| 5 | BookRegistrationService | Task 2, 3 |
| 6 | BookshelfProvider addBook | Task 5 |
| 7 | 검색 UI — 바텀시트 + 뱃지 | Task 5, 6 |
| 8 | 위젯 테스트 + 최종 검증 | Task 7 |
