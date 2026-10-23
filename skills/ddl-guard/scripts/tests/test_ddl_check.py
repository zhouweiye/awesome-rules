#!/usr/bin/env python3
"""ddl_check.py 单元测试

将 badcase 行为固化为断言式测试，并覆盖 DDL 解析与命名/注释/索引检查逻辑。
运行: python3 -m pytest tests/ -v
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ddl_check
from ddl_check import Severity

BADCASE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "badcase")
)


def _badcase(name):
    return os.path.join(BADCASE_DIR, name, "input", "example.sql")


def _mandatory(issues):
    return [i for i in issues if i.severity == Severity.MANDATORY]


# ── badcase 集成断言（行为固化）────────────────────────────────────────────

def test_badcase_001_forbidden_type_and_comment():
    """禁用类型 + 缺失注释：至少检出问题。"""
    issues = ddl_check.check_file(_badcase("001-forbidden-type-and-missing-comment"))
    assert len(issues) >= 1
    assert {i.rule for i in issues}  # 规则名非空


def test_badcase_004_bad_index():
    issues = ddl_check.check_file(_badcase("004-bad-index"))
    assert len(_mandatory(issues)) >= 1


def test_badcase_005_bad_naming():
    issues = ddl_check.check_file(_badcase("005-bad-naming"))
    assert len(_mandatory(issues)) >= 2


def test_badcase_006_bad_comment():
    issues = ddl_check.check_file(_badcase("006-bad-comment"))
    assert len(_mandatory(issues)) >= 10


def test_composite_index_column_limit():
    """联合索引字段数 > 5 → 推荐；≤ 5 → 不报。"""
    def ddl_for(n):
        fields = "\n".join(f"  c{i} VARCHAR(16) COMMENT '列{i}'," for i in range(n))
        cols = ", ".join(f"c{i}" for i in range(n))
        return (f"CREATE TABLE t_ix (\n  id BIGINT COMMENT '主键',\n"
                f"{fields}\n  KEY ix_cols ({cols})\n) COMMENT='测试';\n")

    assert all(i.rule != "联合索引字段数" for i in _issues_for(ddl_for(5)))
    hit = [i for i in _issues_for(ddl_for(6)) if i.rule == "联合索引字段数"]
    assert len(hit) == 1 and "6" in hit[0].description


# ── DDL 解析 ────────────────────────────────────────────────────────────────

def test_strip_sql_comments_removes_inline():
    """strip_sql_comments 处理 -- 行注释（块注释不在其职责内）。"""
    out = ddl_check.strip_sql_comments("id BIGINT -- inline comment\n, name VARCHAR")
    assert "--" not in out
    assert "id" in out and "name" in out


def test_extract_tables_finds_create_table():
    tables = ddl_check.extract_tables(
        "CREATE TABLE t_order (\n  id BIGINT\n);\n")
    assert any(t.name == "t_order" for t in tables)


def test_extract_tables_handles_backtick():
    tables = ddl_check.extract_tables(
        "CREATE TABLE `t_user` (\n  id BIGINT\n);\n")
    assert any(t.name == "t_user" for t in tables)


# ── check_file 入口 ─────────────────────────────────────────────────────────

def test_check_file_returns_list_for_minimal_ddl():
    """最小 DDL：返回 list（结构正确，不抛异常）。"""
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id BIGINT COMMENT '主键',\n"
        "  name VARCHAR(64) COMMENT '名称'\n"
        ") COMMENT='演示表';\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as f:
        f.write(ddl)
        path = f.name
    try:
        issues = ddl_check.check_file(path)
        assert isinstance(issues, list)
        # 表名/注释合规时应无"表注释缺失"
        assert all(i.rule != "表注释缺失" for i in issues)
    finally:
        os.unlink(path)


def test_check_file_detects_reserved_table_name():
    """表名命中保留字应报强制问题。"""
    ddl = (
        "CREATE TABLE `order` (\n"
        "  id BIGINT COMMENT '主键'\n"
        ") COMMENT='订单表';\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as f:
        f.write(ddl)
        path = f.name
    try:
        issues = ddl_check.check_file(path)
        assert any(i.rule == "保留字" and i.severity == Severity.MANDATORY
                   for i in issues)
    finally:
        os.unlink(path)


# ── 辅助：写临时 DDL 并检查 ───────────────────────────────────────────────

def _issues_for(ddl_text, config=None):
    """写临时 DDL 文件并返回 check_file 结果（config 为项目配置，None 用默认）。"""
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as f:
        f.write(ddl_text)
        path = f.name
    try:
        return ddl_check.check_file(path, config=config)
    finally:
        os.unlink(path)


def _ddl_with_field(field_line, table="t_demo", with_del_flag=True):
    """生成含指定字段行、其余必含字段齐全的最小 DDL。

    必含字段使用规范名 + 规范注释（如 last_update_time → COMMENT '最后更新时间'）。
    必含字段名豁免缩写检查（ddl_check.py: REQUIRED_FIELDS 硬编码豁免）。
    with_del_flag=False 供"业务表缺逻辑删除字段"场景复用。
    """
    return (
        f"CREATE TABLE {table} (\n"
        "  id bigint COMMENT '主键id',\n"
        f"  {field_line},\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间'"
        + (",\n  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]'\n" if with_del_flag else "\n")
        + ") COMMENT='demo';\n"
    )


# ── 行尾 -- 分隔线误报修复 ─────────────────────────────────────────────────

def test_comment_style_separator_not_flagged():
    """`-- ----` / `-- ===` 分隔线不得误报为注释格式违规。"""
    ddl = (
        "-- ============================================================\n"
        "-- ------------------------------------------------------------\n"
        "CREATE TABLE t_ok (\n"
        "  id bigint COMMENT '主键',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间'\n"
        ") COMMENT='ok';\n"
    )
    issues = _issues_for(ddl)
    assert all(i.rule != "注释格式" for i in issues)


def test_comment_style_no_space_still_flagged():
    """`--xxx`（无空格真违规）仍应报。"""
    ddl = (
        "--这行注释后没有空格\n"
        "CREATE TABLE t_bad (\n"
        "  id bigint COMMENT '主键'\n"
        ") COMMENT='bad';\n"
    )
    issues = _issues_for(ddl)
    assert any(i.rule == "注释格式" for i in issues)


def test_comment_style_inline_after_code_flagged():
    """代码后行内 `--xxx`（无空格）→ 报，即使该行含 COMMENT。"""
    ddl = (
        "CREATE TABLE t_bad (\n"
        "  id bigint COMMENT '主键'\n"
        ") COMMENT='bad'--注释后没有空格\n"
    )
    issues = _issues_for(ddl)
    assert any(i.rule == "注释格式" for i in issues)


def test_comment_style_dash_in_string_not_flagged():
    """COMMENT 字符串内的 `--` → 不报。"""
    ddl = (
        "CREATE TABLE t_ok (\n"
        "  id bigint COMMENT 'a--b',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间'\n"
        ") COMMENT='ok';\n"
    )
    issues = _issues_for(ddl)
    assert all(i.rule != "注释格式" for i in issues)


# ── COL032 注释补充信息格式 ────────────────────────────────────────────────

def test_field_comment_paren_comma_flagged():
    """补充信息圆括号闭合后用逗号追加 → 报。"""
    issues = _issues_for(_ddl_with_field("parent_id bigint COMMENT '父参数id(0=根),支持嵌套'"))
    assert any(i.rule == "注释补充信息格式" for i in issues)


def test_field_comment_paren_ok():
    """补充信息全在圆括号内 → 不报。"""
    issues = _issues_for(_ddl_with_field("parent_id bigint COMMENT '父参数id(0=根,支持嵌套)'"))
    assert all(i.rule != "注释补充信息格式" for i in issues)


# ── COL018 泛化字段名 ──────────────────────────────────────────────────────

def test_generic_field_name_flagged():
    """泛化单一名词（remark）→ 报推荐。"""
    issues = _issues_for(_ddl_with_field("remark varchar(200) COMMENT '备注'"))
    assert any(i.rule == "泛化字段名" and i.severity == Severity.RECOMMENDED for i in issues)


def test_generic_field_name_prefixed_ok():
    """加主体前缀（merchant_remark）→ 不报。"""
    issues = _issues_for(_ddl_with_field("merchant_remark varchar(200) COMMENT '备注'"))
    assert all(i.rule != "泛化字段名" for i in issues)


# ── NAM002 缩写字典 ────────────────────────────────────────────────────────

def test_abbreviation_field_flagged():
    """字段名含未规范化写法（direction）→ 报。"""
    issues = _issues_for(_ddl_with_field("direction tinyint COMMENT '方向'"))
    assert any(i.rule == "缩写未规范化" for i in issues)


def test_abbreviation_table_flagged():
    """表名含未规范化写法（message）→ 报。"""
    issues = _issues_for(_ddl_with_field("ext varchar(50) COMMENT '扩展'", table="t_message_log"))
    assert any(i.rule == "缩写未规范化" and "表名" in i.location for i in issues)


def test_abbreviation_std_ok():
    """已用标准缩写（dir）→ 不报。"""
    issues = _issues_for(_ddl_with_field("dir tinyint COMMENT '方向'"))
    assert all(i.rule != "缩写未规范化" for i in issues)


def test_abbreviation_extended_dict_mapping():
    """扩展字典条目（mapping → mapp）字段级检测。"""
    issues = _issues_for(_ddl_with_field("mapping_id varchar(32) COMMENT '映射唯一键'"))
    assert any(
        i.rule == "缩写未规范化" and "mapping" in i.description and "mapp" in i.suggestion
        for i in issues
    )


def test_abbreviation_extended_dict_authentication():
    """扩展字典条目（authentication → aut）字段级检测。"""
    issues = _issues_for(_ddl_with_field("authentication varchar(32) COMMENT '鉴权形态'"))
    assert any(
        i.rule == "缩写未规范化" and "authentication" in i.description and "aut" in i.suggestion
        for i in issues
    )


def test_abbreviation_index_flagged():
    """索引主体分词含未规范化写法（ix_mapping_id）→ 报索引缩写未规范化。"""
    ddl = (
        "CREATE TABLE t_demo_idx (\n"
        "  id bigint COMMENT '主键',\n"
        "  mapping_id varchar(32) COMMENT '映射唯一键',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  KEY ix_mapping_id (mapping_id)\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert any(
        i.rule == "索引缩写未规范化" and "mapping" in i.description and "mapp" in i.suggestion
        for i in issues
    )


def test_abbreviation_index_std_ok():
    """索引主体分词已用标准缩写（ix_mapp_id）→ 不报。"""
    ddl = (
        "CREATE TABLE t_demo_idx2 (\n"
        "  id bigint COMMENT '主键',\n"
        "  mapp_id varchar(32) COMMENT '映射唯一键',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  KEY ix_mapp_id (mapp_id)\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert all(i.rule != "索引缩写未规范化" for i in issues)


# ── 索引名包含字段名 ──────────────────────────────────────────────────

def test_index_contains_columns_ok():
    """索引名按 ix_<field1>_<field2>... 完整拼接 → 不报。"""
    ddl = (
        "CREATE TABLE t_idx_full (\n"
        "  id bigint COMMENT '主键',\n"
        "  mch_id varchar(32) COMMENT '商户id',\n"
        "  msg_type varchar(64) COMMENT '消息类型',\n"
        "  status varchar(16) COMMENT '状态',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  KEY ix_mch_id_msg_type_status (mch_id, msg_type, status)\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert all(i.rule != "索引名未包含全部字段" for i in issues)


def test_index_contains_columns_missing_part():
    """索引名省略字段分词（ix_mch_msg_type_status 缺 `id`）→ 报强制。"""
    ddl = (
        "CREATE TABLE t_idx_short (\n"
        "  id bigint COMMENT '主键',\n"
        "  mch_id varchar(32) COMMENT '商户id',\n"
        "  msg_type varchar(64) COMMENT '消息类型',\n"
        "  status varchar(16) COMMENT '状态',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  KEY ix_mch_msg_type_status (mch_id, msg_type, status)\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert any(
        i.rule == "索引名未包含全部字段" and i.severity == Severity.MANDATORY
        and "id" in i.description
        for i in issues
    )


def test_index_contains_columns_uk_missing_part():
    """唯一索引同样要求：uk_cred_id (cred_id) 合规；uk_cred (cred_id) 报。"""
    ddl_bad = (
        "CREATE TABLE t_idx_uk (\n"
        "  id bigint COMMENT '主键',\n"
        "  cred_id varchar(32) COMMENT '凭证id',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  UNIQUE KEY uk_cred (cred_id)\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl_bad)
    assert any(i.rule == "索引名未包含全部字段" for i in issues)

    ddl_ok = ddl_bad.replace("UNIQUE KEY uk_cred (cred_id)", "UNIQUE KEY uk_cred_id (cred_id)")
    issues_ok = _issues_for(ddl_ok)
    assert all(i.rule != "索引名未包含全部字段" for i in issues_ok)


# ── 中文语义缩写（订阅 subscribe → subscr）───────────────────────────────

def test_abbreviation_subscription_table_flagged():
    """表名含 subscription（订阅）→ 提示改用 subscr（按中文语义推断缩写）。"""
    issues = _issues_for(
        _ddl_with_field("ext varchar(50) COMMENT '扩展'", table="t_subscription_log")
    )
    assert any(
        i.rule == "缩写未规范化" and "subscription" in i.description
        and "subscr" in i.suggestion
        for i in issues
    )


def test_abbreviation_subscribe_field_flagged():
    """字段名含 subscribe（订阅）→ 提示改用 subscr。"""
    issues = _issues_for(_ddl_with_field("subscribe_at datetime COMMENT '订阅时间'"))
    assert any(
        i.rule == "缩写未规范化" and "subscribe" in i.description
        and "subscr" in i.suggestion
        for i in issues
    )


def test_abbreviation_subscr_ok():
    """已用 subscr → 不报。"""
    issues = _issues_for(_ddl_with_field("subscr_at datetime COMMENT '订阅时间'"))
    assert all(i.rule != "缩写未规范化" for i in issues)


# ── 强制级别 / 新字典特征（公司数据治理要求）────────────────────────────

def test_abbreviation_severity_is_mandatory():
    """缩写未规范化 → Severity.MANDATORY（公司数据治理强制要求）。"""
    issues = _issues_for(_ddl_with_field("mapping_id varchar(32) COMMENT '映射唯一键'"))
    assert any(
        i.rule == "缩写未规范化" and i.severity == Severity.MANDATORY
        for i in issues
    )


def test_abbreviation_index_severity_is_mandatory():
    """索引缩写未规范化 → Severity.MANDATORY。"""
    ddl = (
        "CREATE TABLE t_idx_abbrev (\n"
        "  id bigint COMMENT '主键',\n"
        "  mapping_id varchar(32) COMMENT '映射唯一键',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  KEY ix_mapping_id (mapping_id)\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert any(
        i.rule == "索引缩写未规范化" and i.severity == Severity.MANDATORY
        for i in issues
    )


def test_abbreviation_paste_table_cargo():
    """粘贴表条目 cargo → cgo：字段名含 cargo → 强制改用 cgo。"""
    issues = _issues_for(_ddl_with_field("cargo_type varchar(32) COMMENT '货物类型'"))
    assert any(
        i.rule == "缩写未规范化" and "cargo" in i.description and "cgo" in i.suggestion
        for i in issues
    )


def test_abbreviation_paste_table_vehicle():
    """粘贴表条目 vehicle → veh：字段名含 vehicle → 强制改用 veh。"""
    issues = _issues_for(_ddl_with_field("vehicle_id varchar(32) COMMENT '车辆id'"))
    assert any(
        i.rule == "缩写未规范化" and "vehicle" in i.description and "veh" in i.suggestion
        for i in issues
    )


def test_abbreviation_paste_table_company():
    """粘贴表条目 company → co：字段名含 company → 强制改用 co。"""
    issues = _issues_for(_ddl_with_field("company_id varchar(32) COMMENT '公司id'"))
    assert any(
        i.rule == "缩写未规范化" and "company" in i.description and "co" in i.suggestion
        for i in issues
    )


def test_abbreviation_paste_table_insurance():
    """粘贴表条目 insurance → insu：字段名含 insurance → 强制改用 insu。"""
    issues = _issues_for(_ddl_with_field("insurance_amount decimal(18,2) COMMENT '保险金额'"))
    assert any(
        i.rule == "缩写未规范化" and "insurance" in i.description and "insu" in i.suggestion
        for i in issues
    )


def test_abbreviation_full_table_name_flagged():
    """粘贴表全量入库：表名含 mapping → 强制改用 mapp。"""
    issues = _issues_for(
        _ddl_with_field("ext varchar(50) COMMENT '扩展'", table="t_mapping_log")
    )
    assert any(
        i.rule == "缩写未规范化" and "表名" in i.location
        and "mapping" in i.description and "mapp" in i.suggestion
        for i in issues
    )


def test_abbreviation_index_body_flagged():
    """索引名缩写：ix_cargo_type 含 cargo → 强制改用 cgo。"""
    ddl = (
        "CREATE TABLE t_idx_cargo (\n"
        "  id bigint COMMENT '主键',\n"
        "  cargo_type varchar(32) COMMENT '货物类型',\n"
        "  cgo_id varchar(32) COMMENT '货物id',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  KEY ix_cargo_type (cargo_type)\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    # 字段 cargo_type + 索引 ix_cargo_type 双触发
    field_hits = [i for i in issues if i.rule == "缩写未规范化" and "cargo" in i.description]
    index_hits = [i for i in issues if i.rule == "索引缩写未规范化" and "cargo" in i.description]
    assert field_hits and index_hits


def test_abbreviation_same_word_skip_ok():
    """同字映射不入字典（用户最新指令）：phone 字段不报错（粘贴表中 phone→phone 已跳过）。"""
    issues = _issues_for(_ddl_with_field("phone varchar(20) COMMENT '电话'"))
    # 应不报 phone → 任何缩写 的违规
    assert not any(
        i.rule == "缩写未规范化" and "phone" in i.description
        for i in issues
    )


def test_abbreviation_multi_word_phrase_not_split():
    """多词短语不拆词副作用：字段名含 company 不应被映射到 insuco（来自 insurance company）。"""
    issues = _issues_for(_ddl_with_field("company_id varchar(32) COMMENT '公司id'"))
    # 应映射到 co 而不是 insuco
    company_hit = next(
        (i for i in issues if i.rule == "缩写未规范化" and "company" in i.description),
        None,
    )
    assert company_hit is not None
    assert "co" in company_hit.suggestion
    assert "insuco" not in company_hit.suggestion


def test_abbreviation_manual_fix_subscription():
    """手工补全：subscription → subscr（粘贴表分号并列短语拆词副作用补全）。"""
    issues = _issues_for(
        _ddl_with_field("ext varchar(50) COMMENT '扩展'", table="t_subscription_log")
    )
    assert any(
        i.rule == "缩写未规范化" and "subscription" in i.description
        and "subscr" in i.suggestion
        for i in issues
    )


# ── 必含字段一致性（公司基线硬性要求）───────────────────────────────

def test_required_field_comment_mismatch_flagged():
    """必含字段注释不一致 → 强制违规。"""
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id bigint COMMENT '主键id',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]'\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert any(
        i.rule == "必含字段注释不一致" and i.severity == Severity.MANDATORY
        and "creator_id" in i.description and "创建人id" in i.suggestion
        for i in issues
    )


def test_required_field_type_mismatch_flagged():
    """必含字段类型不一致（如 creator_id 用了 bigint 而非 varchar(36)）→ 强制违规。"""
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id bigint COMMENT '主键id',\n"
        "  creator_id bigint NOT NULL DEFAULT 0 COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]'\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert any(
        i.rule == "必含字段定义不一致" and i.severity == Severity.MANDATORY
        and "creator_id" in i.description
        for i in issues
    )


def test_required_field_correct_ok():
    """必含字段全部合规（名称+类型+注释完全一致）→ 不报。"""
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id bigint COMMENT '主键id',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]'\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert all(
        i.rule not in ("必含字段缺失", "必含字段定义不一致", "必含字段注释不一致")
        for i in issues
    )


def test_required_field_comment_whitespace_insensitive():
    """必含字段注释比较空白不敏感：内部多余空格容错。"""
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id bigint COMMENT '主键id',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人 id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]'\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    # 内部多一个空格应被容错
    assert all(i.rule != "必含字段注释不一致" for i in issues)


def test_required_field_exempt_from_abbreviation():
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id bigint COMMENT '主键id',\n"
        "  ext varchar(50) COMMENT '扩展',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]'\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    update_hits = [
        i for i in issues
        if i.rule == "缩写未规范化" and "last_update_time" in i.description
    ]
    assert not update_hits


def test_required_field_index_exempt_from_abbreviation():
    """必含字段名豁免索引缩写检查：ix_last_update_time 含 update 分词不报错。"""
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id bigint COMMENT '主键id',\n"
        "  ext varchar(50) COMMENT '扩展',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]',\n"
        "  KEY ix_last_update_time (last_update_time)\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    # last_update_time 含 update 分词，会被字典命中；豁免逻辑生效
    update_index_hits = [
        i for i in issues
        if i.rule == "索引缩写未规范化" and "update" in i.description
    ]
    assert not update_index_hits


# ── 多义并列英文短语（consignor / cargo_owner → cgoer）────────────────

def test_abbreviation_multi_eng_consignor():
    """多义并列英文：consignor / cargo_owner 共享缩写 cgoer，consignor 字段触发。"""
    issues = _issues_for(_ddl_with_field("consignor_id varchar(32) COMMENT '货主id'"))
    assert any(
        i.rule == "缩写未规范化" and "consignor" in i.description
        and "cgoer" in i.suggestion
        for i in issues
    )


def test_abbreviation_multi_eng_cargo_owner():
    """多义并列英文：consignor / cargo_owner 共享缩写 cgoer，cargo_owner 整体字段触发。"""
    issues = _issues_for(_ddl_with_field("cargo_owner varchar(32) COMMENT '货主'"))
    assert any(
        i.rule == "缩写未规范化" and "cargo_owner" in i.description
        and "cgoer" in i.suggestion
        for i in issues
    )


def test_abbreviation_multi_eng_subject_main_body():
    """多义并列英文：subject/main body 共享缩写 subj/main_body。"""
    issues = _issues_for(_ddl_with_field("subject_id varchar(32) COMMENT '主体id'"))
    assert any(
        i.rule == "缩写未规范化" and "subject" in i.description
        and ("subj" in i.suggestion or "main_body" in i.suggestion)
        for i in issues
    )


def test_abbreviation_carrier():
    """carrier 同字映射：字段名 carrier 不报错（value 也是 carrier，项目标准业务名）。"""
    issues = _issues_for(_ddl_with_field("carrier_id varchar(32) COMMENT '承运人id'"))
    # carrier 字段已合规（value == key），不触发
    carrier_hits = [
        i for i in issues
        if i.rule == "缩写未规范化" and "carrier" in i.description
    ]
    assert not carrier_hits


# ── 规范字段名 del_flag 豁免（拆词副作用防护）──────────────────────

def test_del_flag_exempt_from_abbreviation():
    """del_flag 是规范字段名，豁免缩写检查（即使 flag 在字典中同字映射）。"""
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id bigint COMMENT '主键id',\n"
        "  ext varchar(50) COMMENT '扩展',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]'\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    # del_flag 字段豁免，不应触发 flag 缩写违规
    flag_hits = [
        i for i in issues
        if i.rule == "缩写未规范化" and "del_flag" in i.description
    ]
    assert not flag_hits


# ── 手工补充：subscription → subscr（粘贴表分号并列短语拆词副作用补全）──

def test_abbreviation_subscription_manual_fix():
    """subscription → subscr（手工补充，Excel 中 subscribe 独立行但 subscription 缺失）。"""
    issues = _issues_for(_ddl_with_field("subscription_id varchar(32) COMMENT '订阅id'"))
    assert any(
        i.rule == "缩写未规范化" and "subscription" in i.description
        and "subscr" in i.suggestion
        for i in issues
    )


# ── R2: 取值范围 [k-v,...] 格式（COL033）─────────────────────────────

def test_comment_bracket_kv_format_ok():
    """注释取值范围 [10-待支付,20-已支付] → 合规。"""
    issues = _issues_for(_ddl_with_field("status tinyint COMMENT '订单状态[10-待支付,20-已支付,30-已完成]'"))
    assert all(i.rule != "注释取值范围格式" for i in issues)


def test_comment_bracket_kv_format_eq_ok():
    """注释取值范围 [10=待支付,20=已支付] → 合规（= 也是合法分隔符）。"""
    issues = _issues_for(_ddl_with_field("status tinyint COMMENT '订单状态[10=待支付,20=已支付]'"))
    assert all(i.rule != "注释取值范围格式" for i in issues)


def test_comment_bracket_kv_format_bad():
    """注释取值范围 [待支付/已支付] → 违规（应用 k-v 格式）。"""
    issues = _issues_for(_ddl_with_field("status tinyint COMMENT '订单状态[待支付/已支付]'"))
    assert any(
        i.rule == "注释取值范围格式" and i.severity == Severity.MANDATORY
        for i in issues
    )


def test_comment_bracket_single_plain_value_ok():
    """单值枚举 [HTTP] / [状态] → 合规（[值,值,...] 的单值形态，语义交由 R1/AI 判断）。"""
    for comment in ("协议[HTTP]", "订单状态[状态]"):
        issues = _issues_for(_ddl_with_field("ext varchar(16) COMMENT '%s'" % comment))
        assert all(i.rule != "注释取值范围格式" for i in issues), comment


def test_comment_bracket_desc_with_space_still_flagged():
    """[] 内含空格的说明性文字（非纯值非 k-v）→ 仍违规。"""
    issues = _issues_for(_ddl_with_field("ext varchar(64) COMMENT '键[订阅键 companyId 最长134]'"))
    assert any(i.rule == "注释取值范围格式" for i in issues)


def test_comment_bracket_kv_format_space_between():
    """注释取值范围 '10-待支付 20-已支付'（空格代替逗号）→ 违规。"""
    issues = _issues_for(_ddl_with_field("status tinyint COMMENT '订单状态[10-待支付 20-已支付]'"))
    assert any(
        i.rule == "注释取值范围格式"
        for i in issues
    )


# ── R2 纯值枚举形态（steering 规范允许 [值,值,...]）────────────────────

def test_comment_bracket_plain_values_ok():
    """纯值枚举 [enabled,disabled] → 合规（规范允许 [值,值,...] 形态）。"""
    issues = _issues_for(_ddl_with_field("status varchar(16) COMMENT '状态[enabled,disabled]'"))
    assert all(i.rule != "注释取值范围格式" for i in issues)


def test_comment_bracket_plain_values_mixed_case_ok():
    """纯值枚举 [pending,success,failed,managed] / [G,E] → 合规。"""
    for comment in ("校验状态[pending,success,failed,managed]", "切流模式[G,E]"):
        issues = _issues_for(_ddl_with_field("mode varchar(16) COMMENT '%s'" % comment))
        assert all(i.rule != "注释取值范围格式" for i in issues), comment


def test_comment_bracket_desc_text_still_flagged():
    """[] 内混入说明性文字（冒号/箭头/大括号）→ 仍违规。"""
    for comment in (
        "提取规则[行式:源路径→固定信封字段,必配]",
        "字段表JSON[{a,b},spec]",
        "对象键[订阅键companyId×msgType最长134]",
        "阶段[envelope/enrich/meta等]",
    ):
        issues = _issues_for(_ddl_with_field("ext varchar(64) COMMENT '%s'" % comment))
        assert any(i.rule == "注释取值范围格式" for i in issues), comment



def test_comment_bracket_kv_format_paren_in_value():
    """注释取值范围含括号 '10-待支付(注)' → 违规。"""
    issues = _issues_for(_ddl_with_field("status tinyint COMMENT '订单状态[10-待支付(注),20-已支付]'"))
    assert any(
        i.rule == "注释取值范围格式"
        for i in issues
    )


# ── 高优 #1：缩写 value 是保留字的违规循环 ──────────────────────

def test_abbreviation_reserved_value_exempt_character():
    """character → char（char 是 MySQL 保留字）：豁免，避免违规循环。"""
    issues = _issues_for(_ddl_with_field("character_id varchar(32) COMMENT '字符id'"))
    # character 字段不报违规（因为改 char 又会触发保留字违规）
    assert not any(
        i.rule == "缩写未规范化" and "character" in i.description
        for i in issues
    )


def test_abbreviation_reserved_value_exempt_delete():
    """delete → del（del 是 Python 关键字）：豁免。"""
    issues = _issues_for(_ddl_with_field("delete_flag tinyint COMMENT '删除标志'"))
    assert not any(
        i.rule == "缩写未规范化" and "delete" in i.description
        for i in issues
    )


def test_abbreviation_reserved_value_exempt_number():
    """number → no（no 是 MySQL 保留字）：豁免。"""
    issues = _issues_for(_ddl_with_field("number_id varchar(32) COMMENT '编号id'"))
    assert not any(
        i.rule == "缩写未规范化" and "number" in i.description
        for i in issues
    )


def test_abbreviation_reserved_value_index_exempt():
    """索引中含保留字 value 的 key 也豁免：ix_number_id 不报。"""
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id bigint COMMENT '主键id',\n"
        "  number_id varchar(32) COMMENT '编号id',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]',\n"
        "  KEY ix_number_id (number_id)\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert not any(
        i.rule == "索引缩写未规范化" and "number" in i.description
        for i in issues
    )


# ── 中优 #5：_check_index_abbreviation 豁免扩展 ─────────────────────

def test_index_abbreviation_with_del_flag_exempt():
    """索引 ix_del_flag 不报缩写违规（del_flag 是规范字段名豁免列表）。"""
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id bigint COMMENT '主键id',\n"
        "  ext varchar(50) COMMENT '扩展',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]',\n"
        "  KEY ix_del_flag (del_flag)\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert all(i.rule != "索引缩写未规范化" for i in issues)


def test_abbreviation_chain_resolved_to_terminal():
    """链式缩写折叠：website→web→onln 报终点 onln，整改后不再二次违规。"""
    assert ddl_check.iter_abbrev_violations("website_url") == [("website", "onln")]
    assert ddl_check.iter_abbrev_violations("onln_url") == []  # 按建议整改后干净
    assert ddl_check.iter_abbrev_violations("undo_cnt") == [("undo", "canc")]
    assert ddl_check.iter_abbrev_violations("recovery_time") == [("recovery", "recov")]


def test_index_abbreviation_issue_table_name():
    """索引缩写违规的 Issue.table 填表名（非索引名），location 含表与索引。"""
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id bigint COMMENT '主键id',\n"
        "  website_url varchar(200) NOT NULL COMMENT '网址',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]',\n"
        "  KEY ix_website_url (website_url)\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    hits = [i for i in issues if i.rule == "索引缩写未规范化"]
    assert hits, "website_url 索引缩写违规应命中"
    assert all(i.table == "t_demo" for i in hits)
    assert all("表:t_demo" in i.location and "索引:ix_website_url" in i.location for i in hits)


# ── R3: 补充信息 () 格式（COL034）──────────────────────────────────

def test_comment_paren_redundant_flagged():
    """补充信息与主标题完全相同 → 报"补充信息冗余"。"""
    issues = _issues_for(_ddl_with_field("order_no varchar(36) COMMENT '订单编号(订单编号)'"))
    assert any(
        i.rule == "补充信息冗余" and i.severity == Severity.MANDATORY
        for i in issues
    )


def test_comment_paren_empty_flagged():
    """补充信息圆括号为空 → 报"补充信息为空"。"""
    issues = _issues_for(_ddl_with_field("parent_id bigint COMMENT '父参数id()'"))
    assert any(
        i.rule == "补充信息为空" and i.severity == Severity.MANDATORY
        for i in issues
    )


def test_comment_paren_meaningful_ok():
    """补充信息含必要的额外说明 → 不报。"""
    issues = _issues_for(_ddl_with_field("parent_id bigint COMMENT '父参数id(0=根,支持嵌套)'"))
    assert all(
        i.rule not in ("补充信息冗余", "补充信息为空")
        for i in issues
    )





# ── 日志/流水表必含字段豁免 ─────────────────────────────────────────────────

# ── 业务表必含逻辑删除字段（2026-09-24 del_flag 口径 A 接线）──────────────

def test_business_table_missing_del_flag():
    """业务表五必含字段齐全但无 del_flag → 报【强制】逻辑删除字段缺失。"""
    ddl = _ddl_with_field("order_no varchar(36) COMMENT '订单编号'", with_del_flag=False)
    issues = _issues_for(ddl)
    assert any(i.rule == "逻辑删除字段缺失" and i.severity == ddl_check.Severity.MANDATORY for i in issues)


def test_business_table_with_del_flag_passes():
    """业务表含 del_flag → 不报逻辑删除字段缺失。"""
    ddl = _ddl_with_field("del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]'")
    issues = _issues_for(ddl)
    assert all(i.rule != "逻辑删除字段缺失" for i in issues)


def test_business_table_with_equivalent_alias_passes():
    """业务表含等价别名 is_deleted → 不报（存在性满足，命名统一由 del_flag 命名规则另行约束）。"""
    ddl = _ddl_with_field("is_deleted tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]'")
    issues = _issues_for(ddl)
    assert all(i.rule != "逻辑删除字段缺失" for i in issues)


def test_logistics_substring_table_not_exempt():
    """业务表名含 _log 子串（t_order_logistics）→ 不豁免，缺 del_flag 仍报。"""
    ddl = (
        "CREATE TABLE t_order_logistics (\n"
        "  id bigint COMMENT '主键',\n"
        "  order_no varchar(36) COMMENT '订单编号',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间'\n"
        ") COMMENT='物流订单表';\n"
    )
    issues = _issues_for(ddl)
    assert any(i.rule == "逻辑删除字段缺失" for i in issues)


def test_log_table_missing_del_flag_exempt():
    """日志表无 del_flag → 豁免，不报逻辑删除字段缺失。"""
    ddl = (
        "CREATE TABLE call_log (\n"
        "  id bigint COMMENT '主键',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  content varchar(100) COMMENT '内容'\n"
        ") COMMENT='日志表';\n"
    )
    issues = _issues_for(ddl)
    assert all(i.rule != "逻辑删除字段缺失" for i in issues)


def test_log_table_exempt_updater_fields():
    """日志表缺 last_updater_id/last_update_time → 不报必含字段。"""
    ddl = (
        "CREATE TABLE call_log (\n"
        "  id bigint COMMENT '主键id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  content varchar(100) COMMENT '内容'\n"
        ") COMMENT='日志表';\n"
    )
    issues = _issues_for(ddl)
    assert all(i.rule != "必含字段缺失" for i in issues)


def test_log_table_still_requires_create_time():
    """日志表缺 create_time → 仍报。"""
    ddl = (
        "CREATE TABLE call_flow (\n"
        "  id bigint COMMENT '主键',\n"
        "  content varchar(100) COMMENT '内容'\n"
        ") COMMENT='流水表';\n"
    )
    issues = _issues_for(ddl)
    assert any(i.rule == "必含字段缺失" and "create_time" in i.description for i in issues)


def test_non_log_table_requires_all():
    """普通表缺更新人字段 → 仍报。"""
    ddl = (
        "CREATE TABLE t_order (\n"
        "  id bigint COMMENT '主键',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'\n"
        ") COMMENT='订单表';\n"
    )
    issues = _issues_for(ddl)
    assert any(i.rule == "必含字段缺失" and "last_updater_id" in i.description for i in issues)


# ── 唯一索引启发式 ─────────────────────────────────────────────────────────

def test_unique_hint_when_comment_says_unique():
    """注释含「唯一」+ 普通索引 → 建议唯一索引。"""
    ddl = (
        "CREATE TABLE t_ability (\n"
        "  id bigint COMMENT '主键',\n"
        "  ability_code varchar(36) COMMENT '能力编码(唯一)',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  PRIMARY KEY (id),\n"
        "  KEY ix_ability_code (ability_code)\n"
        ") COMMENT='能力表';\n"
    )
    issues = _issues_for(ddl)
    assert any(i.rule == "建议唯一索引" and i.severity == Severity.RECOMMENDED for i in issues)


def test_unique_hint_silent_when_unique_index_exists():
    """已建 UNIQUE 索引 → 不报。"""
    ddl = (
        "CREATE TABLE t_ability (\n"
        "  id bigint COMMENT '主键',\n"
        "  ability_code varchar(36) COMMENT '能力编码(唯一)',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  PRIMARY KEY (id),\n"
        "  UNIQUE KEY uk_ability_code (ability_code)\n"
        ") COMMENT='能力表';\n"
    )
    issues = _issues_for(ddl)
    assert all(i.rule != "建议唯一索引" for i in issues)


# ── 解析器与工具函数 ──────────────────────────────────────────────────────

def test_strip_sql_comments_preserves_quoted():
    """引号内的 -- 保留，引号外的行尾注释去除。"""
    out = ddl_check.strip_sql_comments("a 'b--c' d -- real")
    assert "b--c" in out          # 引号内 -- 保留
    assert "real" not in out      # 引号外注释去除
    assert "a" in out and "d" in out


def test_extract_table_comment_no_paren():
    """无闭括号时返回 None。"""
    assert ddl_check.extract_table_comment("no closing paren here") is None


def test_parse_unique_constraint():
    """UNIQUE CONSTRAINT 能解析为唯一索引。"""
    ddl = (
        "CREATE TABLE t_uc (\n"
        "  id bigint COMMENT '主键',\n"
        "  code varchar(36) COMMENT '码',\n"
        "  UNIQUE CONSTRAINT uc_code (code)\n"
        ") COMMENT='uc';\n"
    )
    tbl = ddl_check.extract_tables(ddl)[0]
    assert any(i.is_unique for i in tbl.indexes)


def test_parse_skips_constraint_and_fk_lines():
    """CONSTRAINT / FOREIGN KEY 行不解析为字段或索引。"""
    ddl = (
        "CREATE TABLE t_fk (\n"
        "  id bigint COMMENT '主键',\n"
        "  oid bigint COMMENT '外键',\n"
        "  CONSTRAINT ck1 CHECK (oid > 0),\n"
        "  FOREIGN KEY (oid) REFERENCES o(id)\n"
        ") COMMENT='fk';\n"
    )
    tbl = ddl_check.extract_tables(ddl)[0]
    assert len(tbl.fields) == 2
    assert all("CONSTRAINT" not in i.raw_definition.upper() for i in tbl.indexes)


def test_parse_field_line_unparseable():
    """无法解析的行返回 None。"""
    assert ddl_check._parse_field_line(") COMMENT='x'", 1) is None
    assert ddl_check._parse_field_line("", 1) is None


def test_parse_index_line_returns_none():
    """无法识别的索引行返回 None。"""
    assert ddl_check._parse_index_line("garbage line", 1) is None


# ── 禁用子句 ─────────────────────────────────────────────────────────────

def test_forbidden_clauses_detected():
    """CHARACTER SET / COLLATE / AUTO_INCREMENT / ENGINE / ROW_FORMAT 全检出。"""
    ddl = (
        "CREATE TABLE t_bad (\n"
        "  id bigint COMMENT '主键'\n"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci "
        "AUTO_INCREMENT=100 ROW_FORMAT=DYNAMIC COMMENT='bad';\n"
    )
    issues = _issues_for(ddl)
    rules = {i.rule for i in issues}
    assert {
        "去除字符集子句", "去除字符序子句", "去除 auto_increment 子句",
        "去除 engine 子句", "去除 row_format 子句",
    } <= rules


def test_partition_detected():
    issues = _issues_for(
        "CREATE TABLE t_p (id bigint COMMENT '主键') COMMENT='p' PARTITION BY HASH(id);\n")
    assert any(i.rule == "禁止分区表" for i in issues)


def test_change_column_detected():
    issues = _issues_for("ALTER TABLE t CHANGE COLUMN a b int;\n")
    assert any(i.rule == "禁止 CHANGE COLUMN" for i in issues)


# ── 禁用语句全文级（触发器/存储过程/自定义函数）─────────────────────────────

def test_create_trigger_flagged_mandatory():
    """含 CREATE TRIGGER → 报【强制】禁用触发器，位置带文件:行号（负控制）。"""
    issues = _issues_for(
        "-- 清理触发器\n"
        "CREATE TRIGGER trg_order_clean BEFORE DELETE ON t_order\n"
        "FOR EACH ROW\n"
        "BEGIN\n"
        "  SET @x = 1;\n"
        "END;\n"
    )
    hit = [i for i in _mandatory(issues) if i.rule == "禁用触发器"]
    assert len(hit) == 1
    assert hit[0].location.endswith(":2")   # 命中行号可定位


def test_create_procedure_flagged_mandatory():
    """含 CREATE PROCEDURE → 报【强制】禁用存储过程。"""
    issues = _issues_for("CREATE PROCEDURE p_sync()\nBEGIN\nEND;\n")
    assert any(i.rule == "禁用存储过程" and i.severity == Severity.MANDATORY
               for i in issues)


def test_create_function_flagged_recommended():
    """含 CREATE FUNCTION → 报【推荐】自定义函数（规范为推荐级）。"""
    issues = _issues_for("CREATE FUNCTION f_add(a int) RETURNS int\nRETURN a + 1;\n")
    assert any(i.rule == "自定义函数" and i.severity == Severity.RECOMMENDED
               for i in issues)


def test_forbidden_statement_case_insensitive_cross_line():
    """小写 + 跨行写法（create\\n trigger）同样检出。"""
    issues = _issues_for(
        "create\n  trigger trg_x BEFORE INSERT ON t_a\nFOR EACH ROW SET @y = 1;\n")
    assert any(i.rule == "禁用触发器" for i in issues)


def test_forbidden_statement_in_comment_not_flagged():
    """注释中的 CREATE TRIGGER/PROCEDURE/FUNCTION → 不报（注释不参与检查）。"""
    issues = _issues_for(
        "-- 历史遗留：CREATE TRIGGER trg_x ...\n"
        "# CREATE PROCEDURE p_x\n"
        "/* CREATE FUNCTION f_x */\n"
        "CREATE TABLE t_ok (\n"
        "  id bigint COMMENT '主键'\n"
        ") COMMENT='ok';\n"
    )
    assert all(i.rule not in ("禁用触发器", "禁用存储过程", "自定义函数")
               for i in issues)


def test_clean_table_no_forbidden_statement():
    """合规建表不含禁用语句 → 无禁用语句检出（正控制）。"""
    issues = _issues_for(_ddl_with_field("order_no varchar(36) COMMENT '订单编号'"))
    assert all(i.rule not in ("禁用触发器", "禁用存储过程", "自定义函数")
               for i in issues)


def test_forbidden_statement_in_string_literal_not_flagged():
    """引号字面量内的解释性短语不误报（PR #210 Sourcery 意见）：
    COMMENT '... CREATE TRIGGER ...' 是描述文本而非建触发器语句。"""
    issues = _issues_for(
        "CREATE TABLE t_note (\n"
        "  id bigint COMMENT '主键',\n"
        "  remark varchar(200) COMMENT 'migration note: CREATE TRIGGER is forbidden'\n"
        ") COMMENT='含禁用语句示例文案的合规表';\n"
    )
    assert all(i.rule not in ("禁用触发器", "禁用存储过程", "自定义函数")
               for i in issues)


def test_definer_clause_does_not_bypass():
    """CREATE DEFINER=... TRIGGER/PROCEDURE 不得绕过检查（PR #210 Sourcery 意见）。"""
    issues = _issues_for(
        "CREATE DEFINER=CURRENT_USER TRIGGER trg_a BEFORE INSERT ON t_a\n"
        "FOR EACH ROW SET @x = 1;\n")
    assert any(i.rule == "禁用触发器" for i in issues)
    issues = _issues_for(
        "CREATE DEFINER='u'@'%' PROCEDURE p_clean() BEGIN DELETE FROM t_b; END;\n")
    assert any(i.rule == "禁用存储过程" for i in issues)


