# 社区/论坛「文件·资源·文库·附件」板块：信息架构与交互设计调研

调研对象：NGA、百度贴吧、知乎、V2EX、豆瓣小组、CSDN 资源下载、蓝奏云/百度网盘分享页（中文）；Reddit、Discourse、Stack Exchange、GitHub Releases、Notion/Google Drive 分享页（英文）。

用途：为学生论坛 Samryetha 新开发的「文件服务」板块提供信息架构与交互设计的参考。

调研时间：2026-10-07。

---

## 0. 调研方法与「已验证 / 未能验证」的判定标准

本报告严格区分两类内容：

- **【实抓】**＝本次调研中成功用程序（Python urllib，绕过系统代理）直接抓取到页面正文，并从中读出的原文事实。每条都附来源 URL。
- **【推断】**＝本报告作者的判断、归纳或建议，不是被观察到的平台事实。
- **【未能验证】**＝本环境无法取得该平台页面正文，因而不对相关功能作任何断言。

**环境限制（重要，直接影响覆盖度）**：本机网络可正常访问部分站点，但下列站点在本次调研中被拦截或不可达，因此**其功能细节全部标记为「未能验证」**：

| 平台 | 目标 URL | 观测到的结果 |
| --- | --- | --- |
| Reddit（官方帮助中心） | https://support.reddithelp.com/hc/en-us/articles/15484260038420 | HTTP 403，返回 Cloudflare 挑战页（"Just a moment..."） |
| Reddit（社区页与 JSON API） | https://www.reddit.com/r/modhelp/wiki/index.json | 连接超时（WinError 10060） |
| NGA | https://bbs.nga.cn/ 、https://ngabbs.com/read.php?tid=43525899 | HTTP 403 |
| 知乎 | https://www.zhihu.com/question/19550224 、https://zhuanlan.zhihu.com/p/110786584 | HTTP 403 |
| V2EX | https://www.v2ex.com/api/topics/latest.json 、https://www.v2ex.com/faq | 连接超时（WinError 10060） |
| CSDN 资源下载 | https://download.csdn.net/ | HTTP 521（源站不可用/盾） |
| 百度贴吧（吧页面本体） | https://tieba.baidu.com/f?kw=大学 | HTTP 403 |
| Stack Exchange | https://stackoverflow.com/help/editing 、https://meta.stackexchange.com/questions/121812 | HTTP 403 |
| Google Drive 帮助 | https://support.google.com/drive/answer/2494822 | 连接超时（WinError 10060） |

另外尝试过的绕行手段（均不可用，记录以免重复投入）：公共阅读代理 `r.jina.ai` 亦连接超时；Bing 搜索页可抓取，但实际返回的结果多为站点首页，信息量不足以支撑功能级事实。

因此本次实际取得正文的平台为：**Discourse（Discourse Meta 官方文档）、GitHub（Releases 文档 + 社区公告）、Notion 帮助中心、豆瓣小组、百度贴吧帮助中心、百度网盘企业版帮助中心、蓝奏云官网**。其余平台见第 1.8 节。

---

## 1. 逐平台观察

### 1.1 Discourse（Discourse Meta 官方文档）

Discourse 是最具参考价值的英文论坛软件样本，其附件体系与「独立文件库」是**两条不同的技术路线**，这一点对本项目最关键。

**（1）列表形态**

Discourse 没有「文件列表」这个概念。它的列表是**话题列表（topic list）**，附件只作为帖子内容的一部分存在。官方原文明确写道："There is no file management interface provided in Discourse."（Discourse 不提供文件管理界面）

列表本身支持一套**过滤查询语言**（route `/filter`），可用的过滤维度包括（【实抓】，来源见来源清单第 3 条）：

- 分类：`category:bug`（含子分类）、`=category:bug`（不含子分类）、`category:documentation:admins`（子分类）、逗号表示「或」、`-category:` 表示排除；
- 标签：`tag:bug+feature`（同时具备）、`tag:bug,feature`（任一）、`-tag:`（排除）、`tag_group:名称`（按标签组）；
- 状态：`status:open` / `closed` / `public` / `archived` / `unlisted` / `listed` / `deleted`；
- 个人维度：`in:pinned`（置顶）、`in:watching`（含 muted/normal/tracking/watching_first_post）、`in:bookmarked`（我收藏的）；
- 作者：`created-by:@USERNAME`；
- 数量：`posts-min:` / `posts-max:` / `posters-min:` / `posters-max:` / `likes-min:` / `likes-max:` / `likes-op-min:` / `likes-op-max:` / `views-min:` / `views-max:`；
- 时间：`activity-before/after:`、`created-before/after:`、`latest-post-before/after:`；
- 排序：`order:activity` / `latest-post` / `created` / `views` / `likes` / `likes-op` / `posters` / `category` / `title`，加 `-asc` 可反向。

官方在该主题中自述：这个功能仍是实验性的，"The user experience needs improvement and we are currently working on adding some sort of inline autocomplete or even an interface to allow users to build out a topics filtering query language without having to remember all the filters by heart."（用户体验仍需改进，正在做行内自动补全，甚至计划提供一个不用背过滤器的可视化构建界面）

**（2）分类与标签体系**

Discourse 的官方立场是**分类不宜多、深度限定两级，横向维度交给标签**。Discourse 社区有实践者明确写道："That is one reason why Discourse supports only two level categories and using tags are guided so often."（这正是 Discourse 只支持两级分类、并经常引导使用标签的原因之一）

标签的管理参数（【实抓】）：

- `max_tags_per_topic`（每主题最大标签数）、`max_tag_length`（标签最大长度）
- `create_tag_allowed_groups`（允许创建标签的组）、`tag_topic_allowed_groups`（允许给主题打标签的组）、`edit_tags_allowed_groups`（允许编辑标签的组）
- `max_tag_search_results`、`tags_sort_alphabetically`

其他要点：标签**首次使用即自动创建**，无需预先建库；分类可配置「本分类允许使用哪些标签」；站点有 `/tags` 页列出全部标签及各自使用次数；多标签交集有专用路由 `/tags/intersection/TAG1/TAG2`；支持**标签组（tag group）**，可按标签组过滤，标签组也能限制「哪类用户可以使用某组标签」；可用 watched words 实现内容命中关键词自动打标签（但**不追溯已有主题**）。

一处值得注意的历史问题：2023 年有管理员报告 `tags:` 过滤**不支持中文等非 ASCII 标签**（查不存在的英文标签返回空，查不存在的中文标签却返回全部主题）。

另有一条官方明确定位：**标签作用于主题，不作用于帖子**（"Tags apply to topics, not to individual posts within topics"），且帖子里的 `#标签名` 只是链接、不等于打标签。

**（3）搜索**

Discourse 的搜索过滤器比过滤语言更完整（【实抓】，来源见来源清单第 9 条），与本项目高度相关的有：

