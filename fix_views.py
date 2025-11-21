import os

file_path = 'erates/views.py'

with open(file_path, 'r') as f:
    lines = f.readlines()

# 1. Add APIView import
# Check if already there
if 'from rest_framework.views import APIView\n' not in lines:
    # Find line starting with 'from rest_framework import viewsets'
    for i, line in enumerate(lines):
        if line.startswith('from rest_framework import viewsets'):
            lines.insert(i + 1, 'from rest_framework.views import APIView\n')
            break

# 2. Add missing serializers and query_qwen
# Find 'ComprehensiveReportSerializer,'
target_line_index = -1
for i, line in enumerate(lines):
    if 'ComprehensiveReportSerializer,' in line:
        target_line_index = i
        break

if target_line_index != -1:
    # Check if already added
    if 'LLMQuerySerializer,' not in lines[target_line_index + 5]: # rough check
        new_lines = [
            '    TransactionVolumeSerializer,\n',
            '    TopUserSerializer,\n',
            '    RevenueReportSerializer,\n',
            '    \n',
            '    #defaulters serializers\n',
            '    DefaulterSerializer,\n',
            '    DefaultersSummarySerializer,\n',
            '    LLMQuerySerializer,\n',
            ')\n',
            'from .llm_utils import query_qwen\n'
        ]
        # Remove the closing parenthesis line which should be after ComprehensiveReportSerializer
        # Actually, in the original file, it was:
        #     ComprehensiveReportSerializer,
        # )
        # So we need to replace the closing ')' with the new lines.
        
        # Let's verify what's after ComprehensiveReportSerializer
        if lines[target_line_index + 1].strip() == ')':
             lines[target_line_index + 1:target_line_index + 2] = new_lines
             # Note: new_lines ends with 'from .llm_utils import query_qwen\n'
             # The original file had 'from .shapefile_utils import ShapefileImporter' after ')'
             # So we are good.

# 3. Append LLMQueryView
# Check if already there
if 'class LLMQueryView(APIView):' not in ''.join(lines[-50:]):
    llm_view_code = [
        '\n',
        '\n',
        'class LLMQueryView(APIView):\n',
        '    """\n',
        '    View to handle queries to the Qwen LLM.\n',
        '    """\n',
        '    permission_classes = [permissions.IsAuthenticated]\n',
        '    serializer_class = LLMQuerySerializer\n',
        '\n',
        '    @extend_schema(\n',
        '        summary="Query Qwen LLM",\n',
        '        description="Send a natural language query to the Qwen LLM with system context.",\n',
        '        tags=[\'LLM\'],\n',
        '        request=LLMQuerySerializer,\n',
        '        responses={\n',
        '            200: OpenApiResponse(description="LLM Analysis Response"),\n',
        '            400: OpenApiResponse(description="Invalid request"),\n',
        '            500: OpenApiResponse(description="LLM API Error"),\n',
        '        },\n',
        '    )\n',
        '    def post(self, request):\n',
        '        serializer = self.serializer_class(data=request.data)\n',
        '        if serializer.is_valid():\n',
        '            query = serializer.validated_data[\'query\']\n',
        '            api_url = serializer.validated_data.get(\'api_url\')\n',
        '            \n',
        '            if not api_url:\n',
        '                 return Response(\n',
        '                    {"error": "QWEN_API_URL not provided in request or environment"},\n',
        '                    status=status.HTTP_400_BAD_REQUEST\n',
        '                )\n',
        '\n',
        '            result = query_qwen(query, api_url)\n',
        '            \n',
        '            if "error" in result:\n',
        '                return Response(result, status=status.HTTP_500_INTERNAL_SERVER_ERROR)\n',
        '            \n',
        '            return Response(result)\n',
        '        \n',
        '        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)\n'
    ]
    lines.extend(llm_view_code)

with open(file_path, 'w') as f:
    f.writelines(lines)

print("Successfully updated views.py")
