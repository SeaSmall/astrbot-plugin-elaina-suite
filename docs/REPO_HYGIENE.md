# 仓库脱敏约定（REPO_HYGIENE）

本仓库是 **public** 仓库。历史里曾经出现过个人与基础设施标识（QQ 号、openid、服务器域名/端口、
用户名、内网 IP、容器名、工作区文件名、少量对话原句），已于 2026-10-29 用 `git-filter-repo`
**重写全部历史**并强推清理掉。

> ⚠️ 提交前请自检：新增的文档 / 测试 / 示例里**不要**再写真实值，一律用下面的占位符。

## 占位符约定

| 被替换的对象 | 现在一律写成 | 备注 |
| --- | --- | --- |
| 主人 QQ 号 | `1000000001` | 出现在 umo 里形如 `default_1000000001:FriendMessage:...` |
| 其他 QQ 号 | `1000000002`、`1000000003` | 递增编号 |
| 平台 openid（微信 / QQ 官方） | `0123456789ABCDEF0123456789ABCDEF` | 32 位大写十六进制占位 |
| 群 openid | `FEDCBA9876543210FEDCBA9876543210` | 同上 |
| 服务器地址 | `ssh.example.com:22` | 不要写真实域名、端口 |
| 服务器用户 | `alice` | 不要写真实用户名 |
| 内网 IP | `192.168.1.10` | 不要写真实网段 |
| 容器 / 服务名 | `astrbot` | 不要写带业务含义的主机名 |
| 聊天记录样例 | 「对话原句已脱敏」 | 不要把真实对话原句写进文档或测试 |
| 工作区里的私人文件名 | `timetable.md` / `project_README.md` 等泛化名 | 不要列出真实私有文件名 |

## 提交前自检

```bash
# 1) 真实值残留（把尖括号里的内容换成你本地笔记里的真实值，别把真实值写进本仓库）
git grep -nE '<QQ号>|<openid>|<域名>|<用户名>|<内网IP>|<容器名>' -- . ':!meme'

# 2) 结构类风险：邮箱 / 手机号 / key 形态
git grep -nE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\b1[3-9][0-9]{9}\b|tp-[A-Za-z0-9]{16,}|sk-[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{20,}|BEGIN [A-Z ]*PRIVATE KEY' -- . ':!meme'

# 3) 已跟踪的二进制/缓存不该进库
git ls-files | grep -E '__pycache__|\.pyc$' || echo "ok: 无缓存文件"
```

期望：第 1、2 条无输出，第 3 条打印 `ok`。

## 其它说明

- `.gitignore` 已忽略 `__pycache__/`、`*.py[cod]`、`.venv/`、`*.log`、IDE 目录。
- 真实服务器配置（含 API Key、真实平台标识）只在服务器 `/opt/astrbot/data/config/` 下，
  **不进仓库**；仓库里的 `_conf_schema.json` 只描述键与默认值。
- 人格来源素材 `assets/persona_source/` 是插件自带人格的一部分，属有意公开内容；
  其中 `MANIFEST.json` 也已改为占位符（`ssh.example.com:22 (user alice)`）。
- 若发现真实值又被提交进来：不要只改工作树——需要再次重写历史
  （`git-filter-repo --replace-text` + `--force` 强推），并通知仓库所有协作者重新 clone。
