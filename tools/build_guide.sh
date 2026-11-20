#!/usr/bin/env bash
# 由 docs/参赛指南.md 生成 docs/参赛指南.pdf
# 依赖：pandoc、google-chrome（或 chromium）、Noto Sans CJK 字体
set -euo pipefail
cd "$(dirname "$0")/../docs"

CHROME="$(command -v google-chrome || command -v chromium || command -v chromium-browser)"

pandoc 参赛指南.md -f markdown -t html5 --standalone --no-highlight \
  --metadata title="单臂柔性机加单元调度 · 参赛指南" --metadata lang=zh-CN \
  --css guide.css -o guide_build.html
# pandoc 的 standalone 模板会把 title 再输出成一个页眉标题，这里去掉（封面里已经有了）
python3 - <<'EOF'
import re
s = open('guide_build.html', encoding='utf-8').read()
s = re.sub(r'<header id="title-block-header">.*?</header>', '', s, flags=re.S)
open('guide_build.html', 'w', encoding='utf-8').write(s)
EOF

"$CHROME" --headless=new --no-sandbox --disable-gpu --no-pdf-header-footer \
  --print-to-pdf=参赛指南.pdf "file://$PWD/guide_build.html" 2>/dev/null
rm -f guide_build.html
ls -la 参赛指南.pdf