# ── 表名 / 表注释违规分支 ────────────────────────────────────────────────

TABLE_NAME_CASES = [
    ("t" + "a" * 30, "表名长度"),
    ("_bad", "表名开头"),
    ("t__u", "连续下划线"),
    ("Tbad", "表名字符"),
]


@pytest.mark.parametrize("name,rule", TABLE_NAME_CASES)
def test_table_name_violation(name, rule):
    ddl = f"CREATE TABLE {name} (\n  id bigint COMMENT '主键'\n) COMMENT='x';\n"
    issues = _issues_for(ddl)
    assert any(i.rule == rule for i in issues)


def test_table_comment_too_long():
    ddl = f"CREATE TABLE t_c (\n  id bigint COMMENT '主键'\n) COMMENT='{'表' * 65}';\n"
    issues = _issues_for(ddl)
    assert any(i.rule == "表注释长度" for i in issues)


def test_table_comment_special_char():
    ddl = "CREATE TABLE t_s (\n  id bigint COMMENT '主键'\n) COMMENT='表★注释';\n"
    issues = _issues_for(ddl)
    assert any(i.rule == "表注释特殊字符" for i in issues)


# ── 字段违规分支 ─────────────────────────────────────────────────────────

def test_field_count_too_many():
    cols = "\n".join(f"  c{i:02d} varchar(10) COMMENT '字段{i}'," for i in range(42))
    ddl = f"CREATE TABLE t_many (\n{cols}\n  id bigint COMMENT '主键'\n) COMMENT='多字段';\n"
    issues = _issues_for(ddl)
    assert any(i.rule == "字段数量" for i in issues)