- **按文件类型检索**：`filetypes:pdf,docx`（"Returns posts with uploads of ext1, ext2, or ext3 file extensions"），别名 `filetype:`；
- 范围：`in:title`、`in:first`（仅首帖）、`in:seen` / `in:unseen`（登录用户）、`in:bookmarks`、`in:personal`（私信）、`#slug`（分类或标签）；
- 内容类型：`with:images`（含图片的帖子）；
- 用户与组：`@username`、`created:@username`、`group:组名`、`badge:徽章`；
- 计数：`min_posts:X`；
- 时间：`before:` / `after:` 支持 `YYYY-MM-DD`、`YYYY-MM`、`YYYY`、`N` 天、星期名、月份名、`yesterday` 等多种形态；
- 状态：`status:open/closed/archived/noreplies/single_user/public`，插件还可提供 `status:solved` / `status:unsolved`；
- 排序：`order:latest` / `oldest` / `latest_topic` / `oldest_topic` / `views` / `likes` / `read`（登录用户）/ `votes`；
- 别名：`t`=`in:title`、`f`=`in:first`、`l`=`order:latest`、`r`=`order:read`；
- 官方文档说明这些是**高级搜索表单之外**的过滤器（"This topic aims to list other filters that aren't available in the advanced search form"），即 Discourse 同时存在**图形化的高级搜索面板**和**手写过滤器**两套入口。

**（4）详情页与上传物**

上传物（图片、视频、音频、文本、PDF、ZIP 等）挂在帖子上，帖子即详情页。附件在帖子正文中以内联链接或预览形式出现，**没有独立的文件详情页**（无独立元信息区、无版本历史、无相关推荐）。删除上传物的唯一方式是删除或编辑包含它的帖子；**孤立文件在 48 小时宽限期后自动删除**。

**（5）上传/贡献流程与限制**

- 允许类型：默认仅图片（jpg、jpeg、png、gif、heic、heif、webp、avif、svg）；管理员可在 `authorized extensions` 与 `authorized extensions for staff` 中追加 `.pdf`、`.docx`、`.mp3` 等；**把扩展名列表清空即等于关闭全站上传**。
- 大小：图片默认 10240 kB（10 MB，上限 102400 kB＝100 MB）；非图片附件默认 10240 kB，上限 1024000 kB（约 1000 MB）；**官方托管客户两者上限均为 30 MB**。
- 去重与存储：按**文件内容哈希**存储，同一文件重复上传只保留一份（即使文件名不同）；服务器自建部署存放于 `/var/discourse/shared/standalone/uploads/default/`。
- 大文件：官方建议不要自存，改用 Google Drive、Dropbox 等外部服务，或 YouTube / SoundCloud 托管媒体，然后把链接单独成行粘贴到帖子中，Discourse 会渲染成播放器或摘要预览卡片。

**（6）下载交互与权限可见性**

- 默认状态下，上传物是**可被直连的公开资源**——只要知道 URL，匿名访客也能取到。
- 若要限制，需开启 **Secure Uploads**：前提是使用 S3 且桶策略非公开，且是 **Enterprise 计划**功能（官方原文："This is an advanced feature and support outside of our Enterprise tier will be limited at best"）。开启后，文件在 S3 上为私有 ACL，直接访问 S3 链接返回 403，所有访问改为经 Discourse 的 `/secure-uploads/` 路由 + S3 预签名 URL 中转。
- 访问控制的锚点是「该上传**首次出现的那篇帖子**」（access control post），权限完全跟随这篇帖子的可见性。官方警告不要把 `/secure-uploads/` 链接在帖子与主题之间搬来搬去，因为不同用户权限不同。
- 开启 `login required`（全站需登录）后，**所有上传物都被标记为 secure**，匿名用户访问 `/secure-uploads/` 一律 404。
- 社区讨论中有人明确指出一个易被忽视的风险：**对于完全私有的自建论坛，如果不用 S3 + Secure Uploads，帖子正文受登录保护、但附件仍可能被知道 URL 的人匿名取走**（"I wouldn't be surprised if a lot of folks running private forums don't realize that all of their post attachments are publicly accessible (if you know the URL)"）。这一条对本项目有直接的警示意义。

**（7）板块与分类的可见性模型**

分类级权限只有三档，按组授予（【实抓】，来源见来源清单第 6 条）：

- **See**（可见该分类及其内容）、**Reply**（可在已有主题中回复）、**Create**（可在该分类中新建主题）。
- 默认组有 `everyone`、`admins`、`staff`、`moderators` 以及各信任等级组。
- **`everyone` 组包含匿名访客；`trust_level_0` 组只包含已登录用户**——所以「禁止匿名访问」的做法是把 `everyone` 换成 `trust_level_0`。
- 未授予 See 的分类**不会出现在该用户的界面上**；有权限的成员会在分类徽章旁看到**锁形图标**。
- 管理员对所有分类恒有全部权限；**版主没有默认权限，必须显式加入权限规则**。
- 可以只给「See」而不给「Reply/Create」，这样分类可见但不能发帖；这类用户仍能收到通知和摘要邮件。

**（8）准入与审核（站点级三档）**

官方给出的封闭社区三档设置（来源见来源清单第 7 条）：

- `login required`：全站内容必须登录才能看见；
- `must approve users`：任何人可注册，但**新账号需工作人员审核通过后才生效**；
- `invite only`：不显示注册入口，**必须有邀请链接才能注册**（邀请链接可通过邮件或站外分发）。

**（9）举报与内容治理**

Discourse 的 flag（举报）机制（来源见来源清单第 5 条）：

- 单条举报进入复核流程，可由工作人员执行：同意举报、驳回举报、隐藏该帖（并自动向作者发私信鼓励修改）、暂停用户；
- **达到社区举报阈值后帖子自动隐藏**：作者会看到「被社区举报」的提示，其他人看到「该帖暂时隐藏」；
- 默认开启 `high_trust_flaggers_auto_hide_posts` 时，**信任等级 3 及以上的用户单人发一条 spam 举报，即可自动隐藏信任等级 0 用户的帖子**；
- 作者**不修改**被隐藏的帖子，则帖子会一直隐藏；默认开启 `delete_old_hidden_posts` 时，**隐藏满 30 天自动删除**；
- 帖子有一个反直觉的行为：**即使有 50 个赞，只要举报数达到阈值，帖子依然会被自动隐藏**。

**（10）下载计数**

Discourse 对以「附件」形式呈现的上传链接（渲染为 `.attachment` 类的链接）走**独立的下载统计机制**，与普通话题内链的点击计数不同路。该机制曾有缺陷：站点为防止用户看到自己无权访问的私密话题的链接点击数而加的 SQL 过滤规则，"accidentally caught file uploads in the crossfire"（误伤了文件上传），导致附件下载计数不显示（来源见来源清单第 11 条，属缺陷报告主题，非稳定功能说明）。

**（11）课程/教学社群的用法**

