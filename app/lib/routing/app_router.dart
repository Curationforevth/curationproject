import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../features/auth/providers/auth_provider.dart';
import '../features/auth/screens/login_screen.dart';
import '../features/book_detail/screens/book_detail_screen.dart';
import '../features/feedback/screens/feedback_flow_screen.dart';
import '../features/home/screens/home_screen.dart';
import '../features/library/screens/library_screen.dart';
import '../features/register/screens/register_flow_screen.dart';
import '../features/search/screens/book_search_screen.dart';
import '../features/shell/screens/app_shell.dart';

GoRouter createRouter(AuthNotifier authNotifier) {
  return GoRouter(
    initialLocation: '/',
    refreshListenable: authNotifier,
    redirect: (context, state) {
      final session = Supabase.instance.client.auth.currentSession;
      final isLoginRoute = state.matchedLocation == '/login';

      if (session == null && !isLoginRoute) return '/login';
      if (session != null && isLoginRoute) return '/';
      return null;
    },
    routes: [
      StatefulShellRoute.indexedStack(
        builder: (context, state, navigationShell) =>
            AppShell(navigationShell: navigationShell),
        branches: [
          // Branch 0: Home
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/',
                builder: (context, state) => const HomeScreen(),
              ),
            ],
          ),
          // Branch 1: Register placeholder (never navigated to directly)
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/register-placeholder',
                builder: (context, state) => const SizedBox.shrink(),
              ),
            ],
          ),
          // Branch 2: Library
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/library',
                builder: (context, state) => const LibraryScreen(),
              ),
            ],
          ),
        ],
      ),
      // Routes outside the shell (pushed over it)
      GoRoute(
        path: '/register',
        builder: (context, state) => const RegisterFlowScreen(),
      ),
      GoRoute(
        path: '/login',
        builder: (context, state) => const LoginScreen(),
      ),
      GoRoute(
        path: '/search',
        builder: (context, state) => const BookSearchScreen(),
      ),
      GoRoute(
        path: '/book/:userBookId',
        builder: (context, state) => BookDetailScreen(
          userBookId: state.pathParameters['userBookId']!,
        ),
      ),
      GoRoute(
        path: '/feedback/:userBookId',
        builder: (context, state) => FeedbackFlowScreen(
          userBookId: state.pathParameters['userBookId']!,
        ),
      ),
    ],
  );
}
