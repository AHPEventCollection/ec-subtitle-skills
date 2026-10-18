# EC Subtitle Skills

面向日中双语字幕制作的Agent Skill合集，每个Skill都可以从仓库子目录单独安装

当前公开`music-video-subtitle`和`concert-subtitle`，`ec-music-subtitle-align`的公开发布状态单独维护

## 项目归档约定

执行归档前先读取当前项目AGENTS.md及其指定的归档规则，确认实际内容分类、归档位置、文件夹命名、保留范围与清理授权。具体路径及个人约定由使用者在本地项目维护，公开Skill使用路径占位符

保存并核验成品、必要材料和人工修改后，仅按项目规则或本次用户指令已授权的范围清理。归档工具完成复制不自动增加删除权限，其他任务及工作区外原始素材保持原位

## 当前可用

|Skill|适用范围|单独安装|
|---|---|---|
|[`music-video-subtitle`](./music-video-subtitle/)|单曲MV、官方Music Video和短篇音乐影像的官方字幕优先、机器对齐、人工复对、预览、压制与交付|`https://github.com/AHPEventCollection/ec-subtitle-skills/tree/main/music-video-subtitle`|
|[`concert-subtitle`](./concert-subtitle/)|整场演唱会的本场资料查询、素材分析、歌曲与MC双语字幕、轻量预览及正片交付|`https://github.com/AHPEventCollection/ec-subtitle-skills/tree/main/concert-subtitle`|

## 安装

MV字幕Skill当前版本为`2.19.2`，演唱会Skill当前版本为`0.19.1`，两个Skill独立安装、独立使用

- 歌曲、MC、回顾、过场及MV共用时长比例进度条，MC权重3且限宽，MC标签常驻，未播白色40%透明，已播金色全高填充
- 多人歌曲按音画证据识别单人、句内接唱、合唱及未知，审核与正式ASS统一消费分色字段，不限定人数；不是自动多人MC识别器
- 两个公开Skill各自提供字幕制作、B站歌词弹幕与三平台文案路由，单独安装即可读取完整流程；MV原有视频号交付范围保留
- 字幕后交付各目标平台标题、正文、来源、真实Token用量及口径、使用模型、制作耗时、ec-subtitle-skills署名、话题和独立章节评论；B站简介不放章节表
- 发布时提醒微博、B站、小红书可编辑章节，至少核对开场和结束，保存后回读；个人栏目格式、账号和实际编辑入口由本地项目维护
- 终版优先实测并使用NVENC、QSV或AMF硬件编码，不可用时明确回退CPU
- 默认成片目录通过项目本地`.subtitle-render.json`配置，必须与片源分属不同物理硬盘；单次可用`--output`显式指定
- 外置成片通过压制回执纳入归档校验，本地路径配置和媒体不随Skill发布

在Codex或其他支持Agent Skills的工具中提供Skill目录链接

```text
帮我安装这个Skill：https://github.com/AHPEventCollection/ec-subtitle-skills/tree/main/music-video-subtitle
```

使用Codex内置安装器时，也可以只指定这个目录

```powershell
python install-skill-from-github.py --repo AHPEventCollection/ec-subtitle-skills --path music-video-subtitle
python install-skill-from-github.py --repo AHPEventCollection/ec-subtitle-skills --path concert-subtitle
```

安装只需复制所选Skill目录，不依赖仓库根目录业务代码；演唱会可使用`https://github.com/AHPEventCollection/ec-subtitle-skills/tree/main/concert-subtitle`

## 目录结构

```text
music-video-subtitle/
├── SKILL.md
├── agents/openai.yaml
├── scripts/
├── references/
├── tests/
├── runtime-requirements.json
└── LICENSE
```

具体字幕项目、媒体、模型、CUDA、FFmpeg、虚拟环境、缓存、Cookie和本地运行时配置不进入公开仓库

## 运行时

业务代码随Skill发布，Python环境、模型和工具保存在Skill目录之外

Skill自带`runtime_manager.py`和完整依赖合同，运行时可先执行检查

```powershell
python music-video-subtitle\scripts\runtime_manager.py doctor --project-root "<mv-project-root>" --task official-subtitle
python music-video-subtitle\scripts\runtime_manager.py doctor --project-root "<mv-project-root>" --task youtube-download
python music-video-subtitle\scripts\runtime_manager.py doctor --project-root "<mv-project-root>" --task song
```

已有可用运行时会自动复用，缺少依赖或模型时先生成安装计划并报告内容、预计空间、来源和候选位置，不静默安装

## 验证

```powershell
python -B -m ruff check --no-cache music-video-subtitle\scripts music-video-subtitle\tests
python -B -m unittest discover -s music-video-subtitle\tests -v
python -B quick_validate.py music-video-subtitle
python -B -m ruff check --no-cache concert-subtitle\scripts concert-subtitle\tests
python -B -m unittest discover -s concert-subtitle\tests -v
python -B quick_validate.py concert-subtitle
```

## 许可证

本仓库及可独立安装的Skill采用[MIT许可证](./LICENSE)