Discourse 社区在「用 Discourse 做课程社群」的主题中给出的标准做法是：**用「组 + 分类安全设置」来划分免费区与付费区**——把「只有买了课程的人能看的板块」实现为一个仅该组可见的分类，并可用批量邀请把成员导入指定组。同一论坛的权限文档也把这种「组 → 私有分类」的配置作为主要示例（示例场景：实习生组可 See + Reply 某个 staff 分类，但不能 Create 新主题）。

**（12）评分与收藏**

- **没有星级评分**。只有点赞（like）。过滤语言与搜索都可按 likes 排序（`likes-min:`、`order:likes`）。
- 有「收藏」概念：过滤语言有 `in:bookmarked`，搜索有 `in:bookmarks`。
- 官方插件生态还提供「Solved（已解决）」——文档提到 `status:solved` / `status:unsolved` 是插件提供的过滤器。

---

### 1.2 GitHub Releases（作为「版本化资源列表」对照）

GitHub Releases 是「一个资源、多个版本、附件挂在版本下」的典型模型，对本项目的「复习提纲第 N 版」类需求有直接参照价值。

- **模型**：Release 基于 Git tag；tag 日期与 release 日期可以不同。任何人只要有读权限就能查看与比较 release，**只有有写权限的人才能管理 release**。
- **列表形态**：仓库的 Releases 页与 Tags 页是两个并列的 Tab；页面按**时间倒序**列出 release 名或 tag 版本号，可用于查看仓库的历史沿革。
- **单个 release 的组成**：tag、Release title、release notes（可手写、可按默认模板自动生成、可自定义模板）、对贡献者的 `@` 提及、作为二进制的附件文件（拖拽或手动选择）、`This is a pre-release` 标记、`Set as latest release` 标记（不勾选则按语义化版本自动判定 latest）。
- **附带的自动内容**：GitHub 会自动为每个 release 附上该 tag 处仓库内容的 **zip 与 tarball 下载链接**。
- **附件元信息**：查看 release 详情时，**每个附件的创建日期显示在附件旁边**。
- **配额**：**单个 release 最多 1000 个附件；单个文件必须小于 2 GiB；release 总大小与带宽无限制。**
- **发布状态流转**：可先 `Save draft`（存为草稿）再 `Publish release`；若仓库启用了 immutable releases，官方**建议先建草稿、把所有附件都传完再发布**，因为发布后附件不可增删改。启用 immutable releases 后，release 存在期间 tag 也不能移动或删除，但标题、release notes、pre-release/latest 标记仍可修改。
- **下载量**：每个附件的下载次数原先只能通过 API 取得，2026 年的一次改进后**在 Releases 界面中直接显示**，但**仅对有写权限的用户可见**；且**不包含 tarball 与 zipball 的下载数**（API 不返回这两项）。社区中有反馈认为下载量对所有访客都有用（便于判断哪个附件是主流）。
- **发布页导航**：同一次改进为 release 页加了**侧边目录（TOC）**，便于在很长的 release notes 中跳转，同时调整了 release 元信息的摆放位置以统一版式。
- **社区反馈中暴露的设计缺口**（可直接作为本项目的经验教训）：
  - release 列表**无法搜索**，翻页查找「最近的稳定版」很痛苦；
  - "Find a release" 搜索框实际只按 release 标题与描述做纯文本匹配，结果常不相关，用户希望有**专搜 release 标题/ tag 名**的字段，并希望能**过滤掉 pre-release**；
  - 仓库存在多产品线发布时（同一仓库既有主产品又有 helm chart、CLI），发布列表的导航难以区分。
- **权限与可见性**：由仓库可见性（public / private）统一控制，私有仓库的 release 对无权限者不可见（来源见来源清单第 16 条）。

---

### 1.3 Notion（作为「在线预览 + 权限可见性」对照）

Notion 的价值在于它把「分享」拆成了**按人授权 / 链接授权 / 公开发布**三条独立路径，并且权限语义定义得非常细。

**（1）三种对外方式**

- **邀请具体的人**：在 Share 菜单输入姓名或邮箱，逐个分配权限等级；
- **"Anyone on the web with link"**：任何拿到链接的人都能访问，**即使不是工作区成员、甚至不是 Notion 用户**；**可以为链接设置过期时间**（"Link expires"）；
- **Publish（发布为 Notion Sites）**：生成公开站点页，可**开关搜索引擎收录**（付费计划还能自定义 SEO 标题与描述）；可认领一个 `notion.site` 域名。

**（2）权限等级（原文定义）**

| 等级 | 含义 |
| --- | --- |
| Full access | 可编辑页面全部内容，并可把页面分享给任何人 |
| Can edit | 可编辑内容，但**不能**分享给别人 |
| Can edit content | 仅数据库页面；可创建/编辑库内页面及其属性值，但**不能**改动数据库结构、属性、视图、排序、筛选 |
| Can create | 仅数据库页面，Business 与 Enterprise 计划可用；**可以新建条目，但看不到也改不了别人未单独授权的条目**（官方给的用途举例就是收集 IT 工单、任务请求、表单回复这类投稿） |
| Can comment | 只能评论，不能编辑或分享 |
| Can view | 只能阅读，不能评论、编辑、分享 |

**（3）权限继承与冲突规则**

- 子页面**继承**父页面的权限，需要单独进子页面修改；
- 把页面移到侧边栏的 Private 区域会**移除其他人的访问权限**，但该覆盖**只作用于父页面**，子页面已授予的权限不变；
- **冲突时取最宽权限**（"Notion respects the broadest level of access given to a user"）；
- 数据库可按 person 属性或 created by 属性配置**页面级访问规则**，规则会作用于该数据库的所有视图及其链接视图；
- 团队空间（teamspace）可设默认权限，作用于空间内每个新页面。

**（4）发布语义与隐私提醒**

- **发布一个页面会连带发布它的全部子页面**（可对子页面单独限权来隐藏）；
- 可开启 **"Duplicate as template"**，允许访客把站点复制成自己工作区里的 Notion 页面；
- 官方明确提示：发布页面或设为「Anyone on the web with link」时，**网页元数据会包含参与该页面的 Notion 用户的姓名、头像与邮箱地址**；
- 站点被取消发布后，若该页的通用访问设置仍是「Anyone on the web with link」，**持链接的人仍能访问**，需要额外关闭。
- **不支持给页面设密码**：官方 FAQ 原文回答是 "Unfortunately, not at the moment."，替代方案是逐个邀请。
- Enterprise 工作区所有者可全局关闭公开发布与公开链接。

---

### 1.4 豆瓣小组

豆瓣小组是本次调研中少数能取得真实页面正文的中文社区。抓取页：小组发现页与一篇小组使用手册帖。

**已验证的观察**：

