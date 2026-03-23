import 'dart:math';
import 'package:flutter/material.dart';
import '../../../core/models/reflection_prompt.dart';
import '../../../core/theme/app_colors.dart';

/// 속성 칩 (UI 전용, 저장하지 않음)
const _topicChips = [
  ('character', '캐릭터'),
  ('writing_style', '문체'),
  ('plot', '전개'),
  ('atmosphere', '분위기'),
  ('message', '메시지'),
  ('worldbuilding', '세계관'),
];

/// 속성 칩별 placeholder 힌트
const _topicHints = {
  'character': '캐릭터에 대해 적어보세요... 어떤 인물이 기억에 남나요?',
  'writing_style': '문체에 대해 적어보세요... 어떤 문장이 좋았나요?',
  'plot': '전개에 대해 적어보세요... 어떤 장면이 인상적이었나요?',
  'atmosphere': '분위기에 대해 적어보세요... 어떤 느낌이었나요?',
  'message': '메시지에 대해 적어보세요... 어떤 생각이 들었나요?',
  'worldbuilding': '세계관에 대해 적어보세요... 어떤 세계가 그려졌나요?',
};

class ReviewTextSection extends StatefulWidget {
  final String? initialText;
  final List<ReflectionPrompt> prompts;
  final ValueChanged<String> onSave;

  const ReviewTextSection({
    super.key,
    this.initialText,
    required this.prompts,
    required this.onSave,
  });

  @override
  State<ReviewTextSection> createState() => _ReviewTextSectionState();
}

class _ReviewTextSectionState extends State<ReviewTextSection> {
  late final TextEditingController _controller;
  bool _helpExpanded = false;
  String? _selectedTopic;
  String _placeholder = '이 책에 대해 자유롭게 적어보세요...';
  bool _hasChanges = false;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.initialText ?? '');
    _controller.addListener(() {
      final changed = _controller.text != (widget.initialText ?? '');
      if (changed != _hasChanges) {
        setState(() => _hasChanges = changed);
      }
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _onTopicTap(String category) {
    setState(() {
      _selectedTopic = _selectedTopic == category ? null : category;
      _placeholder = _selectedTopic != null
          ? _topicHints[_selectedTopic]!
          : '이 책에 대해 자유롭게 적어보세요...';
    });
  }

  void _onPromptTap(ReflectionPrompt prompt) {
    final current = _controller.text;
    final separator = current.isNotEmpty && !current.endsWith('\n') ? '\n' : '';
    _controller.text = '$current$separator${prompt.question}\n';
    _controller.selection = TextSelection.collapsed(
      offset: _controller.text.length,
    );
  }

  ReflectionPrompt _getRandomPrompt() {
    final filtered = _selectedTopic != null
        ? widget.prompts.where((p) => p.category == _selectedTopic).toList()
        : <ReflectionPrompt>[];

    // 카테고리 매칭 결과가 없으면 범용 질문(category == null)으로 폴백
    final pool = filtered.isNotEmpty
        ? filtered
        : widget.prompts.where((p) => p.category == null).toList();

    if (pool.isEmpty) return widget.prompts.first;
    return pool[Random().nextInt(pool.length)];
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // 텍스트 입력
        TextField(
          controller: _controller,
          maxLines: 5,
          minLines: 3,
          decoration: InputDecoration(
            hintText: _placeholder,
            hintStyle: TextStyle(color: AppColors.textSecondary.withValues(alpha: 0.6)),
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: BorderSide(color: AppColors.shelf),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(12),
              borderSide: BorderSide(color: AppColors.primary, width: 1.5),
            ),
            contentPadding: const EdgeInsets.all(16),
          ),
        ),

        const SizedBox(height: 8),

        // 저장 버튼 + 도움 링크
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            // 글쓰기 도움 토글
            GestureDetector(
              onTap: () => setState(() => _helpExpanded = !_helpExpanded),
              child: Text(
                _helpExpanded ? '도움 접기' : '뭘 쓸지 모르겠다면?',
                style: TextStyle(
                  color: AppColors.primary,
                  fontSize: 13,
                ),
              ),
            ),
            // 저장 버튼
            if (_hasChanges)
              FilledButton(
                onPressed: () {
                  widget.onSave(_controller.text);
                  setState(() => _hasChanges = false);
                },
                style: FilledButton.styleFrom(
                  backgroundColor: AppColors.primary,
                  padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                ),
                child: const Text('저장', style: TextStyle(fontSize: 13)),
              ),
          ],
        ),

        // 글쓰기 도움 패널 (접기/펼치기)
        if (_helpExpanded) ...[
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: AppColors.surfaceVariant,
              borderRadius: BorderRadius.circular(12),
              border: Border(
                left: BorderSide(color: AppColors.primary, width: 3),
              ),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // 속성 칩
                Text(
                  '이런 주제로 써보세요',
                  style: TextStyle(
                    fontSize: 12,
                    color: AppColors.textSecondary,
                  ),
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: _topicChips.map((chip) {
                    final (category, label) = chip;
                    final isSelected = _selectedTopic == category;
                    return ActionChip(
                      label: Text(label),
                      onPressed: () => _onTopicTap(category),
                      backgroundColor: isSelected
                          ? AppColors.primary.withValues(alpha: 0.15)
                          : Colors.white,
                      side: BorderSide(
                        color: isSelected ? AppColors.primary : AppColors.shelf,
                      ),
                      labelStyle: TextStyle(
                        fontSize: 12,
                        color: isSelected ? AppColors.primary : AppColors.textSecondary,
                      ),
                    );
                  }).toList(),
                ),

                const SizedBox(height: 16),

                // 리플렉션 질문
                Text(
                  '또는 질문에 답해보세요',
                  style: TextStyle(
                    fontSize: 12,
                    color: AppColors.textSecondary,
                  ),
                ),
                const SizedBox(height: 8),
                if (widget.prompts.isNotEmpty)
                  _ReflectionQuestionCard(
                    prompt: _getRandomPrompt(),
                    onTap: _onPromptTap,
                    onRefresh: () => setState(() {}),
                  ),
              ],
            ),
          ),
        ],
      ],
    );
  }
}

class _ReflectionQuestionCard extends StatelessWidget {
  final ReflectionPrompt prompt;
  final ValueChanged<ReflectionPrompt> onTap;
  final VoidCallback onRefresh;

  const _ReflectionQuestionCard({
    required this.prompt,
    required this.onTap,
    required this.onRefresh,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: () => onTap(prompt),
      borderRadius: BorderRadius.circular(8),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(
          children: [
            Expanded(
              child: Text(
                '"${prompt.question}"',
                style: TextStyle(
                  fontSize: 13,
                  color: AppColors.textPrimary,
                ),
              ),
            ),
            const SizedBox(width: 8),
            GestureDetector(
              onTap: onRefresh,
              child: Icon(
                Icons.refresh,
                size: 20,
                color: AppColors.primary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
