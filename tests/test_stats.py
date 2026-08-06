"""修复项 4：总线统计（CANoe 1s 组）——统计聚合规则测试。

规则来源：参考文件 _T058.mdf 实测校准（13 通道 × 601 点逐点命中），
语义见 core/stats.py 模块文档字符串。
"""
import numpy as np
import pytest

from core.stats import aggregate_channel, ChannelStats


def stats(*args, **kwargs) -> ChannelStats:
    return aggregate_channel(*args, **kwargs)


def test_empty_channel_full_zero_and_t_axis():
    """无帧通道：601 点全 0，t 轴 = [0, 1.109, 2.009, ..., 599.009, 599.999]。"""
    s = stats(np.array([], dtype=np.float64), np.array([], dtype=bool),
              np.array([], dtype=bool), np.array([], dtype=bool),
              global_end_rounded=599.999)
    assert len(s.t) == 601
    assert s.t[0] == 0.0 and s.t[1] == 1.109 and s.t[2] == 2.009
    assert s.t[-2] == 599.009 and s.t[-1] == 599.999
    for name in ("StdData", "StdDataRate", "ExtData", "ExtRemote", "ErrorFrames"):
        arr = s.values[name]
        assert arr.shape == (601,)
        assert np.all(arr == 0), name


def test_cumulative_counts_window_bounds():
    """C[k] = 帧'（round 到 1ms）< R[k] + 0.009 的累计（严格小于）。

    帧：0.5, 1.0, 1.5, 2.5, 3.5（global_end=3.5 → N=4 → 5 点）
    窗：首窗 [0, 1.1)，其后 [k, k+1)
    C = [0, 2, 3, 4, 5]（帧' < 1.109/2.009/3.009/4.009）
    """
    ts = np.array([0.5, 1.0, 1.5, 2.5, 3.5])
    s = stats(ts, np.zeros(5, dtype=bool), np.zeros(5, dtype=bool),
              np.zeros(5, dtype=bool), global_end_rounded=3.5)
    assert s.values["StdData"].dtype == np.int32
    assert s.values["StdData"].tolist() == [0, 2, 3, 4, 5]


def test_rate_first_window_special_and_window_lengths():
    """Rate[1] = 帧' < 1.099 / 1.1（首窗 1.1s）；Rate[2] 分母 0.9；末点复制 Rate[N-1]。"""
    ts = np.array([0.5, 1.0, 1.5, 2.5, 3.5])
    s = stats(ts, np.zeros(5, dtype=bool), np.zeros(5, dtype=bool),
              np.zeros(5, dtype=bool), global_end_rounded=3.5)
    r = s.values["StdDataRate"]
    assert r.dtype == np.float64
    # Rate[1] = 帧'<1.099(2 帧) / 1.1
    assert r[1] == pytest.approx(2 / 1.1)
    # Rate[2] = (帧'<2.0 - 帧'<1.099) / 0.9
    assert r[2] == pytest.approx((3 - 2) / 0.9)
    # Rate[3] = (帧'<3.009 - 帧'<2.0) / 1.0
    assert r[3] == pytest.approx((4 - 3) / 1.0)
    # 末点复制 Rate[N-1]
    assert r[4] == r[3]
    # 首点恒 0
    assert r[0] == 0.0


def test_frame_timestamps_rounded_to_1ms():
    """帧时间戳 round 到 1ms（CANoe 毫秒网格）：1.10899996 → 1.109，1.10900006 → 1.109。"""
    ts = np.array([1.10899996, 1.10900006])
    s = stats(ts, np.zeros(2, dtype=bool), np.zeros(2, dtype=bool),
              np.zeros(2, dtype=bool), global_end_rounded=1.109)
    # 两帧 round3 后都 = 1.109，恰等于 C 界 1.109（1.1+0.009）→ 严格小于排除
    assert s.values["StdData"][1] == 0
    # 但 Rate 界 1.099 之内 0 帧
    assert s.values["StdDataRate"][1] == 0.0


def test_classification_ext_remote_error():
    """分类：扩展帧→ExtData、标准远程→StdRemote、错误帧→ErrorFrames、其余→StdData。"""
    ts = np.array([0.5, 1.5, 2.5, 3.5, 4.5, 5.5])
    ext = np.array([False, True, False, False, False, False])
    remote = np.array([False, False, True, True, False, False])
    error = np.array([False, False, False, False, True, True])
    s = stats(ts, ext, remote, error, global_end_rounded=5.5)
    n = len(s.t)
    assert s.values["StdData"][n - 1] == 1       # 仅 0.5
    assert s.values["ExtData"][n - 1] == 1       # 1.5（扩展数据帧）
    assert s.values["StdRemote"][n - 1] == 2     # 2.5, 3.5（标准远程帧）
    assert s.values["ExtRemote"][n - 1] == 0
    assert s.values["ErrorFrames"][n - 1] == 2   # 4.5, 5.5


def test_global_end_controls_window_count():
    """N = ceil(global_end_rounded)；时长 599.999 → 601 点。"""
    s = stats(np.array([100.0]), np.zeros(1, dtype=bool), np.zeros(1, dtype=bool),
              np.zeros(1, dtype=bool), global_end_rounded=599.999)
    assert len(s.t) == 601
    assert s.values["StdData"][101] == 1  # 100.0 < 101.009