- **小组发现页（讨论精选）**：每个条目展示**标题 + 正文摘要片段 + 「来自 XX 小组」+ 日期 + 喜欢数**；页面右侧有「小组日榜」，条目为**组名 + 成员数 + 加入小组按钮**；底部为分页（观察到页码合计 673 页）。
- **小组分类导航**中有：新组、追剧、书影音、人文、闲趣、兴趣、生活、美食、家居、体育运动、宠物、艺术、科技、情感、科学自然、**学习、校园**、ACG、职场、理财。（说明「学习」「校园」是豆瓣小组的一级发现分类）
- **帖子页**：显示标题、作者昵称、发布时间；正文含图片与视频（页面中出现 `[视频]` 标记）；底部操作为**赞、收藏、转发、回复**（观察到具体计数，例如「赞 5」）；未加入小组的用户在投票区会看到提示「加入小组后即可参加投票」——说明豆瓣小组内置了**小组内投票**功能。
- **一篇社区自制的「小组操作手册」帖**（2018 年发布，现状未验证）记录了当时的机制：开新帖可直接发图，**回复中贴图仅网页版支持**（App 端当时不支持）；删帖需去专门的**「申删楼」**提交帖子地址与原因，由管理员操作；发帖、回复、浏览记录可在「小组 - 管理小组」中查看。

**未能验证**（本次未取得证据，不作断言）：豆瓣小组是否支持非图片类文件附件上传；是否有小组级文件/资料区；资料如何按学期或科目组织；是否有星级评分或积分体系。

---

### 1.5 百度贴吧（帮助中心）

贴吧的社区本体页面被拦截（403），但其**官方帮助中心可正常抓取**，其中「精品区分类」的说明对本项目的分类体系设计有直接参考价值。

**精品区分类机制（【实抓】原文要点）**：

- **每个贴吧最多可以设置 8 个不同的分类**；
- 分类名在**精品区页面的分类导航栏**中显示，顺序即排列顺序，可通过上下箭头调整，调整后需点确认；
- 分类名**可以删除或修改**；删除一个分类名时，该分类下的所有精品贴**将不再属于任何分类**；修改分类名时，原分类下的所有精品贴**整体迁移到新名称下**；
- 前台对**每一篇精品贴单独设置分类**：点帖子上的「设为精品贴」，在下拉菜单里选分类；已设定分类的帖子可重新选择；
- **未设置任何分类的精品贴，默认归入「全部分类」**。

**帮助中心结构反映出的功能面（【实抓】栏目名）**：

- 吧务相关：申请吧务、违规/下任、吧内管理、吧务权责；
- 贴吧会员：开通会员、会员特权、**T豆特权**（T豆为贴吧虚拟积分/货币）；
- 常用功能：发帖/删帖、**图片/视频**、创建贴吧、搜索相关、贴吧合并、其他问题；
- **投诉举报：举报须知、如何举报、处理时效、结果查询**（四步齐全，含时效承诺与结果可查）；
- 用户相关：个人设置、**关注收藏**、**等级特权**；
- 封禁恢复：账号解封、删除恢复；
- 吧主规范按类型分册：**明星类、校园类、地区类**。

**注意（避免过度推断）**：help.baidu.com 的贴吧栏目下，与上传相关的条目只看到「如何上传视频到贴吧？」「吧主/图片小编可进行哪些图片操作？」「吧主/视频小编可以进行哪些视频操作？」，**没有检索到「通用文件/附件上传」的条目**。这只能说明**帮助中心未列出该功能**，不能据此推断贴吧不支持通用文件附件。贴吧的附件大小限制、下载交互、积分门槛、需回复可见等机制，本次**全部未能验证**。

**一处可用的弱信号（明确标注：仅搜索结果标题层面，未验证）**：搜索引擎返回过一条标题为「**校园类贴吧吧主规范**」的条目，与帮助中心栏目一致，可确认校园类贴吧是被官方单列管理的一类板块。

---

### 1.6 百度网盘（企业版帮助中心）——作为下载体验对照

百度网盘的分享面板交互可从其**官方帮助中心**（百度用户服务中心，百度网盘企业版）取得原文描述，这是本次中文侧信息密度最高的一段。

**「文件传输（分享）」流程（【实抓】原文）**：

选中要分享的文件，点击【分享】即生成分享面板，面板分三个区域：

1. **极速下载券选项区**：可选择是否使用极速下载券、以及使用多少张（**一张对应一名用户可使用**）；若使用，文件接收方即可极速下载；面板中会显示剩余券数。
2. **备注**：可为文件填写备注，**不能超过 16 个字**。
3. **文件访问的相关设置**：
   - **有效期**：可选 **1 天、7 天、永久有效**；
   - **提取码**：支持**自动生成**，也支持**手动填写 4 位字母或数字**；
   - **访问人数**：设置后可**限制查看文件的人员数量**；
   - **是否允许文件接收方下载/保存文件**（可只让看、不让存）。

最后点【生成链接】复制信息进行分享。

**帮助中心栏目反映出的能力面（【实抓】栏目名，未验证具体交互）**：操作日志、空间使用查看与清理、在线编辑、组织架构、工具中心、企业号、**文件标签设置**、最近、PDF 转 word、员工管理、获客分享链接、空间分配、**智能分类**、**智能搜索**、回收站、管理员手册、**文件权限设置&管理**、创建文件、文件传输、**我的分享**、本地同步、**专属申诉通道**。另有常见问题分组：登录相关、添加员工相关、文件操作相关、上传下载速度相关、**极速下载券相关**。

**未能验证**：个人版百度网盘分享页的实际界面（文件列表形式、在线预览、登录/客户端限制、下载确认与风险提示、举报入口）；本次亦未取得任何真实分享链接进行页面级观察。

---

### 1.7 蓝奏云（官网）

蓝奏云首页可抓取，其自我描述恰好构成了一个「下载体验」的对照组。

**【实抓】首页原文要点**：

- 主打卖点：**下载不限速度、不限流量、不限次数、免登录下载**；
- 使用三步：「1，免费注册 / 2，上传文件 / 3，分享链接」（标题为「会分享，有收获」）；
- 「云存储」：拥有**无限制的总存储空间**，支持个性化定制；
- 「云加速」：真正的云加速下载，对下载不做任何限制；
- 「体验佳」：下载清晰，不干扰用户，节省用户时间；
- 「分享文件地址永久有效，不必担心过期而经常更换链接」；
- 「下载无任何限制，无验证码等影响用户体验限制」；
- 页脚合规章节：**「使用蓝奏必读(声明)」「禁止上传侵权影视等作品的通知」「互联网不良有害信息举报」「净网2017举报通道」「网站自律公约」**。

**【推断】**蓝奏云把「免登录、无验证码、地址永久有效」当作与主流网盘（需登录、有提取码、链接会过期、限速）差异化的卖点——这从侧面说明「提取码 + 限速 + 需登录」是中文网盘分享页的主流形态，但**本报告未对百度网盘个人版的分享页做页面级验证，故此判断仅为推断**。

**未能验证**：蓝奏云分享页的实际字段布局（文件名、大小、上传时间、下载按钮、密码框位置）。

---

### 1.8 未能验证的平台汇总

