"""从真实 main.py 中提取长任务放行相关方法，构造独立可测的宿主类。

直接抽取源码而不是复制实现，确保测试始终跟随 main.py 的真实代码，
同时避免为了这几个纯逻辑方法去加载整个 AstrBot 运行时。
"""

import ast
import textwrap
import time
from pathlib import Path

MAIN_PY = Path(__file__).resolve().parents[1] / "main.py"

EXTRACTED_METHODS = (
    "_mark_processing_started",
    "_owner_started_at",
    "_is_long_running_occupant",
    "_split_processing_by_long_running",
    "_is_flow_owner_bypassable",
)


def _extract_method_sources() -> list[str]:
    source = MAIN_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)

    plugin_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ChatPlus"
    )

    found = {}
    for node in plugin_class.body:
        if isinstance(node, ast.FunctionDef) and node.name in EXTRACTED_METHODS:
            found[node.name] = textwrap.dedent(ast.get_source_segment(source, node))

    missing = set(EXTRACTED_METHODS) - set(found)
    if missing:
        raise RuntimeError(f"main.py 中缺少预期方法: {sorted(missing)}")

    return [found[name] for name in EXTRACTED_METHODS]


_CLASS_HEADER = """
class BypassHost:
    def __init__(self, long_running_bypass_seconds=30):
        self.long_running_bypass_seconds = long_running_bypass_seconds
        self.processing_sessions = {}
        self._processing_started_at = {}
"""


def _build_host_class():
    body = "\n\n".join(_extract_method_sources())
    class_source = _CLASS_HEADER + "\n" + textwrap.indent(body, "    ")

    namespace = {"time": time}
    exec(compile(class_source, str(MAIN_PY), "exec"), namespace)
    return namespace["BypassHost"]


BypassHost = _build_host_class()
