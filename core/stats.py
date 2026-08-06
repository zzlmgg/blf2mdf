"""总线统计：CANoe 1s 统计组语义复刻（修复项 4 阶段 1）。

统计规则来自参考文件 _T058.mdf 实测校准（13 通道 × 601 点逐点命中，见
docs/2026-08-05-mdf-differences-fix-plan.md §8 执行记录）。阶段 1 实现
10 项帧计数类统计：StdData/ExtData/StdRemote/ExtRemote/ErrorFrames 及各自 Rate。

CANoe 语义（实测）：
- 帧时间戳 round 到 1ms（毫秒网格）；
- 窗界 R = [0, 1.1, 2.0, 3.0, ..., N]：首窗 1.1s，其后每秒整秒对齐；
  N = ceil(全局末帧时刻)；
- 累计 C[k] = 帧数 < (R[k] + 0.009)（严格小于，9ms 为输出时刻偏置）；
- Rate[1] 分子界 = 1.099（首窗 1.1 - 1ms），Rate[k≥2] 界 = R[k]（整数）；
  Rate[k] = 窗内帧数 / 窗长（首窗 1.1、窗 1 0.9、其后 1.0）；末点复制 Rate[N-1]；
- t 轴 = [0, 1.109, 2.009, ..., N-1+0.009, round(末帧, 3)]；
- 覆盖全部 16 个 CAN 通道（0-15），无数据通道全 0；
- 分类：扩展 ID → ExtData，远程帧 → StdRemote/ExtRemote（按扩展位），
  错误帧 → ErrorFrames，其余（含 CAN-FD）→ StdData。
"""
import numpy as np

# 阶段 1 统计项（参考组序：计数后接 Rate）
STAT_NAMES = (
    "StdData", "StdDataRate",
    "ExtData", "ExtDataRate",
    "StdRemote", "StdRemoteRate",
    "ExtRemote", "ExtRemoteRate",
    "ErrorFrames", "ErrorFrameRate",
)

# CANoe 统计覆盖的通道范围（0-15，与参考文件一致；无数据通道输出全 0）
STAT_CHANNELS = tuple(range(16))

# 累计类统计（int32 输出，与参考 dtype 一致）
_COUNT_NAMES = ("StdData", "ExtData", "StdRemote", "ExtRemote", "ErrorFrames")
# Rate 类统计（float64 输出）
_RATE_NAMES = tuple(n for n in STAT_NAMES if n not in _COUNT_NAMES)
# 分类索引
_I_STD, _I_EXT, _I_STDREM, _I_EXTREM, _I_ERR = range(5)


class ChannelStats:
    """单通道的 1s 统计结果。"""

    def __init__(self, channel: int, t: np.ndarray,
                 values: dict[str, np.ndarray]):
        self.channel = channel
        self.t = t                          # float64 (N+1,)
        self.values = values                # 统计名 → int32/float64 (N+1,)

    @property
    def signal_names(self) -> list[str]:
        return list(STAT_NAMES)


def aggregate_channel(timestamps: np.ndarray, is_extended: np.ndarray,
                      is_remote: np.ndarray, is_error: np.ndarray,
                      global_end_rounded: float) -> ChannelStats:
    """按 CANoe 1s 统计语义聚合单通道帧流。

    参数（长度一致的数组，可为空）：
    - timestamps：帧相对时间戳（秒，测量开始归零后）
    - is_extended / is_remote / is_error：分类掩码
    - global_end_rounded：全局测量结束时刻（round 到 1ms，全部通道最大值）
    """
    ts = np.round(np.asarray(timestamps, dtype=np.float64), 3)
    # 方案 B：searchsorted 要求输入有序。BLF 帧按时间序写入（round 后仍单调，
    # 子序列仍单调），正常路径无须排序；乱序输入在分类掩码应用后对掩码取值
    # 排序（掩码已固定，排序 tsm 不改变 sum(x < b) 结果，等价成立）。
    ts_monotonic = len(ts) <= 1 or bool(np.all(ts[:-1] <= ts[1:]))
    ext = np.asarray(is_extended, dtype=bool)
    remote = np.asarray(is_remote, dtype=bool)
    err = np.asarray(is_error, dtype=bool)

    n_windows = int(np.ceil(global_end_rounded))
    n_points = n_windows + 1

    # 窗界（秒）
    r = np.concatenate(([0.0, 1.1], np.arange(2.0, n_windows + 1.0, 1.0)))
    # C 界：[0] + [R[k] + 0.009]（float64 加法，与 CANoe 输出时刻一致）
    c_bound = np.concatenate(([0.0], r[1:] + 0.009))
    # Rate 界：[0, 1.099, 2.0, ..., N-1, C 界[N]]（首窗 -1ms；末元素 C 界仅占位，
    # Rate[N] = Rate[N-1] 复制不使用）
    rb = np.concatenate(([0.0, 1.1 - 0.001],
                         np.arange(2.0, n_windows, 1.0),
                         [c_bound[n_windows]]))
    # t 轴：[0] + C 界[1..N-1] + [末帧]
    t = np.concatenate(([0.0], c_bound[1:-1], [global_end_rounded]))

    # 分类计数（毫秒网格累计）
    classes = [np.zeros(len(ts), dtype=bool) for _ in range(5)]
    classes[_I_ERR] = err
    classes[_I_EXTREM] = ext & remote & ~err
    classes[_I_STDREM] = remote & ~ext & ~err
    classes[_I_EXT] = ext & ~remote & ~err
    classes[_I_STD] = ~ext & ~remote & ~err

    counts = {}
    rates = {}
    for i, (cn, rn) in enumerate(zip(_COUNT_NAMES, _RATE_NAMES)):
        mask = classes[i]
        # 方案 B（性能）：逐窗界全量扫描 O(N)/界 → np.searchsorted O(log N)/界。
        # sum(x < b) ≡ searchsorted(x, b, 'left')，严格小于语义一致，结果等价；
        # ts 单调时 ts[mask] 为有序子序列可直接用，乱序输入对掩码取值排序兜底。
        tsm = ts[mask]
        if not ts_monotonic:
            tsm = np.sort(tsm)
        # C[k] = 帧' < (R[k] + 0.009)
        cum = np.searchsorted(tsm, c_bound, side="left").astype(np.int64)
        # Rate：界序列 rb（累计），分母 = 窗长（R 差）
        cum_r = np.searchsorted(tsm, rb, side="left").astype(np.int64)
        rate = np.zeros(n_points)
        for k in range(1, n_windows):
            rate[k] = (cum_r[k] - cum_r[k - 1]) / (r[k] - r[k - 1])
        rate[n_windows] = rate[n_windows - 1]      # 末点复制
        counts[cn] = cum.astype(np.int32)
        rates[rn] = rate

    values = {}
    for cn in _COUNT_NAMES:
        values[cn] = counts[cn]
    for rn in _RATE_NAMES:
        values[rn] = rates[rn]

    return ChannelStats(channel=0, t=t.astype(np.float64), values=values)