以下平台在本次环境中**完全没有取得页面正文**，因此第 2 节的对比表与第 3 节的建议中**不包含**任何关于它们具体功能的事实性陈述：

- **Reddit（含 r/ 社区 wiki 与附件）**：官方帮助中心被 Cloudflare 拦截（403），社区页与 JSON API 连接超时。**Reddit 的 wiki 权限模型、附件上传策略、社区分类与筛选机制均未能验证。**
- **Stack Exchange**：帮助页与元站均 403。**其图片上传的大小限制、附件策略、投票/徽章体系的具体数值均未能验证。**
- **NGA**：站点 403。**其附件上传、下载门槛、积分/权限体系均未能验证。**
- **知乎**：问题页与专栏页 403。**其文章附件功能、专栏/话题组织方式、审核机制均未能验证。**
- **V2EX**：站点与 API 均网络超时。**其是否支持附件上传、节点/标签体系、收藏与感谢机制均未能验证。**
- **CSDN 资源下载**：download.csdn.net 返回 521。**其资源列表形态、积分定价、评分与评论、审核机制均未能验证。**
- **Google Drive 分享页**：帮助页与相关域名连接超时。**其共享权限层级（查看者/评论者/编辑器）、链接共享与到期设置均未能验证。**

**弱信号（明确标注：仅搜索引擎返回的标题/摘要层面，未经页面验证，仅作线索不宜作为设计依据）**：

- V2EX：搜索结果中出现标题「v2ex 不能上传图片呀，只能借助第三方图床」以及「攻略：教你如何在 V2EX 发图片/插链接/插代码/插视频（第二版）」——提示 V2EX 可能不自建图片托管、改用外链图床。
- CSDN：搜索结果中出现标题「CSDN新版下载频道介绍之四——资源评分评论及积分日志功能改进」——提示 CSDN 下载频道具备资源评分、评论与积分日志。
- NGA：搜索结果中出现标题「专楼排版技巧：个人经验分享」，其页面摘要中含「图片相关[上传]」——提示 NGA 帖子支持图片上传。

以上三条均**不足以支撑功能级结论**，本报告不据此提出设计建议。

---

## 2. 横向对比表

说明：表格中「未能验证」表示本次未取得该平台页面正文；「—」表示该平台不使用该概念。所有已填内容均对应第 1 节的【实抓】事实。