FIELD_NAME_CASES = [
    ("x" * 31, "字段名长度"),
    ("_bad", "字段名开头"),
    ("a__b", "连续下划线"),
    ("Name", "字段名字符"),
]


@pytest.mark.parametrize("name,rule", FIELD_NAME_CASES)
def test_field_name_violation(name, rule):
    ddl = (
        "CREATE TABLE t_f (\n"
        f"  {name} varchar(50) COMMENT 'x',\n"
        "  id bigint COMMENT '主键'\n"
        ") COMMENT='f';\n"
    )
    issues = _issues_for(ddl)
    assert any(i.rule == rule for i in issues)


def test_field_comment_too_long():
    ddl = f"CREATE TABLE t_l (\n  id bigint COMMENT '{'注' * 129}'\n) COMMENT='l';\n"
    issues = _issues_for(ddl)
    assert any(i.rule == "字段注释长度" for i in issues)


def test_varchar_too_long():
    issues = _issues_for(_ddl_with_field("big varchar(600) COMMENT '长'"))
    assert any(i.rule == "varchar长度" for i in issues)


def test_char_too_long():
    issues = _issues_for(_ddl_with_field("c char(30) COMMENT '定长'"))
    assert any(i.rule == "char长度" for i in issues)


def test_field_type_unparseable_base():
    """字段类型无法识别时不抛异常（走 fallback）。"""
    issues = _issues_for(_ddl_with_field("weird xyz COMMENT '怪'"))
    assert isinstance(issues, list)