def _reference_aggregate(ts, ext, remote, err, end):
    """方案 B 参考实现：旧逐窗界全量扫描（O(W×N)）语义，用于回归对比。

    与 core/stats.py 改造前实现逐行一致（分类顺序：StdData/ExtData/
    StdRemote/ExtRemote/ErrorFrames → 索引 0-4）。
    """
    ts = np.round(np.asarray(ts, dtype=np.float64), 3)
    n_windows = int(np.ceil(end))
    n_points = n_windows + 1
    r = np.concatenate(([0.0, 1.1], np.arange(2.0, n_windows + 1.0, 1.0)))
    c_bound = np.concatenate(([0.0], r[1:] + 0.009))
    rb = np.concatenate(([0.0, 1.1 - 0.001],
                         np.arange(2.0, n_windows, 1.0),
                         [c_bound[n_windows]]))
    classes = [np.zeros(len(ts), dtype=bool) for _ in range(5)]
    classes[4] = err
    classes[3] = ext & remote & ~err
    classes[2] = remote & ~ext & ~err
    classes[1] = ext & ~remote & ~err
    classes[0] = ~ext & ~remote & ~err
    out = {}
    for i, (cn, rn) in enumerate(zip(
            ("StdData", "ExtData", "StdRemote", "ExtRemote", "ErrorFrames"),
            ("StdDataRate", "ExtDataRate", "StdRemoteRate", "ExtRemoteRate",
             "ErrorFrameRate"))):
        mask = classes[i]
        cum = np.array([np.sum(ts[mask] < b) for b in c_bound], dtype=np.int64)
        cum_r = np.array([np.sum(ts[mask] < b) for b in rb], dtype=np.int64)
        rate = np.zeros(n_points)
        for k in range(1, n_windows):
            rate[k] = (cum_r[k] - cum_r[k - 1]) / (r[k] - r[k - 1])
        rate[n_windows] = rate[n_windows - 1]
        out[cn] = cum.astype(np.int32)
        out[rn] = rate
    return out


def test_searchsorted_matches_reference_random():
    """方案 B 回归：searchsorted 实现与旧逐界全量扫描参考实现逐点一致。

    随机数据覆盖：空/单帧/小样本/大样本、重复时间戳、全部分类组合
    （各分类类内 count/rate 均须与参考逐位相同）。
    """
    rng = np.random.default_rng(20260805)
    for n in (0, 1, 2, 7, 100, 5000):
        for _ in range(5):
            ts = np.round(rng.uniform(0.0, 100.0, n), 3)
            ext = rng.integers(0, 2, n).astype(bool)
            remote = rng.integers(0, 2, n).astype(bool)
            err = rng.integers(0, 2, n).astype(bool)
            end = round(float(ts.max()) if n else 50.0, 3)
            s = aggregate_channel(ts, ext, remote, err, end)
            ref = _reference_aggregate(ts, ext, remote, err, end)
            for name in s.values:
                assert s.values[name].dtype == ref[name].dtype, (n, name)
                assert np.array_equal(s.values[name], ref[name]), (n, name)


def test_unsorted_timestamps_equivalent():
    """乱序时间戳：排序兜底后结果与有序输入完全一致（sum(x < b) 与顺序无关）。"""
    ts = np.array([5.0, 1.0, 3.0, 2.0, 4.0, 1.0])
    s_unsorted = stats(ts.copy(), np.zeros(6, dtype=bool), np.zeros(6, dtype=bool),
                       np.zeros(6, dtype=bool), global_end_rounded=5.0)
    s_sorted = stats(np.sort(ts), np.zeros(6, dtype=bool), np.zeros(6, dtype=bool),
                     np.zeros(6, dtype=bool), global_end_rounded=5.0)
    for name in s_unsorted.values:
        assert np.array_equal(s_unsorted.values[name], s_sorted.values[name]), name


def test_unsorted_mixed_classes_mask_alignment():
    """乱序 + 混合分类：排序兜底必须不破坏掩码-帧对应（回归：曾把 ts 排序
    而不重排掩码，导致错误帧与扩展帧分类错位）。"""
    ts = np.array([92.362, 9.73])           # 乱序（92.362 > 9.73）
    ext = np.array([True, True])
    remote = np.array([True, True])
    err = np.array([True, False])           # 92.362 帧为错误帧，9.73 帧为扩展远程
    s = stats(ts, ext, remote, err, global_end_rounded=92.362)
    # 9.73 帧（ExtRemote）计入窗口 10.009 内；92.362 帧（ErrorFrames）计入 93.009 末窗
    assert s.values["ExtRemote"][10] == 1
    assert s.values["ErrorFrames"][10] == 0
    assert s.values["ErrorFrames"][93] == 1
    assert s.values["ExtRemote"][93] == 1


def test_duplicate_timestamps_strict_less():
    """重复时间戳 + 帧时刻恰等于 C 界：严格 < 排除（searchsorted 'left' 语义）。"""
    ts = np.array([1.109, 1.109, 2.009, 3.009, 3.009])
    s = stats(ts, np.zeros(5, dtype=bool), np.zeros(5, dtype=bool),
              np.zeros(5, dtype=bool), global_end_rounded=3.009)
    # C 界 = [0, 1.109, 2.009, 3.009, 4.009]；恰等于界的帧不计：
    # C[2] 计入 < 2.009 的两个 1.109 帧，2.009 自身排除
    assert s.values["StdData"].tolist() == [0, 0, 2, 3, 5]
