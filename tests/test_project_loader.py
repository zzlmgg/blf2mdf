"""project_loader：项目枚举 / 新名—通道映射解析 / 项目加载 / 自动绑定建议。"""
import pytest

from core import project_loader
from core.dbc_loader import DbcDef
from conftest import PROJECT_ROOT

# 与 inputs/dbc_ccu3.0/dbc_对应关系.txt 等价的文本（含无通道行与空行）
MAPPING_TEXT = """VDCCCU_CANFD1——CFCAN1——CAN13
VDCCCU_CANFD2——CFCAN2——CAN3
VDCCCU_CANFD3——CFCAN3——CAN6
VDCCIDC_CANFD——IFCAN——CAN12
VDCPublic_CANFD1——PFCAN1——CAN1
VDCPublic_CANFD2——PFCAN2——CAN15
VDCCZF_CANFD——ZFCANF——CAN8
VDCCZL_CANFD——ZFCANL——CAN9
VDCCZR_CANFD——ZFCANR——CAN10
VDCCZT_CANFD——ZFCANT——CAN11

VDCTBOX_CANFD——
VDCCZL_CANFD2——
VDCCZR_CANFD2——
"""

EXPECTED_MAPPING = {
    "CFCAN1": 13, "CFCAN2": 3, "CFCAN3": 6, "IFCAN": 12,
    "PFCAN1": 1, "PFCAN2": 15, "ZFCANF": 8, "ZFCANL": 9,
    "ZFCANR": 10, "ZFCANT": 11,
}


# ---- 映射解析 ----

def test_parse_mapping_text_skips_unbound_lines():
    assert project_loader._parse_mapping_text(MAPPING_TEXT) == EXPECTED_MAPPING


def test_parse_mapping_text_garbage_lines():
    text = "乱码行\n# 注释\nA——B\n"
    assert project_loader._parse_mapping_text(text) == {}


def test_load_mapping_missing_file_falls_back():
    assert project_loader.load_mapping("不存在的路径.txt") \
        == project_loader.DEFAULT_MAPPING
    assert project_loader.load_mapping() == project_loader.DEFAULT_MAPPING
    assert project_loader.DEFAULT_MAPPING == EXPECTED_MAPPING


def test_load_mapping_utf8_file(tmp_path):
    # 内容与内置回退表不同：若解析失败静默回退，断言必然失败（能区分两条路径）
    text = MAPPING_TEXT.replace("CFCAN1——CAN13", "CFCAN1——CAN7")
    p = tmp_path / "map.txt"
    p.write_text(text, encoding="utf-8")
    assert project_loader.load_mapping(p)["CFCAN1"] == 7
    assert project_loader.load_mapping(p) == {**EXPECTED_MAPPING, "CFCAN1": 7}


def test_load_mapping_gbk_file(tmp_path):
    # 真实映射文件为 GBK（文件名/内容含中文），编码探测走 GBK 路径；
    # 同样用与内置表不同的通道号验证走的是文件解析而非回退
    text = MAPPING_TEXT.replace("CFCAN2——CAN3", "CFCAN2——CAN5")
    p = tmp_path / "dbc_对应关系.txt"
    p.write_bytes(text.encode("gbk"))
    assert project_loader.load_mapping(p)["CFCAN2"] == 5


def test_load_mapping_real_file_parses(tmp_path):
    """真实映射文件必须走解析路径（回退日志出现即失败）。"""
    import logging

    root = PROJECT_ROOT / "inputs" / "dbc_ccu3.0"
    if not (root / "dbc_对应关系.txt").exists():
        pytest.skip("缺映射文件")
    records = []
    class _H(logging.Handler):
        def emit(self, r):
            records.append(r.getMessage())
    logger = logging.getLogger("core.project_loader")
    h = _H()
    logger.addHandler(h)
    try:
        m = project_loader.load_mapping(root / "dbc_对应关系.txt")
    finally:
        logger.removeHandler(h)
    assert m == EXPECTED_MAPPING
    assert not [r for r in records if "回退" in r]


# ---- 项目枚举 ----

def test_list_projects_only_dbc_dirs(tmp_path):
    (tmp_path / "AH8").mkdir()
    (tmp_path / "AH8" / "CFCAN1.dbc").touch()
    (tmp_path / "A02").mkdir()
    (tmp_path / "A02" / "IFCAN.dbc").touch()
    (tmp_path / "empty").mkdir()          # 无 DBC 的目录不算项目
    (tmp_path / "说明.txt").touch()
    assert project_loader.list_projects(tmp_path) == ["A02", "AH8"]


