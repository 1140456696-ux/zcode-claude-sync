# zcode-claude-sync

双向同步 **ZCode** 与 **Claude Code** 会话内容。

![Platform](https://img.shields.io/badge/Platform-Windows_10%2F11-0078D4)
![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB)
![License](https://img.shields.io/badge/License-GPL--2.0-blue)

- `ZCode` 会话保存在全局 SQLite：`~/.zcode/cli/db/db.sqlite`（`message` + `part` 两张表）
- `Claude Code` 会话保存在 `~/.claude/projects/<slug>/<uuid>.jsonl`（JSONL 行式）

本项目实现两者之间的**双向迁移**：把 ZCode 中的对话搬到 Claude Code 继续使用，反之亦然；角色、时间、工作目录、工具调用等上下文字段都会完整保留。

## 特性

- **双向**：ZCode → Claude Code与 Claude Code → ZCode
- **零写死**：默认展开 `~`，所有路径可通过环境变量覆盖（见下）
- **交互式目录选择**：运行时列出所有工作目录，支持按编号 / 关键字 / 路径筛选
- **工具调用全保留**：`Bash` / `Read` / `Write` 等工具调用和结果完整迁移
- **幂等 & 安全**：目标会话已存在时先确认；`--dry-run` 仅预览不写入；只读源库、写目标
- **纯标准库**，零第三方依赖，Python 3.9+

## 安装

```bash
# 方式一：直接运行源码（无需安装）
python run.py z2c

# 方式二：安装为命令
pip install .
zcsync z2c
```

## 用法

**默认就是交互式向导，不需要任何参数**：

```bash
python run.py
```

启动后按引导一步步操作：
1. **选方向** —— ZCode → Claude 还是 Claude → ZCode
2. **选目录** —— 列出所有工作目录，输入编号 / 关键字 / 路径
3. **选会话** —— 列出该目录的会话，输入编号（`0` = 全选）
4. **开始转换** —— 每个会话完成后打印结果与 resume ID

> 支持多选：目录可用逗号分隔多个编号，会话同理。随时输入 `q` 退出。

### 进阶参数（可选）

不想走向导时，可加方向或路径直接跳过几步：

```bash
# 跳过「选方向」（直接进目录选择）
python run.py z2c
python run.py c2z

# 跳过「选目录」（按关键字/路径过滤后直接进会话选择）
python run.py z2c --path 测试/MI

# 非交互：全部目录、全部会话、不询问
python run.py c2z --all --yes

# 只预览要转换什么，不写入
python run.py --dry-run
```

完整参数：`--path`（路径/关键字过滤）、`--all`（全部目录）、`--yes`（跳过确认）、`--no-input`（非交互）、`--dry-run`（预览不写）、`--help`。

## 路径与环境变量

默认路径（用 `~` 展开）：

| 含义 | 默认 | 环境变量 |
|------|------|----------|
| ZCode 会话库 | `~/.zcode/cli/db/db.sqlite` | `ZCODE_DB` |
| Claude 项目目录 | `~/.claude/projects` | `CLAUDE_PROJECTS_DIR` |

## 兼容性

- 已在 Claude Code `2.1.276` 与对应 ZCode 版本实测（Windows 11）
- 转换后的 Claude 会话可用 `claude --resume <uuid>` 直接恢复，保留完整上下文

## 安全

- **只读源**：`z2c` 只读 ZCode 的 `db.sqlite`；`c2z` 只写 ZCode 库。均不修改源会话
- **覆盖确认**：目标会话已存在时默认确认；`--force` 显式跳过
- **不碰密钥**：不读取/导出任何 API Key、Token、Cookie
- 数据库写入走事务，出错回滚

## 常见用法补充

```bash
# 只预览将迁移哪些会话，不写入（安全）
python run.py z2c --dry-run
python run.py c2z --dry-run

# 非交互：直接迁移完整路径下所有目录
python run.py z2c --all --no-input
```
