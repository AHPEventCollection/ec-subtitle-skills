# MV取源与范围细节

## 完整任务图

```text
官方链接或已有片源
  → 公开前能确认的发行信息、四平台文案和官方封面素材先写入work
  → 检查人工制作的官方字幕
  ├─ 有 → 锁定官方日文正文与顺序，以官方时间为初始轴
  │       → 查询网易云日中歌词并按正文、条数、重复顺序和相对时段匹配
  │       → 高置信中文直接采用，缺失行再翻译
  │       → 对明显早起或晚起逐条修订并记录与官方时间的差异
  │       → 官方字幕候选与人工语义、时间复对
  └─ 无 → 日中歌词正文与角色路由
          → 无外部时间轴输入审计
          → BS-RoFormer分离人声
          → 演唱SOFA／口语Whisper／独立Combined诊断
          → 完整一番或二番校准、按结构推广到其他段、差异局部复核
          → 可编辑候选与人工复对
  → 候选人工确认并明确决定是否直接出片
  → master.srt人工审定
  → 标准ASS与典型场景预览
  → 递增版本压制与机器检查
  → 成品人工抽查
  → 已定稿文案与官方封面自动落到同版本output
  → 交付检查
  → 复制归档
```

目标发布平台不是前置输入。公开前已经能完成的标题、介绍、话题和官方封面必须实际保存为待交付素材，不得只写研究笔记。四平台文案固定写入`work/publish-copy.md`，使用微博、小红书、B站、视频号四个平台章节；YouTube来源复用本支视频缩略图，缺失时补取同视频高清缩略图，不搜索歌曲封面；其他来源按固定优先级查找现成发行封面

到点公开后的优先顺序是正式下载、字幕候选与人工确认、`finish`、完整交付。已经准备好的介绍、话题、歌词来源和官方封面直接复用，不重新研究，不先补过程文档，不在视频、字幕、封面和文字全部落盘前把机器检查报告当成交付结果

## 路由边界

歌曲时间轴采用[完整代表段与结构推广](structure-timing.md)。机器初稿后，由主线程结合歌词和独立音频识别完整主歌至副歌结构，选择完整一番或二番校准，利用4拍/8拍乐句线索与实际音频锚点映射其他段落。歌词不同或旋律略有差别仍需参考，增删结构、不同断句和低置信处单独复核

样片完整性先于识别精度，十几秒准确副歌不能代替完整一轮；推广必须留下逐行参考和差异记录，不能只做代表段而继续独立生成其余时间轴。4拍/8拍是定位线索，实际音频决定起点与速度，字幕不机械吸附拍点。官方字幕仍优先继承；需要校正其时间轴时可使用同一结构方法。所有结果留在候选目录，`master.srt`须经用户审定后由既有入口处理

目标段的局部重叠须保留整曲推广候选并标明冲突行，修订后再审定，不能因一处差异丢掉所有已参考结果；代表段自身重叠则先修正参考，不能继续传播

使用本Skill处理：

- 单首歌曲MV、官方Music Video、动画或真人单曲影像
- 从官方来源取得片源和歌词后制作日中双语字幕并完成交付
- YouTube片源、官方字幕、封面图和发布文案的一体化交付

以下任务改用`concert-subtitle`：

- 多首歌连续演出
- 多首歌、长MC、返场、歌单分段或多人讲话的Live
- 需要逐段审批、全场覆盖审计或歌曲进度图形层的演唱会

单曲MV不建立歌单、MC工作包、演唱会Manifest、全片ASR覆盖审计或歌曲进度条。短片头或片尾对白仍留在本Skill并路由到Whisper或人工处理

## 建立工作区并取得片源

每支MV使用外部项目根的`workspace/mvs/<mv-id>/`，其中包含`source/`、`lyrics/`、`subtitle/`、`work/`、`review/`和`output/`。设置`MV_SUBTITLE_PROJECT_ROOT`指向该项目根，不把媒体或生成物放进Skill仓库

```powershell
$env:MV_SUBTITLE_PROJECT_ROOT = "<mv-project-root>"
$pipeline = "<installed-skill-dir>\scripts\mv_pipeline.py"
$mvDir = Join-Path $env:MV_SUBTITLE_PROJECT_ROOT "workspace\mvs\<mv-id>"

python -B $pipeline init --mv-dir $mvDir
python -B $pipeline download --mv-dir $mvDir --url "<official-url>"
```

`mv_pipeline.py download`固定委托系统共享`ytdlp-global --auth auto`：公开资源先匿名下载，只有全局入口明确判定`authentication-required`时才使用其专用隔离登录态。项目不接受浏览器配置、PO Token提供器或`player_client`参数，不读取默认Chrome`User Data`，不导出Cookie

下载页面来自艺人、厂牌、发行方或作品官方频道。命令保存片源、页面字幕、视频缩略图、标题、艺人和`source/source.md`，禁止播放列表和同名覆盖。这里保存的视频缩略图同时是该YouTube视频的默认交付封面，保持原尺寸、构图与比例转为work/cover-source.jpg。优先SDR；检测到HDR时必须更换SDR片源或另行明确转色方案

用户已有可靠片源时，不重复下载：

```powershell
python -B $pipeline import-source --mv-dir $mvDir --source "<source-video>"
```

导入只复制到本支工作区，不改写用户原文件