def test_parse_table_body_blank_line():
    """表体内空行被跳过，不影响字段解析。"""
    ddl = (
        "CREATE TABLE t_b (\n"
        "  id bigint COMMENT '主键',\n"
        "\n"
        "  ext varchar(50) COMMENT '扩展'\n"
        ") COMMENT='b';\n"
    )
    tbl = ddl_check.extract_tables(ddl)[0]
    assert len(tbl.fields) == 2


def test_field_name_reserved_word():
    """字段名为 MySQL 保留字（desc）→ 报保留字。"""
    ddl = (
        "CREATE TABLE t_r (\n"
        "  `desc` varchar(50) COMMENT '降序',\n"
        "  id bigint COMMENT '主键'\n"
        ") COMMENT='r';\n"
    )
    issues = _issues_for(ddl)
    assert any(i.rule == "保留字" for i in issues)


def test_field_type_empty_string_no_crash():
    """字段类型为空（base_type_match 为 None）时静默返回。"""
    f = ddl_check.FieldInfo(name="x", raw_definition="x", line=1, type="")
    issues = []
    ddl_check.check_field_type(f, "t", issues)
    assert not issues


# ── 主键 / 外键 / 无建表 ─────────────────────────────────────────────────

def test_foreign_key_detected():
    ddl = (
        "CREATE TABLE t_fk (\n"
        "  id bigint COMMENT '主键',\n"
        "  oid bigint COMMENT '外键',\n"
        "  FOREIGN KEY (oid) REFERENCES o(id)\n"
        ") COMMENT='fk';\n"
    )
    issues = _issues_for(ddl)
    assert any(i.rule == "外键约束" for i in issues)


