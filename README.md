# EC Subtitle Skills

面向日中双语字幕制作的Agent Skill合集，每个Skill都可以从仓库子目录单独安装

当前公开`music-video-subtitle`和`concert-subtitle`，`ec-music-subtitle-align`的公开发布状态单独维护

## 当前可用

|Skill|适用范围|单独安装|
|---|---|---|
|[`music-video-subtitle`](./music-video-subtitle/)|单曲MV、官方Music Video和短篇音乐影像的官方字幕优先、机器对齐、人工复对、预览、压制与交付|`https://github.com/AHPEventCollection/ec-subtitle-skills/tree/main/music-video-subtitle`|
|[`concert-subtitle`](./concert-subtitle/)|整场演唱会的本场资料查询、素材分析、歌曲与MC双语字幕、轻量预览及正片交付|`https://github.com/AHPEventCollection/ec-subtitle-skills/tree/main/concert-subtitle`|

## 安装

演唱会Skill当前版本为`0.18.0`，与MV字幕Skill独立安装、独立使用

- 歌曲、MC、回顾、过场及MV共用整场进度条，仅当前段显示贴字标签
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
