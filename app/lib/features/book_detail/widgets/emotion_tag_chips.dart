import 'package:flutter/material.dart';
import '../../../core/models/emotion_tag.dart';
import '../../../core/theme/app_colors.dart';

class EmotionTagChips extends StatelessWidget {
  final List<EmotionTag> options;
  final List<String> selectedIds;
  final ValueChanged<String> onToggle;

  const EmotionTagChips({
    super.key,
    required this.options,
    required this.selectedIds,
    required this.onToggle,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '이 책의 느낌은?',
          style: Theme.of(context).textTheme.titleSmall?.copyWith(
                color: AppColors.textPrimary,
                fontWeight: FontWeight.w600,
              ),
        ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: options.map((tag) {
            final isSelected = selectedIds.contains(tag.id);
            return FilterChip(
              label: Text(tag.label),
              selected: isSelected,
              onSelected: (_) => onToggle(tag.id),
              selectedColor: AppColors.primary.withValues(alpha: 0.15),
              checkmarkColor: AppColors.primary,
              side: BorderSide(
                color: isSelected ? AppColors.primary : AppColors.shelf,
              ),
              labelStyle: TextStyle(
                color: isSelected ? AppColors.primary : AppColors.textSecondary,
                fontWeight: isSelected ? FontWeight.w600 : FontWeight.normal,
                fontSize: 13,
              ),
            );
          }).toList(),
        ),
      ],
    );
  }
}