| 维度 | Discourse | GitHub Releases | Notion | 豆瓣小组 | 百度贴吧 | 百度网盘（企业版） | 蓝奏云 | Reddit / Stack Exchange / NGA / 知乎 / V2EX / CSDN / Google Drive |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **1 列表形态** | 话题列表（非文件列表）；支持查询语法过滤；无文件管理界面 | 按时间倒序的 release 列表，Releases / Tags 两个 Tab | 页面/数据库视图；公开站点可切换视图 | 卡片式信息流：标题+摘要+来自小组+日期+喜欢数；有小组日榜 | 精品区有分类导航栏 + 精品贴列表 | 分享面板（非列表）；文件列表界面未验证 | 未验证 | 全部未能验证 |
| **字段（每行/每项）** | 标题、分类、标签、作者、回复数、浏览量、点赞、最后活动时间 | release 名/tag、发布时间、pre-release 与 latest 标记、**每个附件的创建日期**、附件下载量（需写权限） | 页面属性（数据库可自定义） | 标题、摘要、来源小组、日期、喜欢数 | 帖子标题（精品贴带分类归属） | 分享链接有效期、提取码、访问人数上限、备注（≤16 字） | 未验证 | 全部未能验证 |
| **封面缩略图** | 有（图片自动生成多种尺寸） | 无 | 有（页面图标/封面） | 有（正文含图/视频） | 未验证 | 未验证 | 未验证 | 未能验证 |
| **2 分类与标签** | 分类**仅两级**；标签承担横向维度；分类可限制可用标签；有标签组；标签即用即建 | tag 即版本号；无分类概念 | 页面层级 + 数据库视图；无标签体系（属性替代） | 小组分类（含「学习」「校园」） | **精品区分类，每吧最多 8 个**，可排序/改名/删除；未分类归「全部分类」 | 文件标签（栏目名可见，细节未验证） | 未验证 | 未能验证 |
| **合集/系列** | 用分类 + 标签组近似实现 | release 系列（同一仓库多个 tag） | 数据库 + 模板 | 未验证 | 精品区即「精选合集」 | 未验证 | 未验证 | 未能验证 |
| **3 筛选与排序** | 过滤语言：`category:` `tag:` `status:` `in:` `created-by:` `posts/likes/views -min/-max`、时间区间；`order:` 支持 activity/latest-post/created/views/likes/posters/category/title | 仅时间倒序；**列表不可搜索**（社区痛点）；"Find a release" 只搜标题与描述 | 数据库视图自带 sort/filter | 未验证 | 精品区按分类导航 | 未验证 | 未验证 | 未能验证 |
| **高级筛选面板** | **有**（官方提到 advanced search form）＋ 手写过滤器双轨；/filter 页有输入框，官方承认 UX 待改进、计划加自动补全 | 无 | 数据库视图筛选 | 未验证 | 无（分类导航代替） | 无 | 未验证 | 未能验证 |
| **4 搜索** | 全文搜索 + 丰富过滤器（`in:title` `in:first` `with:images` `@user` `group:` `min_posts` `before/after` 多形态 `status:` `order:` 及别名）；**可按 `filetypes:pdf` 按文件扩展名检索**；支持 `#分类/标签` 跳转 | 有 "Find a release" 搜索框，实际只匹配标题与描述，社区反馈结果常不相关；希望有专搜 tag 名与过滤 pre-release 的能力 | 搜索页面内容 | 未验证 | 帮助中心有「搜索相关」栏目，细节未验证 | 栏目名含「智能搜索」，细节未验证 | 未验证 | 未能验证 |
| **5 详情页元信息** | 帖子即详情页；附件无独立元信息区 | tag、标题、release notes、贡献者 @、附件列表（含创建日期与下载量）、pre-release/latest 标记 | 页面属性区 | 标题、作者、时间、正文；操作区为赞/收藏/转发/回复 | 未验证 | 未验证 | 未验证 | 未能验证 |
| **在线预览** | 图片多尺寸；外链媒体渲染为播放器/摘要卡片 | 无预览（二进制附件直下） | 完整富文本/数据库/嵌入渲染，公开站点可被嵌入他站 | 图片与视频内嵌 | 图片/视频（帮助栏目可见） | 栏目名含「在线编辑」「PDF 转 word」 | 未验证 | 未能验证 |
| **版本历史** | **无**（帖子编辑历史机制未在本次验证范围内） | **有**，以 tag/release 为核心 | 有页面历史 | 未验证 | 未验证 | 未验证 | 未验证 | 未能验证 |
| **评论区/讨论区** | 帖子本身就是讨论区 | **没有**（讨论在 Issues/Discussions 另处） | 页面评论（需登录 Notion） | 帖子下为回复/转发/点赞/收藏 | 帖子回复 | 未验证 | 未验证 | 未能验证 |
| **6 下载交互** | 默认**可匿名直连**；开启 Secure Uploads 后经 `/secure-uploads/` 鉴权路由 + 预签名 URL；全站 login required 时匿名一律 404 | 直接下载，公开仓库无需登录；单个文件 < 2 GiB，单 release ≤ 1000 附件 | 取决于分享方式：链接可见 / 需邀请 / 需登录评论 | 不适用（无文件下载） | 未能验证 | **有效期 1 天/7 天/永久；4 位提取码；访问人数上限；可禁止接收方下载/保存；极速下载券按人消耗** | **免登录下载、无验证码、地址永久有效** | 未能验证 |
| **下载前确认与风险提示** | 未见专门的风险提示机制 | release notes 里自行说明；pre-release 有明确标记 | 无 | 无 | 未验证 | 未验证 | 首页有「使用蓝奏必读」与侵权举报通道 | 未能验证 |
| **7 收藏与评分** | 点赞 + 收藏（`in:bookmarked`）；**无星级**；可按 likes 排序过滤 | 无评分；有 star（仓库级）；下载量作为热度信号 | 无评分；有收藏/收藏夹 | **赞、收藏、转发**；未验证星级 | 栏目含「关注收藏」「T豆特权」 | 未验证 | 未验证 | 未能验证 |
| **8 权限与可见性** | 分类级 **See / Reply / Create** 三档，按组授予；`everyone` 含匿名，`trust_level_0` 仅登录用户；无 See 权限则分类不可见（有权限者见锁形图标）；站点级三档：login required / must approve users / invite only | 跟随仓库可见性（public / private）；管理 release 需写权限；immutable releases 发布后附件不可改 | **邀请个人（6 级权限）／持链接可见（可设过期）／公开发布（可开关搜索引擎收录）**；子页面继承父页面；冲突取最宽权限；**不支持密码保护** | 小组即权限边界（是否组员）；未验证组内细分权限 | 未验证（有吧务权责与封禁恢复栏目） | 有效期 + 提取码 + 访问人数上限 + 是否允许下载保存 | 未验证 | 未能验证 |
| **审核与举报** | flag 体系：单举报可 agree/disagree/隐藏/暂停用户；达阈值自动隐藏；TL3+ 单条 spam 举报可自动隐藏 TL0 帖子；隐藏 30 天未编辑自动删除；**高赞不能豁免** | 无内容审核（代码协作平台） | Enterprise 可全局禁用公开分享 | 有管理员「申删楼」代删机制（2018 年观察） | **投诉举报四步齐全：举报须知 / 如何举报 / 处理时效 / 结果查询** | 有「专属申诉通道」栏目 | 有不良信息举报通道与侵权作品通知 | 未能验证 |
| **9 上传/贡献流程** | 编辑器内上传按钮（图标随允许类型变化）；允许扩展名由 `authorized extensions` 控制；**按内容哈希去重**；无独立上传表单、无审核状态流转 | 建 tag → 填标题/notes → 拖拽附件 → 标记 pre-release/latest → 存草稿或发布；发布后（immutable）不可增删附件 | 页面内直接编辑；公开站点可开「Duplicate as template」让他人复用 | 开新帖发图；回复贴图仅网页版（2018 年） | 精品贴由吧主设分类；未验证上传附件流程 | 分享面板三项设置（券/备注/访问设置） | 注册 → 上传文件 → 分享链接 | 未能验证 |
| **格式与大小限制** | 默认仅图片；加装扩展名后可传 pdf/docx/mp3 等；图片默认 10 MB（上限 100 MB），非图片默认 10 MB（上限约 1000 MB）；**官方托管客户两者上限 30 MB** | 单文件 < 2 GiB；单 release ≤ 1000 附件；总量与带宽不限 | 未在本次文档中取得限制（未验证） | 建议大文件用外链网盘，贴链接自动生成预览卡片 | 未验证 | 未验证 | 未验证 | 未能验证 |
| **贡献者署名** | 帖子作者即署名；标签/搜索可按 `@username` `created:@username` 过滤 | release notes 中 `@` 提及贡献者；自动生成 release notes 可识别贡献者 | 页面元数据含贡献者姓名/头像/邮箱（官方隐私提示） | 帖子显示作者昵称 | 帖子显示作者 | 未验证（备注 ≤16 字） | 未验证 | 未能验证 |
| **10 学生场景特化** | 官方社区给出「组 + 私有分类」划分课程的方案；未见专门的「新生专区」概念 | 无 | 官方给的「Can create」用途举例即「收集投稿而不让投稿者互相看见」 | 发现页一级分类含**「学习」「校园」** | 吧主规范按**明星类/校园类/地区类**分册；精品区分类 ≤8 个 | 未验证 | 未验证 | 未能验证 |
| **按学期/科目组织的证据** | **未能验证**（本次未取得任何平台按学期组织资料的证据） | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 | 未验证 | 未能验证 |

---

## 3. 对本项目（Samryetha 文件服务）的可借鉴点清单

每条标注「建议采用 / 可选 / 不建议采用」并给出一句话理由。标注【推断】的条目是基于本次观察的归纳，而非某平台明文规则。

### 3.1 建议采用

1. **建议采用：把「文件」建成独立资源实体，而不是挂在帖子里的附件。**
   理由：Discourse 的路线是附件寄生于帖子，官方明说没有文件管理界面、删帖即删附件、孤立文件 48 小时自动清理——对需要长期沉淀的复习资料与提纲而言，这种生命周期完全不可控。

2. **建议采用：附件下载统一走后端鉴权路由，禁止匿名可直连的文件 URL。**
   理由：Discourse 自建站点在未启用 Secure Uploads 时，即使全站要求登录，附件仍可能被知道 URL 的人匿名取走，这是社区明确指出的真实坑；权限锚点应绑定到文件实体自身而非某篇帖子。

3. **建议采用：列表字段里直接展示下载量（并在详情页展示每版本/每文件的下载次数）。**
   理由：GitHub 在 2026 年把原先只在 API 可见的附件下载次数搬进界面，社区反馈这是「最大的收益点」；学生挑资料最依赖「别人都在用」这一信号。

4. **建议采用：排序至少覆盖「最新 / 下载量 / 收藏（点赞）数」三类，并同时提供图形化高级筛选面板与可抄改的筛选条件文本。**
   理由：Discourse 的 `order:latest|views|likes` 与高级搜索表单并行，既能服务只想点选的学生，也能服务愿意记语法的高频用户。

5. **建议采用：分类保持浅层（一级板块 + 二级学科），学期、考试类型、资料类型等会不断增长的维度放到标签上。**
   理由：Discourse 官方立场是分类只支持两级、横向维度交给标签，其社区实践者亦明确指出「分类多且层层嵌套几乎无法维护，且分类并不帮助用户找到相关内容」；而学期是会无限增长的维度，做成分类必然膨胀。

