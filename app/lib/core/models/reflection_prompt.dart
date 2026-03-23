class ReflectionPrompt {
  final String id;
  final String question;
  final String? category;
  final bool isActive;

  const ReflectionPrompt({
    required this.id,
    required this.question,
    this.category,
    required this.isActive,
  });

  factory ReflectionPrompt.fromJson(Map<String, dynamic> json) {
    return ReflectionPrompt(
      id: json['id'] as String,
      question: json['question'] as String,
      category: json['category'] as String?,
      isActive: json['is_active'] as bool? ?? true,
    );
  }
}
