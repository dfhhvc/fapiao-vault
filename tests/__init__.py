"""测试包：把 src 与项目根加入 sys.path，便于直接 `python -m unittest`。"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for p in (_ROOT / "src", _ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)