6. **建议采用：标签使用受控词表为主，配合「管理员批量改标签」的能力。**
   理由：Discourse 提供标签组、可限制某分类只能用哪些标签、并可对筛选出的主题批量 Replace/Append/Remove Tags；本次同批资料也显示 Discourse 社区内部对标签滥用有明确抱怨，说明必须留收敛手段。

7. **建议采用：可见性按三档设计——公开 / 登录可见 / 指定分组可见（班级、专业、社团）。**
   理由：Discourse 用 `everyone`（含匿名）与 `trust_level_0`（仅登录用户）区分「匿名可见」与「登录可见」，并把更细的可见性交给「组 → 私有分类」；Notion 也用「邀请个人 / 持链接可见 / 公开发布」三段表达同一件事，两种体系互相印证这条分法足够用。

8. **建议采用：上传表单强制填写结构化字段（所属课程/科目、学期、资料类型、适用对象），而不是只让用户自由打标签。**
   理由：贴吧要求每篇精品贴**手动指定分类**、未指定则归入「全部分类」，说明「入库即定型」是保证后续可检索的关键；单靠自由标签无法保证检索质量。

9. **建议采用：举报与审核做成可见的闭环——举报入口、处理时效、结果可查三件套。**
   理由：贴吧帮助中心把「投诉举报」明确拆成「举报须知 / 如何举报 / 处理时效 / 结果查询」四步；相比只给一个举报按钮，闭环显著降低学生对「举报了没人管」的不信任。

10. **建议采用：对举报采用「多人举报达阈值自动隐藏 + 作者可编辑恢复」的机制，并设自动清理期限。**
    理由：Discourse 的多举报阈值自动隐藏 + 私信提示作者修改 + 隐藏 30 天未编辑自动删除，是一套已在大型论坛运行的成熟折中；同时要注意其反直觉行为——**高赞不能豁免举报阈值**，这条规则需要在产品说明里对学生讲清楚。

11. **建议采用：文件详情页显式给出「谁上传、什么时候传的、多大、什么格式、下载量」以及上传者署名。**
    理由：GitHub 在每个 release 附件旁显示创建日期、并让 release notes 支持 `@` 提及贡献者；Notion 也会把贡献者信息暴露在页面元数据中——署名是贡献者持续贡献的主要动力。

### 3.2 可选

12. **可选：为文件引入「版本」概念，允许多个版本挂在同一资源下并标记最新版与预发布/草稿。**
    理由：GitHub Releases 用 tag 承载版本、每个 release 独立附件、可标记 pre-release 与 latest、并支持草稿后发布——这对「复习提纲第 N 版」极有价值；但若资料以一次性上传为主，成本收益比不高。

13. **可选：引入「资料合集/系列」实体。**
    理由：贴吧精品区与 GitHub 的 tag 系列都说明「把散件聚成一组」是真实需求；但 Discourse 的教训是合集很容易退化成又一层分类，需谨慎设计边界。

14. **可选：大文件允许外链第三方网盘，但必须在详情页明确标注「将跳转至第三方」并给出失效风险提示。**
    理由：Discourse 官方对超大文件即建议改用 Google Drive / Dropbox 并粘贴链接；但这会牺牲站内下载统计，也与本项目「文件服务」的自有定位有张力。

15. **可选：分享链接支持有效期与访问人数上限。**
    理由：百度网盘企业版分享面板提供 1 天 / 7 天 / 永久有效期与访问人数限制；对校内资料而言多数不需要，但对「仅限本次考试前发放」的场景有意义。

16. **可选：收藏夹与「稍后读」入口，并提供「我收藏的资料」筛选。**
    理由：Discourse 有 `in:bookmarked` 过滤与 `in:bookmarks` 搜索，豆瓣帖子也把「收藏」列为帖子一级操作；这是低成本高回报的功能。

17. **可选：提供「可复制为模板/一键另存」的能力，让学生复用他人的资料组织格式。**
    理由：Notion 公开页可开启 "Duplicate as template"；在资料板块里可对应为「以该资料的字段结构新建一份」。

18. **可选：在文件详情页挂评论区。**
    理由：豆瓣帖子的互动（赞/收藏/转发/回复）说明讨论能延长资源寿命；但需与现有的讨论区功能划清边界，避免内容重复。

### 3.3 不建议采用

19. **不建议采用：需回复可见、需积分下载、需消耗虚拟币下载。**
    理由：这类门槛会制造大量无意义回复与刷分行为，与「让学生更快拿到资料」的初衷相反；本次调研中没有任何一个可完整观察的样本证明其正收益（贴吧的 T 豆体系本次未能验证，故不作事实判断，仅从动机上反对）。

20. **不建议采用：强制跳转第三方网盘作为主下载路径。**
    理由：链接会失效、学生需再登录一次、站点完全拿不到下载数据；Discourse 把外链定位为「超大文件的例外手段」而非主路径，是更合理的分工。

21. **不建议采用：星级评分作为资料质量的主要信号。**
    理由：Discourse 与 GitHub 均未对内容采用星级评分；学生资料的评分样本量小、主观性强，点赞与下载量更稳健。

22. **不建议采用：打赏/赞赏功能。**
    理由：校园文件服务引入金钱往来会带来合规、退费与审核负担，收益与风险不成比例。

23. **不建议采用：允许无约束的自由标签作为唯一组织手段。**
    理由：Discourse 社区内部即有明确抱怨——标签在网络语境下被长期滥用、与关键词混淆，普通用户并不清楚该怎么用标签；必须有受控词表与批量整理手段兜底。

24. **不建议采用：三级以上的分类树。**
    理由：Discourse 出于可维护性**主动把分类限制在两级**；本次同批资料中实践者也指出「大量分类与层层子分类几乎无法管理，而且分类并不帮助用户找到相关内容」。

25. **不建议采用：用「提取码/密码」保护链接的分享方式。**
    理由：Notion 官方明确不提供页面密码保护，并以「逐个邀请」作为替代；在校内场景中，登录状态与分组权限本来就是更强的身份边界，再加一道提取码只是增加摩擦而几乎不增加安全性。

### 3.4 学生场景特化的建议（标注为【推断】）

以下为基于本次观察的推断性建议，**本次调研未能取得任何平台按学期/科目组织资料的直接证据**（见第 2 节表格最后一行）：

26. **【推断】建议采用：一级分类按院系/专业，二级分类按课程或科目；学期与学年用标签或筛选器承载。**
    理由：分类深度受限于两级（Discourse 的明确约束），而学期是只增不减的维度，放进分类会让分类树随时间线性膨胀。

27. **【推断】建议采用：「新生专区」做成一个受控分类 + 一组固定标签的组合，而不是一个新的一级板块。**
    理由：贴吧把「精品区分类」限制在每个吧最多 8 个，说明专题聚合的容量应当克制；用「分类 + 固定标签 + 置顶」承载新生攻略，比新开一级板块更易维护。Discourse 的 `in:pinned` 过滤器说明「置顶」在成熟论坛里是一等概念，本项目的「新生专区」应支持置顶条目。

