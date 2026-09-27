"""复用桥 — 导入 vendored 的 JavaScanner / LayerIdentifier

doc-gen 于 2026-09-27 整体迁往 arch-hawkeye 仓（docgen/scripts/）；
java.py / layers.py / doctypes.py 按迁移时快照 vendored 于 _vendored/，
升级 = 用 arch-hawkeye 同名文件覆盖后跑本技能测试。
"""

import sys
from pathlib import Path

VENDORED = Path(__file__).resolve().parent / "_vendored"

if str(VENDORED) not in sys.path:
    sys.path.insert(0, str(VENDORED))

from java import JavaScanner          # noqa: E402,F401
from layers import LayerIdentifier    # noqa: E402,F401
