"""Bucket 解码桶相位契约测试（spec A：桶契约类型化）。

相位互斥（由工厂方法保证）：feed 列表 / blocks 块 / 数组三相位；
to_array 幂等收敛到数组相位；n_frames / memory_estimate 覆盖三相位。
"""
import numpy as np
import pytest

from core.decoder import Bucket
from core.dbc_loader import MessageDef


def _md(frame_length=8):
    return MessageDef(name="T", sender_node="ECU", frame_length=frame_length)


def test_from_feed_initializes_feed_phase():
    b = Bucket.from_feed(100, 100, _md())
    assert b.arb == 100 and b.raw_id == 100
    assert b.feed_ts == [] and b.feed_data == []
    assert b.ts is None and b.blocks is None


def test_from_blocks_initializes_blocks_phase():
    """raw_id = 首帧原始 id（调用方传入，两工厂均捕获）。"""
    ts = np.array([1.0, 2.0], dtype=np.float64)
    lens = np.array([2, 2], dtype=np.int64)
    block = np.zeros((2, 2), dtype=np.uint8)
    b = Bucket.from_blocks(100, 0x64, _md(), block, ts, lens)
    assert b.raw_id == 0x64
    assert b.blocks == [block] and b.ts_blocks == [ts] and b.lens_blocks == [lens]
    assert b.ts is None and b.feed_ts is None


def test_to_array_feed_phase_converts_once():
    """feed 相位 → 数组：lens 从 data 字节长推导、data 列宽 = max lens 补零；幂等。"""
    b = Bucket.from_feed(100, 100, _md())
    for t, d in zip([0.0, 1.5, 2.0], [b"\x01", b"\x02\x03", b""]):
        b.add_frame(t, d)
    b.to_array()
    assert b.ts.dtype == np.float64 and b.lens.dtype == np.int64 \
        and b.data.dtype == np.uint8
    assert np.array_equal(b.ts, [0.0, 1.5, 2.0])
    assert np.array_equal(b.lens, [1, 2, 0])
    assert b.data.shape == (3, 2)          # 列宽 = max lens
    assert b.data.tolist() == [[1, 0], [2, 3], [0, 0]]
    assert b.feed_ts is None and b.feed_data is None
    b.to_array()                            # 幂等：结果不变
    assert np.array_equal(b.ts, [0.0, 1.5, 2.0])
    assert np.array_equal(b.lens, [1, 2, 0])


def test_to_array_blocks_phase_matches_feed_phase():
    """同帧序列：feed 逐帧 vs 单块整体入桶，to_array 后逐元素等价（双转换合一）。"""
    frames = [b"\x01", b"\x02\x03", b"\x04"]
    ts = [0.0, 1.5, 2.25]
    bf = Bucket.from_feed(100, 100, _md())
    for t, d in zip(ts, frames):
        bf.add_frame(t, d)
    bf.to_array()
    rows = np.array([list(f) + [0] * (2 - len(f)) for f in frames], dtype=np.uint8)
    bb = Bucket.from_blocks(100, 100, _md(), rows,
                            np.asarray(ts, dtype=np.float64),
                            np.asarray([len(f) for f in frames], dtype=np.int64))
    bb.to_array()
    assert np.array_equal(bf.ts, bb.ts)
    assert np.array_equal(bf.lens, bb.lens)
    assert np.array_equal(bf.data, bb.data)


def test_to_array_blocks_multi_block_pad():
    """多块拼接（H2a 一次末态连接语义）：窄块行按末态列宽补零，源相位置 None。"""
    b = Bucket.from_blocks(100, 100, _md(),
                           np.array([[1, 2]], dtype=np.uint8),
                           np.array([0.0], dtype=np.float64),
                           np.array([2], dtype=np.int64))
    b.add_block(np.array([[3, 4, 5], [6, 7, 8]], dtype=np.uint8),
                np.array([1.0, 2.0], dtype=np.float64),
                np.array([3, 3], dtype=np.int64))
    b.to_array()
    assert b.ts.tolist() == [0.0, 1.0, 2.0]
    assert b.lens.tolist() == [2, 3, 3]
    assert b.data.tolist() == [[1, 2, 0], [3, 4, 5], [6, 7, 8]]
    assert b.blocks is None and b.ts_blocks is None and b.lens_blocks is None


def test_to_array_array_phase_noop():
    """数组相位 no-op：不重建、不重赋值（对象同一性保持）。"""
    ts = np.array([0.0], dtype=np.float64)
    lens = np.array([1], dtype=np.int64)
    data = np.array([[1]], dtype=np.uint8)
    b = Bucket.from_blocks(100, 100, _md(), data, ts, lens)
    b.to_array()
    b.to_array()
    assert b.ts is ts and b.lens is lens and b.data is data


def test_n_frames_three_phases():
    feed = Bucket.from_feed(1, 1, _md())
    for t in (0.0, 1.0, 2.0):
        feed.add_frame(t, b"\x01")
    assert feed.n_frames == 3
    feed.to_array()
    assert feed.n_frames == 3
    blocks = Bucket.from_blocks(1, 1, _md(),
                                np.zeros((2, 2), dtype=np.uint8),
                                np.array([0.0, 1.0], dtype=np.float64),
                                np.array([2, 2], dtype=np.int64))
    blocks.add_block(np.zeros((1, 2), dtype=np.uint8),
                     np.array([2.0], dtype=np.float64),
                     np.array([2], dtype=np.int64))
    assert blocks.n_frames == 3


def test_memory_estimate_three_phases():
    feed = Bucket.from_feed(1, 1, _md())
    feed.add_frame(0.0, b"\x01\x02")
    feed.add_frame(1.0, b"\x03")
    assert feed.memory_estimate() == 2 * 32 + 3      # feed：len(ts)*32 + Σlen(data)
    feed.to_array()
    assert feed.memory_estimate() == int(feed.ts.nbytes) + int(feed.lens.nbytes) \
        + int(feed.data.nbytes)
    blocks = Bucket.from_blocks(1, 1, _md(),
                                np.zeros((2, 2), dtype=np.uint8),
                                np.array([0.0, 1.0], dtype=np.float64),
                                np.array([2, 2], dtype=np.int64))
    blocks.add_block(np.zeros((1, 2), dtype=np.uint8),
                     np.array([2.0], dtype=np.float64),
                     np.array([2], dtype=np.int64))
    exp = sum(t.nbytes for t in blocks.ts_blocks) \
        + sum(l.nbytes for l in blocks.lens_blocks) \
        + sum(blk.nbytes for blk in blocks.blocks)
    assert blocks.memory_estimate() == exp


def test_finish_asserts_key_matches_arb():
    """finish 入口断言：buckets 键与 bucket.arb 漂移 → AssertionError（防键/字段分叉）。"""
    from core.blf_reader import Frame
    from core.decoder import ChannelDecoder
    from core.dbc_loader import DbcDef

    md = MessageDef(name="T", sender_node="ECU", frame_length=1)
    dbc = DbcDef(path="", db=None, messages={100: md})
    dec = ChannelDecoder(dbc, 1)
    dec.feed(Frame(channel=1, ts_seconds=0.0, arbitration_id=100,
                   is_extended=False, is_fd=False, dlc=1, data=b"\x01"))
    dec.buckets[100].arb = 999              # 制造键/字段漂移
    with pytest.raises(AssertionError):
        dec.finish()