def test_primary_key_not_int():
    ddl = (
        "CREATE TABLE t_pk (\n"
        "  id varchar(36) COMMENT '主键',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  PRIMARY KEY (id)\n"
        ") COMMENT='pk';\n"
    )
    issues = _issues_for(ddl)
    assert any(i.rule == "主键类型" for i in issues)


def test_no_create_table():
    issues = _issues_for("-- only a comment, no table\n")
    assert any(i.rule == "无建表语句" for i in issues)


# ── 报告格式化 ───────────────────────────────────────────────────────────

def test_format_report_text_with_and_without_issues():
    issues = [ddl_check.Issue(table="t", severity=Severity.MANDATORY, rule="r",
                              location="L", description="d", suggestion="s")]
    text = ddl_check.format_report_text("f.sql", issues)
    assert "DDL 审查报告" in text and "r" in text and "s" in text
    assert "检查通过" in ddl_check.format_report_text("f.sql", [])


def test_format_report_json():
    issues = [ddl_check.Issue(table="t", severity=Severity.MANDATORY, rule="r",
                              location="L", description="d")]
    data = json.loads(ddl_check.format_report_json("f.sql", issues))
    assert data["summary"]["total"] == 1
    assert data["issues"][0]["rule"] == "r"


# ── main 入口（文件/目录/空目录/json）────────────────────────────────────

def _write_tmp_sql(content):
    f = tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False)
    f.write(content)
    f.close()
    return f.name


def test_main_single_file_text(monkeypatch, capsys):
    path = _write_tmp_sql("CREATE TABLE t (id bigint COMMENT '主键') COMMENT='t';\n")
    try:
        monkeypatch.setattr(sys, "argv", ["ddl_check", path])
        rc = ddl_check.main()
        assert "DDL 审查报告" in capsys.readouterr().out
        assert rc == 1   # 缺必含字段 → 有强制问题
    finally:
        os.unlink(path)


def test_main_json_format(monkeypatch, capsys):
    path = _write_tmp_sql("CREATE TABLE t (id bigint COMMENT '主键') COMMENT='t';\n")
    try:
        monkeypatch.setattr(sys, "argv", ["ddl_check", path, "--format", "json"])
        rc = ddl_check.main()
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, list) and data
        assert rc == 1
    finally:
        os.unlink(path)


def test_main_directory(monkeypatch, capsys):
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "a.sql"), "w") as fh:
            fh.write("CREATE TABLE t (id bigint COMMENT '主键') COMMENT='t';\n")
        monkeypatch.setattr(sys, "argv", ["ddl_check", d])
        rc = ddl_check.main()
        assert "总计" in capsys.readouterr().out
        assert rc == 1


def test_main_no_sql_files(monkeypatch, capsys):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(sys, "argv", ["ddl_check", d])
        rc = ddl_check.main()
        assert "未找到" in capsys.readouterr().err
        assert rc == 2


# ── 解析层放宽（表名/字段名非法字符可达 + 跨行/注释异常）────────────────────

def test_table_name_chars_reachable():
    """表名含非法字符 '-' → 「表名字符」检出；表本身仍被解析。"""
    issues = _issues_for(
        "CREATE TABLE t-order-info (\n"
        "  id bigint(20) NOT NULL COMMENT '主键id',\n"
        "  creator_id varchar(36) NOT NULL COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL COMMENT '最后更新时间',\n"
        "  del_flag tinyint(4) NOT NULL DEFAULT 0 COMMENT '删除标志'\n"
        ") COMMENT = 'x';\n"
    )
    rules = {i.rule for i in issues}
    assert "表名字符" in rules
    assert "无建表语句" not in rules


def test_table_name_chars_schema_qualified():
    """schema 限定的非法表名 db.t-order 同样可达。"""
    issues = _issues_for(
        "CREATE TABLE db.t-order (\n"
        "  id bigint(20) NOT NULL COMMENT '主键id',\n"
        "  creator_id varchar(36) NOT NULL COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL COMMENT '最后更新时间',\n"
        "  del_flag tinyint(4) NOT NULL DEFAULT 0 COMMENT '删除标志'\n"
        ") COMMENT = 'x';\n"
    )
    assert "表名字符" in {i.rule for i in issues}


def test_field_name_chars_reachable():
    """字段名含非法字符 '-' → 「字段名字符」检出。"""
    issues = _issues_for(_ddl_with_field("order-status varchar(10) COMMENT '订单状态'"))
    assert "字段名字符" in {i.rule for i in issues}


def test_backtick_bad_chars_reachable():
    """反引号包裹的非法字符表名/字段名同样可达（非反引号名不受影响）。"""
    ddl = (
        "CREATE TABLE `t-order` (\n"
        "  id bigint(20) NOT NULL COMMENT '主键id',\n"
        "  `user-name` varchar(20) NOT NULL COMMENT '用户名',\n"
        "  creator_id varchar(36) NOT NULL COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL COMMENT '最后更新时间',\n"
        "  del_flag tinyint(4) NOT NULL DEFAULT 0 COMMENT '删除标志'\n"
        ") COMMENT = 'x';\n"
    )
    rules = {i.rule for i in _issues_for(ddl)}
    assert "表名字符" in rules and "字段名字符" in rules


def test_create_table_paren_next_line_parsed():
    """CREATE TABLE t\\n( 跨行：表体起点扫描不吞并后续语句。"""
    tables = ddl_check.extract_tables(
        "CREATE TABLE t_a\n(\n"
        "  id bigint(20) NOT NULL COMMENT '主键id',\n"
        "  creator_id varchar(36) NOT NULL COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL COMMENT '创建时间'\n"
        ") COMMENT = 'x';\n"
    )
    assert len(tables) == 1 and tables[0].name == "t_a"
    assert any(f.name == "creator_id" for f in tables[0].fields)


# ── 注释含括号的解析健壮性（引号内括号不参与表体深度计数）────────────────

def _minimal_two_tables(first_table_comment_line: str) -> str:
    """两张表的最小 DDL：第一张表某字段注释含指定文本，第二张表随后。"""
    return (
        "CREATE TABLE t_first (\n"
        f"  slug varchar(128) DEFAULT NULL COMMENT '{first_table_comment_line}',\n"
        "  id bigint(20) NOT NULL COMMENT '主键id'\n"
        ") COMMENT = 'x';\n"
        "CREATE TABLE t_second (\n"
        "  id bigint(20) NOT NULL COMMENT '主键id'\n"
        ") COMMENT = 'y';\n"
    )


def test_comment_with_unpaired_paren_does_not_break_parsing():
    """注释含未配对半角括号 '(' → 引号内括号不计数，解析不吞行。"""
    tables = ddl_check.extract_tables(_minimal_two_tables("节点slug(url短标签"))
    assert len(tables) == 2
    assert tables[0].name == "t_first"
    assert any(f.name == "id" for f in tables[0].fields)
    assert tables[1].name == "t_second"


# ── TEXT/BLOB/JSON 族类型不允许 DEFAULT（MySQL 语法限制）────────────────

def test_text_default_null_flagged():
    """mediumtext DEFAULT NULL → 报『TEXT类型含DEFAULT』强制违规。"""
    issues = _issues_for(_ddl_with_field("node_text mediumtext DEFAULT NULL COMMENT '节点正文'"))
    assert any(
        i.rule == "TEXT类型含DEFAULT" and i.severity == Severity.MANDATORY
        and "node_text" in i.description
        for i in issues
    )


def test_text_default_value_flagged():
    """text DEFAULT 'x' → 同样违规（该族类型不能有任何 DEFAULT）。"""
    issues = _issues_for(_ddl_with_field("detl_json text DEFAULT '' COMMENT '明细json'"))
    assert any(
        i.rule == "TEXT类型含DEFAULT"
        for i in issues
    )


def test_text_without_default_ok():
    """mediumtext 无 DEFAULT → 不报『TEXT类型含DEFAULT』。"""
    issues = _issues_for(_ddl_with_field("node_text mediumtext COMMENT '节点正文'"))
    assert all(i.rule != "TEXT类型含DEFAULT" for i in issues)


def test_text_default_in_comment_not_flagged():
    """注释文案含『默认值』字样 → 误报防护，不报『TEXT类型含DEFAULT』。"""
    issues = _issues_for(_ddl_with_field("node_text mediumtext COMMENT '正文,默认值为空'"))
    assert all(i.rule != "TEXT类型含DEFAULT" for i in issues)


def test_blob_default_flagged():
    """blob 族同样不能有 DEFAULT。"""
    issues = _issues_for(_ddl_with_field("file_data blob DEFAULT NULL COMMENT '文件数据'"))
    assert any(i.rule == "TEXT类型含DEFAULT" for i in issues)


def test_comment_with_fullwidth_paren_does_not_break_parsing():
    """注释含全角括号 '）'（不闭合半角 '('）→ 不产生 CREATE 字段噪音。"""
    tables = ddl_check.extract_tables(_minimal_two_tables("节点slug(url短标签）"))
    assert len(tables) == 2
    assert all(t.name in ("t_first", "t_second") for t in tables)
    # 不应把 CREATE 误解析为字段
    assert not any(f.name.upper() == "CREATE" for t in tables for f in t.fields)


def test_comment_with_paired_paren_parsed_normally():
    """注释含配对半角括号 '(url短标签)' → 正常解析。"""
    tables = ddl_check.extract_tables(_minimal_two_tables("节点slug(url短标签)"))
    assert len(tables) == 2
    assert any(f.name == "slug" for f in tables[0].fields)


