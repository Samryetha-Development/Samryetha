# 数据目录外置迁移与更新流程加固

> 背景：修复「每次 dev / main 更新后，上传文件都消失」的 bug。根因是上传目录与 SQLite
> 数据库默认落在**仓库目录内**（`backend/uploads`、`backend/data`），且被 `.gitignore`
> 忽略（=未跟踪文件）。任何执行 `git clean -fdx` / `git reset --hard` 后再 clean / 重新
> clone / 压缩包覆盖的更新流程，都会把这些未跟踪文件一并删掉。

## 1. 本次改了什么（代码侧）

| 项 | 改动 |
|---|---|
| 数据根目录 | 新增配置 `SAMRYETHA_DATA_DIR`，默认落在**仓库之外**（Linux `~/.local/share/samryetha`、macOS `~/Library/Application Support/Samryetha`、Windows `%LOCALAPPDATA%\Samryetha`） |
| 上传目录 | 留空时默认 = `{SAMRYETHA_DATA_DIR}/uploads`（不再 `./uploads`） |
| 数据库 | 留空时默认 = `{SAMRYETHA_DATA_DIR}/app.db`（不再 `./data/app.db`） |
| 显式覆盖 | `UPLOAD_DIR` / `DATABASE_URL` 仍可覆盖（向后兼容，旧 `.env` 里的值照常生效） |
| 启动自检 | 启动时确保上传目录存在，并把实际解析到的 `data_dir / upload_dir / database_url` 及来源（env/默认）打到日志 |
| 仓库内告警 | 若数据根/上传目录/数据库路径任一解析到 git 仓库内，启动会打显著 `WARNING`（不拒绝启动） |

设计取舍：**告警而非拒绝启动**——默认值已经外置，旧部署若还在 `.env` 里写了相对路径，硬拒会让
现有可用环境直接起不来、增加迁移风险；改为显著告警，既能提醒又不阻断。结构上，只要数据在仓库外，
`git clean -fdx` 就再也碰不到它——这是本次修复的核心。

## 2. dev 与 main 共用同一数据目录（管理员 2026-10-20 追加）

目标形态：`dev` 与 `main` 两个部署线**指向同一份数据**——同一个外置数据目录（同一个 `app.db`
与同一套 `uploads/`），通过 `SAMRYETHA_DATA_DIR` 在两个部署的 `.env` 里配置成**同一个绝对路径**。

- **代码**照常通过 git 在 dev/main 之间流动（这是正常的）。
- **数据文件绝不复制**：两边共用同一个目录，切分支 / 切环境 / 重新部署都不会重置数据，也不会出现
  「两边各有一份库、互相覆盖/复制」的错乱。

### 并发写风险结论（两个进程写同一个 SQLite）

- 本项目 SQLite 已开 **WAL** 模式（`backend/src/samryetha/core/db.py` 的 `PRAGMA journal_mode=WAL`），
  并设 `busy_timeout=5000`。WAL 允许多个并发读 + **单写**：同一时刻只有一个进程能写，另一个会等待
  至多 5 秒，超时报 `database is locked`。
- **结论/建议：dev 与 main 不要同时对外提供服务、不要同时写同一个 `app.db`。** 同一份数据只由一个
  「活动」部署写；切换部署线时先停旧再起新。若确需两个进程同时在线，只读的那个也指向同一目录
  （WAL 下读安全），但写操作必须收敛到单一进程，避免两个不同代码版本互相写坏 schema/数据。

## 3. 生产迁移步骤（服务器 43.161.252.86，只出文档不执行）

> 以下命令在服务器仓库目录下执行，**全程不删旧数据**，只有最后确认无误才删。

1. **停后端**（避免 SQLite WAL/SHM 边写边拷）：`pm2 stop samryetha-backend`
2. **备份现有数据**（以生产 main 的那一份为准，dev 的作废但先备份、不直接删）：
   ```bash
   cp -a backend/uploads backend/uploads.bak-$(date +%F)
   cp -a backend/data backend/data.bak-$(date +%F)
   ```
