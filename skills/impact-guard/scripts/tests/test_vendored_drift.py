"""vendored 快照对拍 — 与 arch-hawkeye docgen/scripts 真相源字节一致。

doc-gen 迁出（2026-09-27，PR #250）时 java/layers/doctypes 三文件 vendored 于
_vendored/（字节级镜像，覆盖即升级）。本测试对拍同级检出的 arch-hawkeye：
真相源演化而快照未跟随 → fail，防止两侧 scanner 语义静默分叉（同一 Java 类
分层结论相左时极难排查，症状先于根因暴露）。

无 arch-hawkeye 检出（如 CI 未接线）时 SKIP——门禁实际生效于开发者
push 路径（pre-push 全量 pytest 含本套件）；CI 接线后可去掉 SKIP 语义。
"""

import filecmp
import os
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent          # skills/impact-guard/scripts
REPO_ROOT = SCRIPTS_DIR.parent.parent.parent                  # awesome-rules
VENDORED = SCRIPTS_DIR / "_vendored"

# 快照文件名 ↔ arch-hawkeye docgen/scripts/ 相对路径
PAIRS = [
    ("java.py", "scanner/java.py"),
    ("layers.py", "generator/layers.py"),
    ("doctypes.py", "doctypes.py"),
]


def _hawkeye_scripts() -> Path:
    root = Path(os.environ.get(
        "HAWKEYE_PATH", str(REPO_ROOT.parent / "arch-hawkeye"))).expanduser()
    return root / "docgen" / "scripts"


def test_vendored_matches_hawkeye_truth_source():
    scripts = _hawkeye_scripts()
    if not scripts.is_dir():
        pytest.skip(f"无 arch-hawkeye 检出（{scripts}），对拍跳过；"
                    "设 HAWKEYE_PATH 指向其检出根可启用")
    drifted = []
    for snap, truth in PAIRS:
        v, t = VENDORED / snap, scripts / truth
        if not t.is_file() or not filecmp.cmp(v, t, shallow=False):
            drifted.append(f"_vendored/{snap} ↔ docgen/scripts/{truth}")
    assert not drifted, (
        "vendored 快照 ≠ arch-hawkeye 真相源，用同名文件覆盖 _vendored/ 后跑本套件:\n  "
        + "\n  ".join(drifted))
