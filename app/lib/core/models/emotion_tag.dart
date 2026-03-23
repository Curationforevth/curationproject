class EmotionTag {
  final String id;
  final String label;
  final int sortOrder;
  final bool isActive;

  const EmotionTag({
    required this.id,
    required this.label,
    required this.sortOrder,
    required this.isActive,
  });

  factory EmotionTag.fromJson(Map<String, dynamic> json) {
    return EmotionTag(
      id: json['id'] as String,
      label: json['label'] as String,
      sortOrder: json['sort_order'] as int? ?? 0,
      isActive: json['is_active'] as bool? ?? true,
    );
  }
}