def test_comment_escaped_quote_does_not_break_parsing():
    """注释含 SQL 转义单引号 '' → 引号状态机不提前闭合。"""
    tables = ddl_check.extract_tables(_minimal_two_tables("it''s ok (测试"))
    assert len(tables) == 2
    assert tables[1].name == "t_second"


def test_fullwidth_paren_comment_still_flagged():
    """全角括号注释：解析恢复后仍应报『全角字符』违规。"""
    issues = _issues_for(_ddl_with_field("node_slug varchar(128) COMMENT '节点slug(url短标签）'"))
    assert any(
        i.rule == "全角字符" and "）" in i.description
        for i in issues
    )


def test_multiple_tables_not_merged():
    """多建表语句：前一表体闭合后不得吞并后续 CREATE TABLE。"""
    tables = ddl_check.extract_tables(
        "CREATE TABLE t_one (\n"
        "  id bigint(20) NOT NULL COMMENT '主键id'\n"
        ");\n"
        "CREATE TABLE t_two (\n"
        "  id bigint(20) NOT NULL COMMENT '主键id',\n"
        "  name varchar(20) NOT NULL COMMENT '名称'\n"
        ") COMMENT = 'x';\n"
    )
    assert len(tables) == 2
    one = next(t for t in tables if t.name == "t_one")
    two = next(t for t in tables if t.name == "t_two")
    assert len(one.fields) == 1
    assert len(two.fields) == 2  # t_two 字段未被 t_one 吞掉


def test_field_continuation_no_bogus_field():
    """字段跨行定义（NOT NULL 续行）不产生垃圾字段。"""
    issues = _issues_for(
        "CREATE TABLE t_a (\n"
        "  id bigint(20)\n"
        "  NOT NULL COMMENT '主键id',\n"
        "  creator_id varchar(36) NOT NULL COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL COMMENT '最后更新时间',\n"
        "  del_flag tinyint(4) NOT NULL DEFAULT 0 COMMENT '删除标志'\n"
        ") COMMENT = 'x';\n"
    )
    rules = {i.rule for i in issues}
    assert "字段数量" not in rules          # 续行行未变成伪字段
    assert "字段名开头" not in rules        # NOT NULL 未被当字段名


def test_hash_comment_stripped():
    """# 行注释剥除：# 行内容不污染解析；「注释符号」仍独立检出。"""
    issues = _issues_for(
        "# 用户表\n"
        "CREATE TABLE t_a (\n"
        "  id bigint(20) NOT NULL COMMENT '主键id',\n"
        "  # 这是字段级 # 注释\n"
        "  creator_id varchar(36) NOT NULL COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL COMMENT '最后更新时间',\n"
        "  del_flag tinyint(4) NOT NULL DEFAULT 0 COMMENT '删除标志'\n"
        ") COMMENT = 'x';\n"
    )
    rules = {i.rule for i in issues}
    assert "注释符号" in rules              # # 注释本身仍报违规
    assert "无建表语句" not in rules        # # 注释行未干扰 CREATE 解析
    assert all(i.rule != "字段数量" for i in issues)


def test_index_name_with_dash_parsed():
    """索引名含非法字符 '-' 仍被解析（命名规则可达）。"""
    issues = _issues_for(
        "CREATE TABLE t_a (\n"
        "  id bigint(20) NOT NULL COMMENT '主键id',\n"
        "  order_no varchar(36) NOT NULL COMMENT '订单编号',\n"
        "  creator_id varchar(36) NOT NULL COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL COMMENT '最后更新时间',\n"
        "  del_flag tinyint(4) NOT NULL DEFAULT 0 COMMENT '删除标志',\n"
        "  UNIQUE KEY uk-order-no (order_no)\n"
        ") COMMENT = 'x';\n"
    )
    assert "唯一索引命名" in {i.rule for i in issues}


def test_create_table_no_body_skipped():
    """CREATE TABLE 后无表体 '('（孤立语句）→ 跳过该行，仍报「无建表语句」。"""
    issues = _issues_for("CREATE TABLE t_a;\n")
    assert "无建表语句" in {i.rule for i in issues}


def test_create_table_no_body_does_not_swallow_next_table():
    """无表体 CREATE TABLE t_a; 后跟合法建表 → t_a 丢弃，t_b 表体归属自身（PR #98 评论 1）。"""
    tables = ddl_check.extract_tables(
        "CREATE TABLE t_a;\n"
        "CREATE TABLE t_b (\n"
        "  id bigint(20) NOT NULL COMMENT '主键id'\n"
        ") COMMENT = 'x';\n"
    )
    assert [t.name for t in tables] == ["t_b"]


def test_create_table_no_body_not_swallowing_paren_statement():
    """无表体 CREATE TABLE 后跟含括号非建表语句 → 不产生幽灵表（PR #98 评论 1）。"""
    issues = _issues_for(
        "CREATE TABLE t_a;\n"
        "INSERT INTO t_x (a, b) VALUES (1, 2);\n"
    )
    assert "无建表语句" in {i.rule for i in issues}


# ── Sourcery review bug 回归测试（PR #237 反馈） ────────────────────────────


def test_required_field_type_fullmatch_substring_not_ok():
    """Sourcery #1：必含字段类型正则用 fullmatch,避免 `int` 误匹配 `point`。

    原实现 `re.search("(int|bigint)", ...)` 会让任何含 `int` 子串的类型通过,
    现在 fullmatch 要求整段匹配,`id point` 应被报违规。
    """
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id point NOT NULL COMMENT '主键id',\n"  # type 含 `int` 子串但不是 int/bigint
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]'\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert any(
        i.rule == "必含字段定义不一致" and "id" in i.location
        for i in issues
    ), "fullmatch 应当把 id 的非 int/bigint 类型识别为违规"


def test_abbreviation_composite_no_duplicate_violation():
    """Sourcery #2：复合短语命中字典后,其覆盖的分词不再重复报告。

    `cargo_owner` 命中 `cargo_owner → cgoer`（第一层）后,`cargo → cgo` 不应再报。
    """
    from abbreviations import iter_abbrev_violations
    violations = iter_abbrev_violations("cargo_owner")
    parts_hit = {p for p, _ in violations}
    assert ("cargo_owner", "cgoer") in violations
    assert "cargo" not in parts_hit, (
        f"复合短语命中后,单词 cargo 不应再报;实际 violations={violations}"
    )


def test_index_name_strict_order_required():
    """Sourcery #3：索引名必须按列声明顺序精确拼接,集合比较会放过乱序。"""
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id bigint COMMENT '主键id',\n"
        "  a varchar(36) NOT NULL DEFAULT '' COMMENT '字段a',\n"
        "  b varchar(36) NOT NULL DEFAULT '' COMMENT '字段b',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]',\n"
        "  KEY ix_b_a (a, b)\n"  # 顺序错乱（声明是 a,b 但索引名是 b,a）
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert any(
        i.rule == "索引名未包含全部字段" and "ix_b_a" in i.location
        for i in issues
    ), "乱序索引名应被识别为违规"


def test_index_name_extra_token_flagged():
    """Sourcery #3 延伸：索引名含额外 token（如 `ix_a_b_extra`）也应被报。"""
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id bigint COMMENT '主键id',\n"
        "  a varchar(36) NOT NULL DEFAULT '' COMMENT '字段a',\n"
        "  b varchar(36) NOT NULL DEFAULT '' COMMENT '字段b',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]',\n"
        "  KEY ix_a_b_extra (a, b)\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert any(i.rule == "索引名未包含全部字段" for i in issues)


# ── 第二轮 review 回归（Sourcery #1/#4/#5 + CodeRabbit）───────────────────


def test_id_type_allows_display_width():
    """CodeRabbit：MySQL 5.7 SHOW CREATE TABLE 输出 bigint(20)/int(11)，
    id 带显示宽度不算必含字段类型不一致（point 等非法类型仍被拒）。"""
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id bigint(20) NOT NULL COMMENT '主键id',\n"
        "  ext varchar(50) NOT NULL DEFAULT '' COMMENT '扩展',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]'\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert all(i.rule != "必含字段定义不一致" for i in issues)


def test_abbreviation_longest_match_composite_prefix():
    """CodeRabbit：cargo_owner_id 应命中复合短语 cargo_owner → cgoer，
    而不是拆词只报 cargo → cgo（会把整改误导成 cgo_owner_id）。"""
    violations = ddl_check.iter_abbrev_violations("cargo_owner_id")
    assert ("cargo_owner", "cgoer") in violations
    assert all(part != "cargo" for part, _ in violations)


def test_abbreviation_same_value_compound_not_split():
    """CodeRabbit：同字复合 key（value_date/public_sea 是字典收录的标准名）
    不应拆词误报 value → val / public → share。"""
    assert ddl_check.iter_abbrev_violations("value_date") == []
    assert ddl_check.iter_abbrev_violations("public_sea") == []


def test_composite_index_with_required_field_no_conflict():
    """CodeRabbit：组合索引（如 ix_mch_id_last_update_time）不再同时触发
    索引缩写未规范化 与 索引名未包含全部字段 两条强制规则。"""
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id bigint COMMENT '主键id',\n"
        "  mch_id varchar(32) NOT NULL DEFAULT '' COMMENT '商户id',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]',\n"
        "  KEY ix_mch_id_last_update_time (mch_id, last_update_time)\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert all(i.rule != "索引缩写未规范化" for i in issues)
    assert all(i.rule != "索引名未包含全部字段" for i in issues)


def test_index_prefix_length_and_desc_modifier_ok():
    """CodeRabbit：前缀索引 name(10) 与 DESC 修饰是合法 MySQL DDL，
    不应误报 索引名未包含全部字段。"""
    ddl = (
        "CREATE TABLE t_demo (\n"
        "  id bigint COMMENT '主键id',\n"
        "  name varchar(64) NOT NULL DEFAULT '' COMMENT '名称',\n"
        "  a varchar(36) NOT NULL DEFAULT '' COMMENT '字段a',\n"
        "  creator_id varchar(36) NOT NULL DEFAULT '' COMMENT '创建人id',\n"
        "  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',\n"
        "  last_updater_id varchar(36) NOT NULL DEFAULT '' COMMENT '最后更新人id',\n"
        "  last_update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',\n"
        "  del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]',\n"
        "  KEY ix_name (name(10)),\n"
        "  KEY ix_a (a DESC)\n"
        ") COMMENT='demo';\n"
    )
    issues = _issues_for(ddl)
    assert all(i.rule != "索引名未包含全部字段" for i in issues)


def test_comment_multiple_bracket_segments_all_checked():
    """Sourcery #4：注释含多个 [..] 段时逐段校验，后续格式错误段也应报。"""
    issues = _issues_for(
        _ddl_with_field(
            "status varchar(16) NOT NULL DEFAULT '' COMMENT '状态[1-有效][待支付/已支付]'"
        )
    )
    hits = [i for i in issues if i.rule == "注释取值范围格式"]
    assert any("待支付/已支付" in i.description for i in hits)


def test_comment_multiple_paren_segments_all_checked():
    """Sourcery #5：注释含多个 (..) 段时逐段校验，后续空段/重复段也应报。"""
    empty = _issues_for(
        _ddl_with_field("parent_id bigint NOT NULL DEFAULT 0 COMMENT '父参数id(0=根)()'")
    )
    assert any(i.rule == "补充信息为空" for i in empty)
    dup = _issues_for(
        _ddl_with_field(
            "order_no varchar(36) NOT NULL DEFAULT '' COMMENT '订单编号(备注)(订单编号)'"
        )
    )
    assert any(i.rule == "补充信息冗余" for i in dup)


# ── 枚举字段类型（注释声明值域 + 字符串类型 → 强制 tinyint）───────────────

