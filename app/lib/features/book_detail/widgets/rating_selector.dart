import 'package:flutter/material.dart';
import '../../../core/theme/app_colors.dart';

class RatingSelector extends StatelessWidget {
  final String? currentRating;
  final bool disabled;
  final ValueChanged<String> onChanged;

  const RatingSelector({
    super.key,
    this.currentRating,
    this.disabled = false,
    required this.onChanged,
  });

  static const _options = [
    ('good', '좋았다', Icons.thumb_up_outlined, Icons.thumb_up),
    ('bad', '별로', Icons.thumb_down_outlined, Icons.thumb_down),
  ];

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '이 책 어때요?',
          style: Theme.of(context).textTheme.titleSmall?.copyWith(
                color: AppColors.textPrimary,
                fontWeight: FontWeight.w600,
              ),
        ),
        const SizedBox(height: 12),
        IgnorePointer(
          ignoring: disabled,
          child: Opacity(
            opacity: disabled ? 0.6 : 1.0,
            child: Row(
              children: _options.map((option) {
                final (value, label, iconOutlined, iconFilled) = option;
                final isSelected = currentRating == value;
                return Expanded(
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 4),
                    child: _RatingButton(
                      label: label,
                      icon: isSelected ? iconFilled : iconOutlined,
                      isSelected: isSelected,
                      onTap: () => onChanged(value),
                    ),
                  ),
                );
              }).toList(),
            ),
          ),
        ),
      ],
    );
  }
}

class _RatingButton extends StatelessWidget {
  final String label;
  final IconData icon;
  final bool isSelected;
  final VoidCallback onTap;

  const _RatingButton({
    required this.label,
    required this.icon,
    required this.isSelected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Material(
      color: isSelected ? AppColors.primary.withValues(alpha: 0.1) : Colors.transparent,
      borderRadius: BorderRadius.circular(12),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 12),
          decoration: BoxDecoration(
            border: Border.all(
              color: isSelected ? AppColors.primary : AppColors.shelf,
              width: isSelected ? 1.5 : 1,
            ),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Column(
            children: [
              Icon(
                icon,
                size: 24,
                color: isSelected ? AppColors.primary : AppColors.textSecondary,
              ),
              const SizedBox(height: 4),
              Text(
                label,
                style: TextStyle(
                  fontSize: 12,
                  color: isSelected ? AppColors.primary : AppColors.textSecondary,
                  fontWeight: isSelected ? FontWeight.w600 : FontWeight.normal,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
