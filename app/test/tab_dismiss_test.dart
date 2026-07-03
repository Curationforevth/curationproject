import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:curation_app/features/shell/screens/app_shell.dart';

/// 탭바 탭 = 어디서든 해당 탭 루트로 (열린 바텀시트/푸시 화면 전부 닫힘).
///
/// 회귀 배경(2026-07-03 Eden 실기기): showModalBottomSheet 는 가장 가까운
/// 내비게이터(= StatefulShellRoute 의 "브랜치" 내비게이터)에 route 를 올린다.
/// 루트 내비게이터만 popUntil 하던 첫 수정(PR#57)은 시트를 못 닫았고,
/// goBranch(initialLocation)도 pageless route(시트)는 걷어내지 못했다.
/// → AppShell 이 브랜치 navigatorKey 들을 받아 전부 루트까지 pop 한다.

late List<GlobalKey<NavigatorState>> _branchKeys;

GoRouter _buildRouter() {
  _branchKeys = [
    GlobalKey<NavigatorState>(debugLabel: 'branch-home'),
    GlobalKey<NavigatorState>(debugLabel: 'branch-register'),
    GlobalKey<NavigatorState>(debugLabel: 'branch-library'),
  ];
  return GoRouter(
    initialLocation: '/library',
    routes: [
      StatefulShellRoute.indexedStack(
        builder: (context, state, navigationShell) => AppShell(
          navigationShell: navigationShell,
          branchNavigatorKeys: _branchKeys,
        ),
        branches: [
          StatefulShellBranch(
            navigatorKey: _branchKeys[0],
            routes: [
              GoRoute(
                path: '/',
                builder: (_, __) => const Center(child: Text('HOME-ROOT')),
              ),
            ],
          ),
          StatefulShellBranch(
            navigatorKey: _branchKeys[1],
            routes: [
              GoRoute(
                path: '/register-placeholder',
                builder: (_, __) => const SizedBox.shrink(),
              ),
            ],
          ),
          StatefulShellBranch(
            navigatorKey: _branchKeys[2],
            routes: [
              GoRoute(
                path: '/library',
                builder: (_, __) => const _LibraryStub(),
              ),
            ],
          ),
        ],
      ),
      GoRoute(
        path: '/register',
        builder: (_, __) => const Center(child: Text('REGISTER-PAGE')),
      ),
    ],
  );
}

class _LibraryStub extends StatelessWidget {
  const _LibraryStub();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: ElevatedButton(
        // 실제 앱과 동일 경로: 화면 context → 가장 가까운(브랜치) 내비게이터
        onPressed: () => showModalBottomSheet(
          context: context,
          builder: (_) => const SizedBox(
            height: 300,
            child: Center(child: Text('SHEET-CONTENT')),
          ),
        ),
        child: const Text('open-sheet'),
      ),
    );
  }
}

Future<void> _pumpApp(WidgetTester tester) async {
  await tester.pumpWidget(MaterialApp.router(routerConfig: _buildRouter()));
  await tester.pumpAndSettle();
}

Future<void> _openSheet(WidgetTester tester) async {
  await tester.tap(find.text('open-sheet'));
  await tester.pumpAndSettle();
  expect(find.text('SHEET-CONTENT'), findsOneWidget);
}

void main() {
  testWidgets('서재에서 시트 열림 → 홈 탭: 시트 닫히고 홈 루트', (tester) async {
    await _pumpApp(tester);
    await _openSheet(tester);

    await tester.tap(find.text('홈'));
    await tester.pumpAndSettle();

    expect(find.text('SHEET-CONTENT'), findsNothing);
    expect(find.text('HOME-ROOT'), findsOneWidget);
  });

  testWidgets('서재에서 시트 열림 → 서재 재탭: 시트 닫히고 서재 루트', (tester) async {
    await _pumpApp(tester);
    await _openSheet(tester);

    await tester.tap(find.text('서재'));
    await tester.pumpAndSettle();

    expect(find.text('SHEET-CONTENT'), findsNothing);
    expect(find.text('open-sheet'), findsOneWidget);
  });

  testWidgets('다른 탭에 남아있던 시트도 그 탭으로 돌아가면 닫혀 있다', (tester) async {
    await _pumpApp(tester);
    await _openSheet(tester);

    // 홈으로 갔다가 서재로 복귀 — 서재 브랜치에 남아있던 시트가 없어야 한다
    await tester.tap(find.text('홈'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('서재'));
    await tester.pumpAndSettle();

    expect(find.text('SHEET-CONTENT'), findsNothing);
    expect(find.text('open-sheet'), findsOneWidget);
  });
}