def _enum_hits(issues):
    return [i for i in issues if i.rule == "枚举字段类型"]


def test_enum_symbolic_domain_flagged_mandatory():
    """注释声明符号枚举值域 + varchar → 强制报「枚举字段类型」。"""
    issues = _issues_for(_ddl_with_field(
        "verf_status varchar(16) NOT NULL DEFAULT '' "
        "COMMENT '验证状态[pending,success,failed]'"
    ))
    hits = _enum_hits(issues)
    assert len(hits) == 1 and hits[0].severity == Severity.MANDATORY


def test_enum_numeric_domain_flagged():
    """数字码值域 + varchar（码值未落 tinyint）同样强制报。"""
    issues = _issues_for(_ddl_with_field(
        "auth_type varchar(32) NOT NULL DEFAULT '' COMMENT '鉴权类型[10-NONE,20-HMAC]'"
    ))
    assert _enum_hits(issues), issues


def test_enum_tinyint_ok():
    """tinyint + 数字码值域 → 不报。"""
    issues = _issues_for(_ddl_with_field(
        "auth_type tinyint(4) NOT NULL DEFAULT 0 COMMENT '鉴权类型[10-无,20-HMAC]'"
    ))
    assert not _enum_hits(issues)


def test_enum_without_declared_domain_not_flagged():
    """仅字段名像状态、注释未声明值域 → 不报（避免 order_status 泛化误伤）。"""
    issues = _issues_for(_ddl_with_field(
        "order_status varchar(10) NOT NULL COMMENT '订单状态'"
    ))
    assert not _enum_hits(issues)


def test_enum_single_value_domain_not_flagged():
    """仅 1 个值不算枚举值域 → 不报。"""
    issues = _issues_for(_ddl_with_field(
        "biz_scope varchar(16) NOT NULL DEFAULT '' COMMENT '业务范围[ALL]'"
    ))
    assert not _enum_hits(issues)


def test_enum_multi_value_list_not_flagged():
    """注释提示「逗号分隔」的多值列表列（名称无列表后缀）→ 不报。

    用例字段名刻意不带 `_list/_lines` 等后缀，确保覆盖的是注释提示分支
    （带后缀的用例另有 test_enum_multi_value_list_suffix_not_flagged）。
    """
    issues = _issues_for(_ddl_with_field(
        "biz_scope varchar(128) NOT NULL DEFAULT '' "
        "COMMENT '对接业务线,逗号分隔[10-物流,20-商贸]'"
    ))
    assert not _enum_hits(issues)


def test_enum_text_type_not_flagged():
    """TEXT 族不参与枚举规则：由「禁用类型」单独报告，避免同字段被双报。"""
    issues = _issues_for(_ddl_with_field(
        "payload_body text COMMENT '报文载荷[10-A,20-B]'"
    ))
    assert not _enum_hits(issues)
    assert any(i.rule == "禁用类型" for i in issues)


def test_enum_range_comment_not_flagged():
    """注释声明的是数值区间（费率区间[0,100]）→ 不是枚举值域，不报。"""
    issues = _issues_for(_ddl_with_field(
        "rate_range varchar(32) NOT NULL DEFAULT '' COMMENT '费率区间[0,100]'"
    ))
    assert not _enum_hits(issues)


@pytest.mark.parametrize("comment", [
    "业务范围[1-订单,2-结算]",
    "适用范围[1-线上,2-线下]",
    "范围类型[1-闭区间,2-开区间]",
])
def test_enum_business_scope_still_flagged(comment):
    """注释含「范围」但值域是业务枚举（非全数字）→ 仍须报，不得静默漏报。"""
    issues = _issues_for(_ddl_with_field(
        f"biz_scope varchar(16) NOT NULL DEFAULT '' COMMENT '{comment}'"
    ))
    assert len(_enum_hits(issues)) == 1


def test_enum_text_type_flagged_when_text_check_off():
    """项目把 TEXT 检查置 off 时，TEXT 族字段的枚举规则兜底，不留检查真空。"""
    issues = _issues_for(
        _ddl_with_field("payload_body text COMMENT '报文载荷[10-A,20-B]'"),
        config={"text_type_severity": "off"},
    )
    assert len(_enum_hits(issues)) == 1
    assert not any(i.rule == "禁用类型" for i in issues)


def test_enum_decimal_domain_not_flagged():
    """小数形态的值域（[0.00,100.00]）按数值区间处理，不报枚举。"""
    issues = _issues_for(_ddl_with_field(
        "amount_scope varchar(32) NOT NULL DEFAULT '' COMMENT '金额取值[0.00,100.00]'"
    ))
    assert not _enum_hits(issues)


def test_enum_multi_value_list_suffix_not_flagged():
    """列表列名后缀（_ids）即使注释含值域也不报。"""
    issues = _issues_for(_ddl_with_field(
        "msg_type_ids varchar(64) NOT NULL DEFAULT '' COMMENT '消息类型[10-A,20-B]'"
    ))
    assert not _enum_hits(issues)


def test_enum_del_flag_exempt():
    """del_flag 是规范固定字段（[0-否,1-是]），不按业务枚举重复报告。

    其类型须为 tinyint（规范 §四），属人工确认项，见 ddl-manual-rules。
    """
    issues = _issues_for(_ddl_with_field(
        "del_flag varchar(1) NOT NULL DEFAULT '0' COMMENT '删除标志[0-否,1-是]'"
    ))
    assert not _enum_hits(issues)


def test_enum_rule_disabled_by_config():
    """enum_field_type_check=false → 关闭枚举检查。"""
    config = ddl_check.load_config()[0]
    config["enum_field_type_check"] = False
    issues = _issues_for(_ddl_with_field(
        "auth_type varchar(32) NOT NULL DEFAULT '' COMMENT '鉴权类型[10-NONE,20-HMAC]'"
    ), config=config)
    assert not _enum_hits(issues)


# ── 字段长度上限（命名模式规则，非字段名白名单）──────────────────────────

def _length_hits(issues):
    return [i for i in issues if i.rule == "字段长度上限"]


def test_length_rule_id_suffix_over_limit_mandatory():
    """*_id 字段超过上限 36 → 强制。"""
    issues = _issues_for(_ddl_with_field("co_id varchar(64) NOT NULL DEFAULT '' COMMENT '公司id'"))
    hits = _length_hits(issues)
    assert len(hits) == 1 and hits[0].severity == Severity.MANDATORY


def test_length_rule_key_suffix_not_checked_by_default():
    """默认规则只约束 *_id；*_key（access_key/biz_key 等长标识）默认不报。"""
    issues = _issues_for(_ddl_with_field("app_key varchar(64) NOT NULL DEFAULT '' COMMENT '应用Key'"))
    assert not _length_hits(issues)


def test_length_rule_first_match_wins_allows_specific_exception():
    """更具体的模式前置可放宽单族字段，且不叠加后续规则。"""
    config = ddl_check.load_config()[0]
    config["field_length_rules"] = [
        {"pattern": r"(^|_)idemp_id$", "max": 64},
        {"pattern": r"(^|_)id$", "max": 36},
    ]
    idemp = _issues_for(
        _ddl_with_field("evt_idemp_id varchar(64) NOT NULL DEFAULT '' COMMENT '事件幂等id'"),
        config=config,
    )
    assert not _length_hits(idemp)
    plain = _issues_for(
        _ddl_with_field("co_id varchar(64) NOT NULL DEFAULT '' COMMENT '公司id'"),
        config=config,
    )
    assert _length_hits(plain)


def test_length_rule_at_or_below_limit_ok():
    """上限判定为「不超过」：36 与更短的 32 均合规。"""
    for length in (36, 32):
        issues = _issues_for(
            _ddl_with_field(f"mch_id varchar({length}) NOT NULL DEFAULT '' COMMENT '商户id'")
        )
        assert not _length_hits(issues), length


def test_length_rule_requires_token_boundary():
    """仅按分词边界匹配：paid/valid 这类普通词不以 id 分词结尾，不报。"""
    issues = _issues_for(_ddl_with_field("is_paid varchar(64) NOT NULL DEFAULT '' COMMENT '是否支付'"))
    assert not _length_hits(issues)


def test_length_rule_plural_list_not_matched():
    """复数/列表列（*_ids）不是单值标识符，不匹配 (^|_)id$。"""
    issues = _issues_for(
        _ddl_with_field("cred_ids varchar(256) NOT NULL DEFAULT '' COMMENT '凭证id列表'")
    )
    assert not _length_hits(issues)


def test_length_rule_ignores_non_string_type():
    """非 char/varchar 类型（bigint）不在长度规则范围。"""
    issues = _issues_for(_ddl_with_field("co_id bigint(20) NOT NULL DEFAULT 0 COMMENT '公司id'"))
    assert not _length_hits(issues)


def test_length_rule_override_by_config(tmp_path):
    """配置整体替换规则集（id 上限放宽到 64）→ 不再报。"""
    (tmp_path / ".ddl-guard.json").write_text(
        json.dumps({"field_length_rules": [{"pattern": "(^|_)id$", "max": 64}]}),
        encoding="utf-8",
    )
    cfg, error = ddl_check.load_config(base_dir=str(tmp_path))
    assert error is None
    issues = _issues_for(
        _ddl_with_field("co_id varchar(64) NOT NULL DEFAULT '' COMMENT '公司id'"),
        config=cfg,
    )
    assert not _length_hits(issues)


def test_length_rule_empty_list_disables_check(tmp_path):
    """field_length_rules=[] → 关闭长度上限检查。"""
    (tmp_path / ".ddl-guard.json").write_text(
        json.dumps({"field_length_rules": []}), encoding="utf-8"
    )
    cfg, error = ddl_check.load_config(base_dir=str(tmp_path))
    assert error is None
    issues = _issues_for(
        _ddl_with_field("co_id varchar(64) NOT NULL DEFAULT '' COMMENT '公司id'"),
        config=cfg,
    )
    assert not _length_hits(issues)


def test_length_rule_custom_pattern_applies():
    """自定义模式（*_name 上限 20）立即生效——规则可扩展，无需改代码。"""
    config = ddl_check.load_config()[0]
    config["field_length_rules"] = [{"pattern": r"(^|_)name$", "max": 20}]
    issues = _issues_for(
        _ddl_with_field("msg_type_name varchar(50) NOT NULL DEFAULT '' COMMENT '消息类型名称'"),
        config=config,
    )
    assert _length_hits(issues)


def test_length_rule_recommended_severity():
    """规则可声明推荐级别，避免全部强制。"""
    config = ddl_check.load_config()[0]
    config["field_length_rules"] = [
        {"pattern": r"(^|_)id$", "max": 36, "severity": "recommended"}
    ]
    issues = _issues_for(
        _ddl_with_field("co_id varchar(64) NOT NULL DEFAULT '' COMMENT '公司id'"),
        config=config,
    )
    hits = _length_hits(issues)
    assert len(hits) == 1 and hits[0].severity == Severity.RECOMMENDED


# ── TEXT 族级别可配置（规范默认强制，可按项目降级/关闭）─────────────────

def test_text_type_default_mandatory():
    """默认配置：text 为强制（与规范【强制】一致）。"""
    issues = _issues_for(_ddl_with_field("payload text COMMENT '报文载荷'"))
    hits = [i for i in issues if i.rule == "禁用类型"]
    assert hits and hits[0].severity == Severity.MANDATORY


def test_text_type_recommended_by_config():
    """text_type_severity=recommended → text 降为推荐级。"""
    config = ddl_check.load_config()[0]
    config["text_type_severity"] = "recommended"
    issues = _issues_for(_ddl_with_field("payload text COMMENT '报文载荷'"), config=config)
    hits = [i for i in issues if i.rule == "禁用类型"]
    assert hits and hits[0].severity == Severity.RECOMMENDED