def test_list_projects_missing_root_returns_empty(tmp_path):
    """发布布局缺 inputs/dbc_ccu3.0 时 GUI 启动不应崩溃（空项目列表 + 手动 DBC）。"""
    assert project_loader.list_projects(tmp_path / "no_such_dir") == []


def test_list_projects_none_returns_empty():
    """未定位到数据源（CCU3_ROOT=None）时同样返回空列表。"""
    assert project_loader.list_projects(None) == []


# ---- ccu3 数据源定位（project_loader.find_ccu3_root）----

def test_find_ccu3_root_prefers_inputs_layout(tmp_path):
    """源码/发布包布局 <root>/inputs/dbc_ccu3.0 优先于直接旁挂。"""
    (tmp_path / "inputs" / "dbc_ccu3.0").mkdir(parents=True)
    (tmp_path / "dbc_ccu3.0").mkdir()
    assert project_loader.find_ccu3_root(tmp_path) \
        == tmp_path / "inputs" / "dbc_ccu3.0"


def test_find_ccu3_root_direct_layout(tmp_path):
    """exe 直接旁挂布局 <root>/dbc_ccu3.0 也能识别（用户需求）。"""
    (tmp_path / "dbc_ccu3.0").mkdir()
    assert project_loader.find_ccu3_root(tmp_path) == tmp_path / "dbc_ccu3.0"


def test_find_ccu3_root_missing(tmp_path):
    """两种布局都不存在 → None（GUI 降级手动 DBC，不崩溃）。"""
    assert project_loader.find_ccu3_root(tmp_path) is None


def test_list_projects_real_root():
    root = PROJECT_ROOT / "inputs" / "dbc_ccu3.0"
    projects = project_loader.list_projects(root)
    assert projects and "AH8" in projects and "A02" in projects
    # 每个项目文件夹只含规范命名的 DBC（当前 10 个，AH8 缺 PFCAN2）
    for name in projects:
        stems = {p.stem for p in (root / name).glob("*.dbc")}
        assert stems <= set(EXPECTED_MAPPING)


# ---- 项目加载 ----

def test_load_project_empty_folder(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="未找到 DBC"):
        project_loader.load_project(tmp_path, "empty")


def test_load_project_real_ccu3_folder():
    """真实项目文件夹（A02）全部 DBC 可解析——新命名 DBC 走 cantools 的集成验证。"""
    root = PROJECT_ROOT / "inputs" / "dbc_ccu3.0"
    folder = root / "A02"
    if not folder.exists():
        pytest.skip("缺 inputs/dbc_ccu3.0 数据")
    dbcs = project_loader.load_project(root, "A02")
    assert [d.path.split("\\")[-1] for d in dbcs] == \
        [f"{k}.dbc" for k in EXPECTED_MAPPING]
    assert all(d.messages for d in dbcs)


# ---- 自动绑定建议 ----

def _dbc(path: str) -> DbcDef:
    return DbcDef(path=path, db=None)


def test_auto_bindings_standard():
    dbcs = [_dbc(fr"x\{k}.dbc") for k in EXPECTED_MAPPING]
    got = project_loader.auto_bindings(dbcs, EXPECTED_MAPPING)
    # {通道: DBC 路径}（结构化身份），通道来自映射
    assert got == {v: fr"x\{k}.dbc" for k, v in EXPECTED_MAPPING.items()}


def test_auto_bindings_missing_dbc_file():
    """AH8 场景：项目缺 PFCAN2.dbc → CAN15 不出现（保持不绑定）。"""
    dbcs = [_dbc(fr"x\{k}.dbc") for k in EXPECTED_MAPPING if k != "PFCAN2"]
    got = project_loader.auto_bindings(dbcs, EXPECTED_MAPPING)
    assert 15 not in got
    assert got[13] == r"x\CFCAN1.dbc"


def test_auto_bindings_unknown_dbc_ignored():
    """映射表之外的 DBC 读入列表但不参与自动绑定。"""
    dbcs = [_dbc(r"x\CFCAN1.dbc"), _dbc(r"x\NEWBUS.dbc")]
    got = project_loader.auto_bindings(dbcs, EXPECTED_MAPPING)
    assert got == {13: r"x\CFCAN1.dbc"}
