import 'package:go_router/go_router.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../features/auth/providers/auth_provider.dart';
import '../features/auth/screens/login_screen.dart';
import '../features/book_detail/screens/book_detail_screen.dart';
import '../features/bookshelf/screens/bookshelf_screen.dart';
import '../features/search/screens/book_search_screen.dart';

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
      GoRoute(
        path: '/',
        builder: (context, state) => const BookshelfScreen(),
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
    ],
  );
}