def test_text_type_off_by_config():
    """text_type_severity=off → text 不检查。"""
    config = ddl_check.load_config()[0]
    config["text_type_severity"] = "off"
    issues = _issues_for(_ddl_with_field("payload text COMMENT '报文载荷'"), config=config)
    assert not [i for i in issues if i.rule == "禁用类型"]


def test_blob_still_mandatory_when_text_downgraded():
    """配置只影响 TEXT 族，blob 恒为强制。"""
    config = ddl_check.load_config()[0]
    config["text_type_severity"] = "off"
    issues = _issues_for(_ddl_with_field("attach_blob blob COMMENT '附件内容'"), config=config)
    hits = [i for i in issues if i.rule == "禁用类型"]
    assert hits and hits[0].severity == Severity.MANDATORY


# ── 配置加载 ─────────────────────────────────────────────────────────────

def test_load_config_defaults():
    cfg, error = ddl_check.load_config()
    assert error is None
    assert cfg["text_type_severity"] == "mandatory"
    assert cfg["enum_field_type_check"] is True
    assert cfg["field_length_rules"] == ddl_check.DEFAULT_CONFIG["field_length_rules"]


def test_load_config_autodiscover(tmp_path):
    (tmp_path / ".ddl-guard.json").write_text(
        json.dumps({"text_type_severity": "recommended"}), encoding="utf-8"
    )
    cfg, error = ddl_check.load_config(base_dir=str(tmp_path))
    assert error is None and cfg["text_type_severity"] == "recommended"


def test_load_config_keeps_untouched_defaults(tmp_path):
    """只配置一项时，其余默认项保留。"""
    (tmp_path / ".ddl-guard.json").write_text(
        json.dumps({"enum_field_type_check": False}), encoding="utf-8"
    )
    cfg, error = ddl_check.load_config(base_dir=str(tmp_path))
    assert error is None
    assert cfg["enum_field_type_check"] is False
    assert cfg["text_type_severity"] == "mandatory"
    assert cfg["field_length_rules"]  # 默认模式规则仍在


def test_load_config_missing_explicit_path(tmp_path):
    _, error = ddl_check.load_config(str(tmp_path / "nope.json"))
    assert error and "不存在" in error


def test_load_config_invalid_severity(tmp_path):
    (tmp_path / ".ddl-guard.json").write_text(
        json.dumps({"text_type_severity": "warn"}), encoding="utf-8"
    )
    _, error = ddl_check.load_config(base_dir=str(tmp_path))
    assert error and "text_type_severity" in error


def test_load_config_invalid_rule_max(tmp_path):
    (tmp_path / ".ddl-guard.json").write_text(
        json.dumps({"field_length_rules": [{"pattern": "(^|_)id$", "max": "36"}]}),
        encoding="utf-8",
    )
    _, error = ddl_check.load_config(base_dir=str(tmp_path))
    assert error and "max" in error


def test_load_config_invalid_rule_pattern(tmp_path):
    (tmp_path / ".ddl-guard.json").write_text(
        json.dumps({"field_length_rules": [{"pattern": "([", "max": 36}]}), encoding="utf-8"
    )
    _, error = ddl_check.load_config(base_dir=str(tmp_path))
    assert error and "正则非法" in error


def test_load_config_invalid_rule_severity(tmp_path):
    (tmp_path / ".ddl-guard.json").write_text(
        json.dumps({"field_length_rules": [
            {"pattern": "(^|_)id$", "max": 36, "severity": "warn"}]}), encoding="utf-8"
    )
    _, error = ddl_check.load_config(base_dir=str(tmp_path))
    assert error and "severity" in error


def test_load_config_broken_json(tmp_path):
    (tmp_path / ".ddl-guard.json").write_text("{not json", encoding="utf-8")
    _, error = ddl_check.load_config(base_dir=str(tmp_path))
    assert error and "解析失败" in error


def test_load_config_unknown_key_rejected(tmp_path):
    """键名拼错（text_severity）必须报错，不得静默按默认值跑（fail-open 防线）。"""
    (tmp_path / ".ddl-guard.json").write_text(
        json.dumps({"text_severity": "off"}), encoding="utf-8"
    )
    _, error = ddl_check.load_config(base_dir=str(tmp_path))
    assert error and "未识别" in error and "text_severity" in error


def test_load_config_unknown_rule_key_rejected(tmp_path):
    """规则项内键名多余/拼错（note、max_len）必须报错，否则该项静默失效。"""
    for extra in ({"note": "幂等键 36"}, {"max_len": 36}):
        item = {"pattern": "(^|_)id$", "max": 36}
        item.update(extra)
        (tmp_path / ".ddl-guard.json").write_text(
            json.dumps({"field_length_rules": [item]}), encoding="utf-8"
        )
        _, error = ddl_check.load_config(base_dir=str(tmp_path))
        assert error and "未识别的键" in error
        assert next(iter(extra)) in error


def test_load_config_walk_up_to_project_root(tmp_path):
    """配置放项目根、审查子目录时仍能自动发现（向上回溯到 .git 为止）。"""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".ddl-guard.json").write_text(
        json.dumps({"text_type_severity": "recommended"}), encoding="utf-8"
    )
    nested = tmp_path / "db" / "migration"
    nested.mkdir(parents=True)
    cfg, error = ddl_check.load_config(base_dir=str(nested))
    assert error is None and cfg["text_type_severity"] == "recommended"


def test_load_config_walk_up_stops_at_git_root(tmp_path):
    """越过项目根（含 .git 的目录）后不再向上找，避免误读上级目录配置。"""
    (tmp_path / ".ddl-guard.json").write_text(
        json.dumps({"text_type_severity": "off"}), encoding="utf-8"
    )
    project = tmp_path / "proj"
    (project / ".git").mkdir(parents=True)
    nested = project / "db"
    nested.mkdir()
    cfg, error = ddl_check.load_config(base_dir=str(nested))
    assert error is None and cfg["text_type_severity"] == "mandatory"
    assert cfg["_source"] is None


def test_load_config_records_source(tmp_path):
    """生效配置来源记入 _source（报告可溯源降级/替换）。"""
    (tmp_path / ".ddl-guard.json").write_text("{}\n", encoding="utf-8")
    cfg, error = ddl_check.load_config(base_dir=str(tmp_path))
    assert error is None
    assert cfg["_source"] == str((tmp_path / ".ddl-guard.json").resolve())


@pytest.mark.parametrize("payload", [
    {"enum_field_type_check": "false"},
    {"field_length_rules": {}},
    {"field_length_rules": ["x"]},
    {"field_length_rules": [{"max": 10}]},
    [],
])
def test_main_config_fail_closed_exit2(monkeypatch, capsys, tmp_path, payload):
    """各类非法配置一律退出码 2（fail-closed），不静默回落默认值。"""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")
    path = _write_tmp_sql("CREATE TABLE t (id bigint COMMENT '主键') COMMENT='t';\n")
    try:
        monkeypatch.setattr(sys, "argv", ["ddl_check", path, "--config", str(bad)])
        assert ddl_check.main() == 2
        assert "配置错误" in capsys.readouterr().err
    finally:
        os.unlink(path)


def test_main_reports_effective_config(monkeypatch, capsys, tmp_path):
    """降级/替换生效时在 stderr 声明，报告不得让读者以为仍是全量【强制】检查。"""
    cfg_path = tmp_path / ".ddl-guard.json"
    cfg_path.write_text(json.dumps({
        "text_type_severity": "off",
        "enum_field_type_check": False,
        "field_length_rules": [{"pattern": "(^|_)code$", "max": 64}],
    }), encoding="utf-8")
    path = _write_tmp_sql("CREATE TABLE t (id bigint COMMENT '主键') COMMENT='t';\n")
    try:
        monkeypatch.setattr(sys, "argv", ["ddl_check", path, "--config", str(cfg_path)])
        ddl_check.main()
        err = capsys.readouterr().err
        assert "生效配置" in err
        assert "text_type_severity=off" in err
        assert "enum_field_type_check=false" in err
        assert "当前无规则覆盖 *_id" in err
    finally:
        os.unlink(path)


def test_main_warns_when_id_cap_widened(monkeypatch, capsys, tmp_path):
    """规则仍覆盖 *_id 但把上限放宽（如 `.*` → 64）时也要提示，不能默不作声。"""
    cfg_path = tmp_path / ".ddl-guard.json"
    cfg_path.write_text(json.dumps({
        "field_length_rules": [{"pattern": ".*", "max": 64}],
    }), encoding="utf-8")
    path = _write_tmp_sql("CREATE TABLE t (id bigint COMMENT '主键') COMMENT='t';\n")
    try:
        monkeypatch.setattr(sys, "argv", ["ddl_check", path, "--config", str(cfg_path)])
        ddl_check.main()
        err = capsys.readouterr().err
        assert "首个命中规则的上限为 64" in err
    finally:
        os.unlink(path)


@pytest.mark.parametrize("rules", [
    [{"pattern": "(^|_)id$", "max": 36}],                              # 边界：等于默认值
    [{"pattern": "(^|_)id$", "max": 32}],                              # 收紧
    [{"pattern": "(^|_)idemp_id$", "max": 64},
     {"pattern": "(^|_)id$", "max": 36}],                              # 具体族前置 + 兜底
])
def test_main_no_warn_when_id_rule_tightened_or_specific(monkeypatch, capsys,
                                                         tmp_path, rules):
    """上限不变/收紧/具体族前置属正常用法，stderr 不得出现上限提示（边界 36 也不提示）。"""
    cfg_path = tmp_path / ".ddl-guard.json"
    cfg_path.write_text(json.dumps({"field_length_rules": rules}), encoding="utf-8")
    path = _write_tmp_sql("CREATE TABLE t (id bigint COMMENT '主键') COMMENT='t';\n")
    try:
        monkeypatch.setattr(sys, "argv", ["ddl_check", path, "--config", str(cfg_path)])
        ddl_check.main()
        err = capsys.readouterr().err
        assert "首个命中规则的上限为" not in err
    finally:
        os.unlink(path)


def test_id_length_cap_first_match_semantics():
    """首个命中规则生效：前置的宽松规则若先命中，实际上限就是它。"""
    rules = [{"pattern": "(^|_)co_id$", "max": 128},
             {"pattern": "(^|_)id$", "max": 36}]
    assert ddl_check.id_length_cap(rules) == 128
    assert ddl_check.id_length_cap([]) is None
    assert ddl_check.id_length_cap([{"pattern": "(^|_)code$", "max": 64}]) is None


def test_main_invalid_config_exit2(monkeypatch, capsys, tmp_path):
    """配置非法 → 退出码 2（fail-closed，不静默用默认值放行）。"""
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"field_length_rules": [{"pattern": "(^|_)id$", "max": -1}]}),
        encoding="utf-8",
    )
    path = _write_tmp_sql("CREATE TABLE t (id bigint COMMENT '主键') COMMENT='t';\n")
    try:
        monkeypatch.setattr(sys, "argv", ["ddl_check", path, "--config", str(bad)])
        assert ddl_check.main() == 2
        assert "配置错误" in capsys.readouterr().err
    finally:
        os.unlink(path)


def test_main_autodiscover_config_downgrades_text(monkeypatch, tmp_path):
    """.ddl-guard.json 自动发现生效：text 降级后不再有强制问题。"""
    ddl = _ddl_with_field("payload text COMMENT '报文载荷'")
    (tmp_path / "a.sql").write_text(ddl, encoding="utf-8")
    (tmp_path / ".ddl-guard.json").write_text(
        json.dumps({"text_type_severity": "recommended"}), encoding="utf-8"
    )
    monkeypatch.setattr(sys, "argv", ["ddl_check", str(tmp_path)])
    assert ddl_check.main() == 0
