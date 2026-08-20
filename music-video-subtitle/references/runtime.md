# 运行时发现与安装

## 固定顺序

1. 先读取当前项目的`AGENTS.md`和`.subtitle-runtime.json`
2. 运行`python scripts/runtime_manager.py doctor --project-root <项目根> --task <任务>`，读取`READY`、`DEGRADED`或`BLOCKED`
3. 已存在可用运行时就直接复用，不下载、不创建项目内虚拟环境
4. 只有项目绑定、环境变量和显式路径均缺失或失效时才运行`plan`，随后使用Everything核对本机是否已有同名模型、Python环境和ffmpeg
5. 仍有缺项时，把缺少内容、预计新增空间、来源和候选安装目录一次性告诉用户并等待确认
6. 获得确认后才安装，将环境、模型和工具写入同一个外部运行时根，更新`runtime.json`，再重新运行`doctor`

`AGENTS.md`和`.subtitle-runtime.json`只是本机绑定，不是Skill可用性的前提。换到没有这些文件的机器时，`runtime_manager.py`会检查`SUBTITLE_RUNTIME_ROOT`、本机指针和Windows Everything索引，找不到时输出公开来源与安装估算

## 任务选择

- `youtube-download`检查基础Pillow、pykakasi、yt-dlp、匹配的yt-dlp-ejs、ffmpeg、ffprobe及Deno>=2.3或Node>=22，不要求任何模型
- `official-subtitle`检查基础Pillow、pykakasi、yt-dlp、yt-dlp-ejs、ffmpeg和ffprobe，不要求任何模型
- `song`检查BS-RoFormer与SOFA
- `whisper`只检查Whisper
- `compare`检查3个Profile

## 外部运行时结构

```text
subtitle-runtime/
  runtime.json
  environments/
    whisper/.venv/
    whisper/python_deps/
    sofa/.venv/
    separator/.venv/
  models/
    whisper/faster-whisper-large-v3/
    sofa/akm_ja_v001/model.onnx
    separator/bs-roformer-viperx-1297/
  tools/SOFA/
  bin/
  cache/yt-dlp/
  cuda-runtime/
```

Python环境必须隔离。SOFA当前依赖`numpy1.24`，BS-RoFormer当前依赖`numpy2.x`，不能合并为一个虚拟环境

JS运行时在`runtime.json`的`shared.javascript`登记`kind`和`executable`。可以引用本机已经验证的外部Deno或Node绝对路径，不要求复制进运行时；没有登记时才检查当前`PATH`

## 安装合同

安装来源、固定版本和估算体积记录在`runtime-requirements.json`

- Whisper模型固定使用`Systran/faster-whisper-large-v3`及记录的revision
- BS-RoFormer固定使用描述文件中的ckpt和yaml
- SOFA源码固定到描述文件中的commit，日语模型固定使用`akm_ja_v001`
- SOFA推理保留ONNX模型即可，确认ONNX会话可建立后不保留训练ckpt和下载zip
- 下载必须先进入临时文件，完成后再原子改名或解压
- `doctor`必须实际执行ffmpeg与ffprobe、检查separator的CUDA，并用正式SOFA模型建立ONNX会话，不能只看文件存在或Provider列表

`runtime_manager.py plan`只生成计划，不修改磁盘。安装属于获得用户确认后的执行步骤，禁止把`doctor`失败直接解释为可以静默下载

## 运行入口

选择Profile后通过运行时管理器启动业务脚本或模块

```powershell
python scripts/runtime_manager.py run --project-root <项目根> --task song --profile separator -- scripts/separator_cli.py <参数>
python scripts/runtime_manager.py run --project-root <项目根> --task song --profile sofa -- <SOFA脚本> <参数>
python scripts/runtime_manager.py run --project-root <项目根> --task whisper --profile whisper -- <业务脚本> <参数>
```

管理器会注入共享bin、CUDA、模型与工具路径。业务代码仍留在Skill，外部运行时只保存环境、模型和通用工具