3. **决定新位置**（给一个**仓库之外**的绝对路径，dev 与 main 都用它）：
   ```bash
   echo 'SAMRYETHA_DATA_DIR=/var/lib/samryetha' >> backend/.env
   ```
4. **先 dry-run**：`cd backend && uv run python scripts/migrate-data-dir.py`
5. **正式迁移**（复制 + 逐文件 SHA-256 校验，不删旧目录）：
   `cd backend && uv run python scripts/migrate-data-dir.py --apply`
   脚本把 `backend/uploads` → `{数据根}/uploads`，`backend/data/app.db`（及 `-wal`/`-shm`）→
   `{数据根}/`，并在 `{数据根}/migration-record.json` 留迁移记录（源/目标/文件数/字节数）。
6. **清理旧 `.env` 里的相对路径**：删除或注释 `DATABASE_URL=./data/app.db` 与 `UPLOAD_DIR=./uploads`
   两行，让代码走 `SAMRYETHA_DATA_DIR` 派生（dev 与 main 的 `.env` 都这样改，指向同一路径）。
7. **校验**：`diff -r backend/uploads /var/lib/samryetha/uploads`（或对比文件数/字节/SHA；数据库可用
   sqlite 表行数对比）。
8. **重启并核对日志**：`pm2 restart samryetha-backend && pm2 logs samryetha-backend --lines 50`
   （找 `storage paths — data_dir=...` 与 `data root: ... (source=env)`）。
9. **验证上传/下载正常**后，再决定是否删旧目录：`rm -rf backend/uploads backend/data`（脚本不会自动删）。

### 回滚

发现异常时：把 `.env` 里的 `SAMRYETHA_DATA_DIR` 删掉/改回，恢复旧的相对路径两行，用第 2 步的
`.bak-*` 备份覆盖回 `backend/uploads`、`backend/data`，重启即可回到旧态（旧目录在迁移时从未被删除）。

## 4. 更新流程加固 + 运维一句话

- 数据已移出仓库，现有的 `git pull` / `git clean` / `git reset --hard` 更新**天然不再碰数据目录**。
- **给运维的一句话总结**：更新时**只用 `git clean -fd`（不带 `-x`）**，或对数据路径加 `-e` 排除项；
  **永不使用 `git clean -fdx`**（`-x` 会把被 gitignore 的目录也删掉）。
- 正确的更新流程：`git pull` → 装依赖（`uv sync` / `pnpm install`）→（如数据目录有变）跑迁移脚本
  dry-run 再 `--apply` → 重启 pm2。**全程不得触碰 `SAMRYETHA_DATA_DIR` 指向的数据目录。**

## 5. 跨环境复制逻辑清除清单

排查结论：**仓库内不存在「dev 从 main 复制数据文件」的逻辑**，无需改代码。逐一核对如下：

| 位置 | 原逻辑 | 判定 |
|---|---|---|
| `ForumBootstrap.py:42` `shutil.copy2(example, target)` | 首次引导时把 `.env.example` 复制成 `.env`（配置模板） | 非数据同步，保留 |
| `backend/src/samryetha/feedback/backup.py:99` `shutil.copyfile(source, db_path)` | 应用自身的「备份恢复」功能（从 backups/ 恢复快照到库） | 非跨环境复制，保留 |
| `.github/workflows/dev-to-main-sync.yml` + `.github/scripts/dev-to-main-sync*.mjs` | 自动开 dev→main 的**代码 PR** | 是代码同步，非数据复制，保留 |

若服务器上的 `update.sh`（gitignored、仓库内不可见）里确有「从 main 复制 data/uploads 到 dev」的
逻辑，应按第 2 节改造：**删除复制逻辑，改为让两个部署的 `.env` 都指向同一个 `SAMRYETHA_DATA_DIR`**，
数据不再随分支复制。

## 6. 开发者本地注意

- 本地开发数据现在默认落在系统数据目录（非 `backend/data` / `backend/uploads`）。
  若想沿用仓库内旧位置，在 `backend/.env` 显式写回绝对路径即可，但会失去「更新不清空数据」的保护
  （启动会打 WARNING）。
