# ddl-guard

MySQL DDL 设计与规范审查技能。

## 能力

1. **审查 DDL 文件** — 运行检查脚本自动检查 20+ 条规则，再补充人工判断
2. **设计新表** — 按规范生成合规建表语句

## 快速使用

```bash
# 审查所有 DDL 文件
python3 scripts/ddl_check.py

# 审查指定 DDL 文件
python3 scripts/ddl_check.py path/to/file.sql

# 显式指定项目配置（默认自动查找 .ddl-guard.json）
python3 scripts/ddl_check.py path/to/file.sql --config .ddl-guard.json

# 审查所有 MyBatis SQL 和 PO 类（Java 项目）
python3 scripts/sql_check.py

# 审查指定路径
python3 scripts/sql_check.py path/to/mapper_or_project/

# JSON 格式输出
python3 scripts/ddl_check.py path/to/file.sql --format json
python3 scripts/sql_check.py path/to/project/ --format json
```

**退出码**：`0` = 通过，`1` = 有强制问题，`2` = 运行错误

## 项目级配置（可选）

`.ddl-guard.json` 放在项目根或审查路径下即可自动生效（查找顺序：`--config` > 从审查路径向上
回溯 > 从当前目录向上回溯，均到项目根 `.git` 为止）；不配置时行为与规范【强制】条款一致
（含技能级扩展规则，见下）。配置只调节**检查级别与基线数据**，不改写规范正文；配置非法
（未知配置项、取值非法、正则非法、上限非正整数）以退出码 `2` 失败，不静默回落默认值。
生效配置来源与被降级/替换的检查会打印到 stderr，避免"报告说通过、实际少查一类"。

```json
{
  "text_type_severity": "recommended",
  "enum_field_type_check": true,
  "field_length_rules": [
    {"pattern": "(^|_)idemp_id$", "max": 64},
    {"pattern": "(^|_)id$", "max": 36}
  ]
}
```

| 配置项 | 默认 | 说明 |
|---|---|---|
| `text_type_severity` | `mandatory` | TEXT 族（text/tinytext/mediumtext/longtext）级别：`mandatory`/`recommended`/`off`。DBA 审批允许保留大文本载荷的项目可降级，blob/json 等恒为强制 |
| `enum_field_type_check` | `true`（开启） | 置 `false` 才关闭「注释声明枚举值域却用字符串类型」检查 |
| `field_length_rules` | `*_id ≤ 36` | 命名模式规则（**非字段名白名单**）：`pattern` 对字段小写名 `re.search`，`max` 为长度上限，`severity` 可选 `mandatory`/`recommended`；首个命中规则生效，特殊字段族用更具体模式前置放宽。规则项**只接受这三个键**（多余键视为配置错误，退出码 2）。**该项整体替换默认集**——新增约束时须把 `*_id` 规则一并写出，否则内置的 `*_id ≤ 36` 强制检查会静默消失（脚本在 stderr 提示） |

规则是模式化的而非逐字段枚举：`*_id` 类标识字段统一受 36 上限约束（UUID 带连字符长度），
`*_ids`/`*_id_list` 等列表列不匹配；项目需要额外约束（如 `*_code` ≤ 64）或放宽某族
（如幂等键 64）时只改配置，不改脚本：

```json
{"field_length_rules": [
  {"pattern": "(^|_)idemp_id$", "max": 64},
  {"pattern": "(^|_)id$", "max": 36},
  {"pattern": "(^|_)code$", "max": 64}
]}
```

## 需人工补充的规则

脚本无法覆盖全部规范，以下文档列出了各自需要人工核对的规则，**审查时务必逐项检查**：

| 脚本 | 未覆盖规则文档 |
|---|---|
| `ddl_check.py` | [`ddl-manual-rules.md`](ddl-manual-rules.md) |
| `sql_check.py` | [`sql-manual-rules.md`](sql-manual-rules.md) |

## 协作流程

```
开发人员编写 DDL
       │
       ▼
运行 ddl_check.py 自检 ──── 通过 ──→ 提交审核
       │
     未通过
       │
       ▼
按报告修复 ──→ 重新自检
```

## 相关文件

- 技能定义：[`SKILL.md`](SKILL.md)
- 检查脚本：[`scripts/ddl_check.py`](scripts/ddl_check.py)（DDL）、[`scripts/sql_check.py`](scripts/sql_check.py)（MyBatis SQL + PO 类）
- DDL 人工规则：[`ddl-manual-rules.md`](ddl-manual-rules.md)
- SQL 人工规则：[`sql-manual-rules.md`](sql-manual-rules.md)
- 设计规范：[`steering/database-design-specification.md`](../../steering/database-design-specification.md)
- 审查样例：[`test/`](test/)
- 目录说明：[`badcase/`](badcase/) 回归语料（CI 由仓顶 `scripts/badcase_runner.py` 消费）；[`eval/`](eval/) 评估集；[`test/`](test/) 审查样例——三者均非 skill 运行时物料，模型无需加载
