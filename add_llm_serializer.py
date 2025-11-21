import os

file_path = 'erates/views.py'

with open(file_path, 'r') as f:
    lines = f.readlines()

# Find DefaultersSummarySerializer
target_index = -1
for i, line in enumerate(lines):
    if 'DefaultersSummarySerializer,' in line:
        target_index = i
        break

if target_index != -1:
    # Check if LLMQuerySerializer is already there
    if 'LLMQuerySerializer,' not in lines[target_index + 1]:
        lines.insert(target_index + 1, '    LLMQuerySerializer,\n')
        
        # Check if query_qwen is imported
        # It should be after the closing ')'
        # Find the closing ')'
        closing_paren_index = -1
        for j in range(target_index + 1, len(lines)):
            if lines[j].strip() == ')':
                closing_paren_index = j
                break
        
        if closing_paren_index != -1:
            if 'from .llm_utils import query_qwen' not in lines[closing_paren_index + 1]:
                lines.insert(closing_paren_index + 1, 'from .llm_utils import query_qwen\n')

with open(file_path, 'w') as f:
    f.writelines(lines)

print("Added LLMQuerySerializer and query_qwen")
