# Samryetha 主站复刻提示词

本提示词提炼自 2026-09-26 的仓库实现（提交 `0d0571b`）。完整尺寸、状态和子站差异见同目录 `README.md`；主题值见 `tokens.css`。

---

请按下面的视觉和交互规范实现界面。目标是复现 Samryetha 主站的设计语言。对已有内容保持其信息结构与业务语义。

整体气质：安静、精确、偏编辑排版、低饱和、内容优先。使用冷灰白或炭黑的整片背景，标题、细分隔线和留白组成版面。列表直接落在页面底上。用表面明度差区分层级；投影集中用于浮层。保留大量纵横留白，控件和辅助信息则相对紧凑。

颜色使用语义变量：浅色 bg #f7f8f8、surface #ffffff、surface-2 #f0f2f3、ink #171a1c、muted #70777b、faint #a7adaf、line #e1e4e5、accent #597585、accent-fill #3d7dbf、accent-soft #e7eef1。深色依次为 #111416、#171b1e、#1e2428、#eceeef、#8f989d、#687075、#292e31、#87a8b7、#4a86cf、#1d2a30。accent 用于链接和细指示条；accent-fill 用于彩色实选状态。主 CTA 为 ink 底、bg 字，暗色时自然成为浅底深字。背景和卡片用纯色。

字体依次使用 "Segoe UI Variable Text", "Segoe UI", -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Helvetica Neue", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif。启用 font-synthesis:none、font-kerning:normal、font-optical-sizing:auto。普通文本主要 400，强调 500/600，品牌与帖子详情标题 700。首页标题 30px/1.05/600、letter-spacing:-.015em；列表标题 17px/1.46/600、正常字距；摘要 13.5px/1.65；meta 12px；帖子正文 15.5px/1.75；帖子详情标题 30px/1.25/700、-.01em。数字使用 tabular-nums。

主版心宽 min(calc(100% - 40px),1160px)。顶栏 sticky、64px 高、不透明页面底、1px 底线。首页为 minmax(0,740px) 1fr 两栏，gap 88px，上下 padding 48px/88px，右侧栏 sticky top 108px。设置/后台为 180px minmax(0,1fr)、gap72px；反馈为 210px minmax(0,1fr)、gap56px。帖子详情为整版心单栏，article padding 34px 0 70px。

列表行使用透明底、上下20px/左右14px padding、10px 圆角、底部1px细线。行宽比正文栏多24px、margin-left:-12px，使 hover 底超出正文边缘。hover 为 accent-soft 32% 与透明混合。列表摘要距标题8px，meta距上9px，meta内部gap8px；计数列右对齐、最小28px。

按钮分层：主按钮高度至少40px、左右18px、13px/600、999px全圆；hover opacity .88、按下 scale(.98)。次按钮为 surface-2底、1px line边框、7px 14px padding、13px/600、全圆；选中为 accent-fill+白字。图标按钮40×40全圆。后台密集按钮30px起、8px圆角、12px/500。危险删除确认使用 #e5484d 实心红；普通危险操作优先文字语义。

输入框1px line描边，surface的76%透明混合底，10–11px圆角，高42–44px。聚焦边框为 accent 62%混line，背景变surface，外圈为3px accent 8%。placeholder为faint。标签11–12px/600，标签与输入间8–9px。回复textarea初始128px，发帖正文230px。

页签13px，gap22px，底部1px线。激活项文字ink，2px雾蓝指示条通过测量真实项目位置和宽度移动；220ms cubic-bezier(.22,.8,.24,1)。首屏直接落位，后续切换才滑动。设置侧栏使用8px圆角的accent-soft滑动背景和2×18px左侧细条。

开关38×22px，thumb14px，top/left3px，on平移16px，轨道accent-fill。徽章20px高、10.5px/600、左右8px、999px圆角、细边框；语义色只占文字、小面积浅底与描边。

下拉框展开后，触发器下两角归零，面板与触发器无缝相接；选项34px高、7px圆角、选中雾蓝文字+5px圆点。支持方向键、Enter、Escape、焦点返回。具体是否搜索取决于组件：主站基础Dropdown没有搜索，@lako/ui的Dropdown可选搜索。

主站对话框：黑色70%遮罩、8px背景模糊；面板380px最大宽，16px圆角，padding22px 22px 18px，surface底、1px line；180ms scale(.96→1)+fade，退出也保留180ms。主站和@lako/ui的Dialog尺寸不同，按目标来源选择。

微交互一般120–180ms，导航/布局移动220–280ms。页面内容先淡出130ms，再延迟125ms淡入130ms；列表标题到帖子标题用共享元素420ms cubic-bezier(.16,1,.3,1)。Save/Follow颜色180ms ease、按钮180ms scale(.985→1)、文字200ms opacity .35→1和blur3→0、相邻按钮220ms位置补间。快速连续操作能从当前帧接管。菜单是少数带轻微过冲的场景：350ms cubic-bezier(.34,1.56,.64,1)，Y -20→0，blur6→0，每项延迟45ms。

响应式按主站实际实现：900px及以下首页/发帖/资料/反馈折单栏，导航和右侧栏隐藏，出现菜单；设置/后台在此仍为160px侧栏+内容、gap40px。640px及以下设置/后台才折单栏；shell改为总共28px侧边预留，顶栏56px，首页标题26px，列表标题16px，输入字号强制16px。480px及以下隐藏顶栏账户姓名。手机搜索收成图标，聚焦后展开并让品牌/账户区域让位。

回复区保留头像树结构：桌面头像40px、内容gap12px、子树缩进46px；手机34/11/38px。深层缩进收窄，连接线按头像实际几何绘制为1px SVG连续路径，圆端点；顶层评论用16px底部留白分组。回复框就地展开。

实现时同时处理默认、hover、focus-visible、pressed、selected、disabled、loading、empty、error状态。保留长文本换行、代码/表格横向滚动、输入触屏字号、键盘焦点及减弱动效。减弱动效时显式恢复菜单可见性，spinner停止但保留加载文案。

验收以真实CSS像素的桌面/手机布局、字体断行、颜色角色、状态过渡和键盘行为为准。不要把全站改成卡片网格、扩大到营销页标题、为所有按钮套相同圆角，或把子站的参数混入主站。若任务是复刻Tasks、Lako或翻译站，应先读取README的子站差异表。
