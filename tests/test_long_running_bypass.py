"""长任务放行（long_running_bypass_seconds）逻辑回归测试。

这些测试只覆盖并发判定用的纯逻辑方法，不加载整个插件，
因此不需要 AstrBot 运行时环境。
"""

import time

import pytest

from long_running_bypass_helpers import BypassHost


def test_disabled_by_default_threshold_zero():
    host = BypassHost(long_running_bypass_seconds=0)
    old_start = time.time() - 600

    assert not host._is_long_running_occupant(old_start)


def test_recent_occupant_is_not_bypassed():
    host = BypassHost(long_running_bypass_seconds=30)

    assert not host._is_long_running_occupant(time.time() - 5)


def test_occupant_past_threshold_is_bypassed():
    host = BypassHost(long_running_bypass_seconds=30)

    assert host._is_long_running_occupant(time.time() - 31)


@pytest.mark.parametrize("bad_value", [None, "", 0])
def test_missing_started_at_is_never_bypassed(bad_value):
    host = BypassHost(long_running_bypass_seconds=30)

    assert not host._is_long_running_occupant(bad_value)


def test_split_separates_long_running_from_active():
    host = BypassHost(long_running_bypass_seconds=30)
    now = time.time()

    host.processing_sessions = {
        "slow": "group_a",
        "fresh": "group_a",
        "other_group": "group_b",
        "self": "group_a",
    }
    host._processing_started_at = {
        "slow": now - 90,
        "fresh": now - 3,
        "other_group": now - 90,
        "self": now,
    }

    blocking, bypassed = host._split_processing_by_long_running("group_a", "self")

    assert blocking == ["fresh"]
    assert bypassed == ["slow"]


def test_split_ignores_other_chats_and_self():
    host = BypassHost(long_running_bypass_seconds=30)
    now = time.time()

    host.processing_sessions = {"self": "group_a", "elsewhere": "group_b"}
    host._processing_started_at = {"self": now - 999, "elsewhere": now - 999}

    blocking, bypassed = host._split_processing_by_long_running("group_a", "self")

    assert blocking == []
    assert bypassed == []


def test_entry_without_timestamp_still_blocks():
    """缺少起始时间的条目按保守策略处理：继续阻塞，不放行。"""
    host = BypassHost(long_running_bypass_seconds=30)

    host.processing_sessions = {"legacy": "group_a"}
    host._processing_started_at = {}

    blocking, bypassed = host._split_processing_by_long_running("group_a", "self")

    assert blocking == ["legacy"]
    assert bypassed == []


def test_flow_owner_bypass_uses_started_at():
    host = BypassHost(long_running_bypass_seconds=30)
    now = time.time()

    assert host._is_flow_owner_bypassable(
        {"owner": "normal", "processing_id": "x", "started_at": now - 45}
    )
    assert not host._is_flow_owner_bypassable(
        {"owner": "normal", "processing_id": "x", "started_at": now - 5}
    )
    assert not host._is_flow_owner_bypassable(None)
    assert not host._is_flow_owner_bypassable("proactive")


def test_mark_processing_records_timestamp_and_keeps_entry():
    host = BypassHost(long_running_bypass_seconds=30)

    host._mark_processing_started("msg1", "group_a")

    assert host.processing_sessions["msg1"] == "group_a"
    assert host._processing_started_at["msg1"] == pytest.approx(time.time(), abs=5)


def test_mark_processing_cleans_stale_timestamps():
    host = BypassHost(long_running_bypass_seconds=30)
    host._processing_started_at = {"gone": 1.0, "also_gone": 2.0}

    host._mark_processing_started("msg1", "group_a")

    assert set(host._processing_started_at) == {"msg1"}


def test_bypassed_long_task_entry_survives_for_its_own_hooks():
    """放行不得移除长任务条目，否则它自己的回复/保存钩子会被跳过。"""
    host = BypassHost(long_running_bypass_seconds=30)
    now = time.time()

    host.processing_sessions = {"slow": "group_a"}
    host._processing_started_at = {"slow": now - 120}

    _, bypassed = host._split_processing_by_long_running("group_a", "newcomer")
    host._mark_processing_started("newcomer", "group_a")

    assert bypassed == ["slow"]
    # 长任务仍在处理中标记内：on_llm_response / after_message_sent 的准入检查仍会通过
    assert "slow" in host.processing_sessions
    assert "slow" in host._processing_started_at
    assert host.processing_sessions["newcomer"] == "group_a"


def test_owner_started_at_inherits_original_processing_time():
    """normal owner 必须沿用消息的原始起始时间，而非注册瞬间。"""
    host = BypassHost(long_running_bypass_seconds=30)
    original = time.time() - 120
    host.processing_sessions = {"slow": "group_a"}
    host._processing_started_at = {"slow": original}

    assert host._owner_started_at("slow") == pytest.approx(original)


def test_owner_started_at_falls_back_to_now_when_missing():
    host = BypassHost(long_running_bypass_seconds=30)

    for bad in ("missing_id",):
        assert host._owner_started_at(bad) == pytest.approx(time.time(), abs=5)

    host._processing_started_at = {"bad": "not-a-number"}
    assert host._owner_started_at("bad") == pytest.approx(time.time(), abs=5)


def test_long_task_owner_is_bypassable_right_after_send():
    """
    回归：耗时工具调用结束、刚进入保存阶段时注册的 normal owner，
    必须立刻可被放行，否则等待超时的消息仍会被降级成「不回复、只缓存」。
    """
    host = BypassHost(long_running_bypass_seconds=30)
    host.processing_sessions = {"slow": "group_a"}
    host._processing_started_at = {"slow": time.time() - 60}

    owner = {
        "owner": "normal",
        "processing_id": "slow",
        "started_at": host._owner_started_at("slow"),
    }

    assert host._is_flow_owner_bypassable(owner)


def test_fast_message_owner_is_not_bypassable():
    """普通快回复进入保存阶段时不得被放行，避免破坏原有串行语义。"""
    host = BypassHost(long_running_bypass_seconds=30)
    host.processing_sessions = {"fast": "group_a"}
    host._processing_started_at = {"fast": time.time() - 3}

    owner = {
        "owner": "normal",
        "processing_id": "fast",
        "started_at": host._owner_started_at("fast"),
    }

    assert not host._is_flow_owner_bypassable(owner)