28. **【推断】建议采用：把「复习提纲 / 学习纲要 / 历年真题」等资料类型做成上传时的必选字段，并作为筛选维度之一。**
    理由：贴吧要求精品贴必选分类、未选则归入「全部分类」，说明显式分类比隐式推断可靠；资料类型是最能区分学生检索意图的维度之一。

29. **【推断】建议保留：与学校教务信息（学期、课程代码）的对接位。**
    理由：本次未能验证任何平台的做法，但分类与字段设计一旦定型后迁移成本极高，建议在上传表单中预留课程代码字段。

---

## 4. 来源清单

### 4.1 已成功抓取正文的来源（【实抓】依据）

| 标题 | URL |
| --- | --- |
| Understanding Uploads, Images, and Attachments（Discourse Meta 官方文档） | https://meta.discourse.org/t/understanding-uploads-images-and-attachments/275735 |
| Admin guide to tags in Discourse（Discourse Meta 官方文档） | https://meta.discourse.org/t/admin-guide-to-tags-in-discourse/121041 |
| Topics list filter feature（Discourse Meta，过滤语言完整列表） | https://meta.discourse.org/t/topics-list-filter-feature/263641 |
| Secure Uploads（Discourse Meta 官方文档） | https://meta.discourse.org/t/secure-uploads/140017 |
| Understanding post flags in Discourse（Discourse Meta 官方文档） | https://meta.discourse.org/t/understanding-post-flags-in-discourse/275 |
| How to use category security settings to control access to content（Discourse Meta 官方文档） | https://meta.discourse.org/t/how-to-use-category-security-settings-to-control-access-to-content/87678 |
| Configuring Discourse for a closed or private community（Discourse Meta 官方文档） | https://meta.discourse.org/t/configuring-discourse-for-a-closed-or-private-community/27014 |
| Search tips and tricks（Discourse Meta 官方文档，完整搜索过滤器表） | https://meta.discourse.org/t/search-tips-and-tricks/273328 |
| Documentation: Tips & tricks how to use search?（Discourse Meta） | https://meta.discourse.org/t/documentation-tips-tricks-how-to-use-search/234507 |
| Using Discourse For Course Community（Discourse Meta） | https://meta.discourse.org/t/using-discourse-for-course-community/136421 |
| Link counter missing for attachments（Discourse Meta，附件下载计数缺陷报告） | https://meta.discourse.org/t/link-counter-missing-for-attachments/398453 |
| Cleaning up Uploads and Purging Uploads from S3（Discourse Meta） | https://meta.discourse.org/t/cleaning-up-uploads-and-purging-uploads-from-s3/248343 |
| About releases（GitHub Docs） | https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases |
| Managing releases in a repository（GitHub Docs） | https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository |
| Viewing your repository's releases and tags（GitHub Docs） | https://docs.github.com/en/repositories/releasing-projects-on-github/viewing-your-repositorys-releases-and-tags |
| Release page improvements: new navigation sidebar and per-asset download counts（GitHub Community Discussion #200055） | https://github.com/orgs/community/discussions/200055 |
| Setting repository visibility（GitHub Docs） | https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility |
| Sharing & permissions settings in Notion（Notion Help） | https://www.notion.com/help/sharing-and-permissions |
| Publish a website with Notion Sites（Notion Help） | https://www.notion.com/help/public-pages-and-web-publishing |
| 豆瓣小组 - 讨论精选（含小组分类与小组日榜） | https://www.douban.com/group/explore |
| 给新来豆瓣的道友们简单科普一下豆瓣小组的操作手册（豆瓣小组主题，2018 年） | https://www.douban.com/group/topic/125032668/ |
| 如何设置精品区贴吧分类？（百度用户服务中心 - 贴吧） | https://help.baidu.com/question2?prod_id=3&class=379&id=2714 |
| 百度用户服务中心 - 贴吧（栏目总览） | https://help.baidu.com/question2?prod_id=3 |
| 文件传输（百度用户服务中心 - 百度网盘企业版，分享面板说明） | https://help.baidu.com/question2?prod_id=254&class=785&id=1002089 |
| 百度用户服务中心 - 百度网盘企业版（栏目总览） | https://help.baidu.com/question2?prod_id=254 |
| 蓝奏·云存储官网首页 | https://www.lanzou.com/ |

### 4.2 仅取得搜索结果标题/摘要层面的线索（未验证，不建议作为设计依据）

| 标题 | URL |
| --- | --- |
| v2ex 不能上传图片呀，只能借助第三方图床 - V2EX | https://global.v2ex.co/t/1237791 |
| 攻略：教你如何在 V2EX 发图片/插链接/插代码/插视频（第二版）- V2EX | https://www.v2ex.com/go/v2ex |
| CSDN新版下载频道介绍之四——资源评分评论及积分日志功能改进 | https://blog.csdn.net/iteye_18451/article/details/82129963 |
| 专楼排版技巧：个人经验分享（含「图片相关[上传]」小节）- NGA | https://ngabbs.com/read.php?tid=43525899 |

### 4.3 尝试访问但被拦截 / 不可达（记录以备复核，勿重复投入）

| 平台 | URL | 结果 |
| --- | --- | --- |
| Reddit 帮助中心 | https://support.reddithelp.com/hc/en-us/articles/15484260038420 | HTTP 403（Cloudflare 挑战页） |
| Reddit 社区 wiki JSON | https://www.reddit.com/r/modhelp/wiki/index.json | 连接超时 WinError 10060 |
| NGA | https://bbs.nga.cn/ 、https://ngabbs.com/read.php?tid=43525899 | HTTP 403 |
| 知乎 | https://www.zhihu.com/question/19550224 、https://zhuanlan.zhihu.com/p/110786584 | HTTP 403 |
| V2EX | https://www.v2ex.com/api/topics/latest.json 、https://www.v2ex.com/faq | 连接超时 WinError 10060 |
| CSDN 资源下载 | https://download.csdn.net/ | HTTP 521 |
| 百度贴吧吧页面 | https://tieba.baidu.com/f?kw=大学 | HTTP 403 |
| Stack Exchange | https://stackoverflow.com/help/editing | HTTP 403 |
| Stack Exchange Meta | https://meta.stackexchange.com/questions/121812/how-do-i-upload-an-image | HTTP 403 |
| Google Drive 帮助 | https://support.google.com/drive/answer/2494822 | 连接超时 WinError 10060 |
| 公共阅读代理（绕行尝试） | https://r.jina.ai/https://stackoverflow.com/help/editing 等 | 连接超时 WinError 10060 |
| Google 支持（Notion 帮助替代源） | https://www.notion.com/en-gb/help/share-your-work | 可访问，内容与 4.1 中 Notion 两条重叠 |

---

报告结束。凡本报告未标注【实抓】来源的功能描述，一律为推断或未验证内容，请勿直接作为开发依据。
