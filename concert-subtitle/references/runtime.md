# 运行时发现与安装

## 固定顺序

1. 先读取当前项目的`AGENTS.md`和`.subtitle-runtime.json`
2. 预检与抽音频运行`doctor --task prepare`，歌曲或MC本地识别运行`doctor --task song`或`doctor --task mc`
3. 已存在可用运行时就直接复用，不下载、不创建单场工作区虚拟环境
4. 只有项目绑定、环境变量和显式路径均缺失或失效时才运行`plan`，随后使用Everything核对本机是否已有同名模型、Python环境和ffmpeg
5. 仍有缺项时，把缺少内容、预计新增空间、来源和候选安装目录一次性告诉用户并等待确认
6. 获得确认后才安装，将环境、模型和工具写入同一个外部运行时根，更新`runtime.json`，再重新运行`doctor`

`AGENTS.md`和`.subtitle-runtime.json`只是本机绑定，不是Skill可用性的前提。换到没有这些文件的机器时，管理器会检查`SUBTITLE_RUNTIME_ROOT`、本机指针和Windows Everything索引，找不到时输出公开来源与安装估算

## 外部运行时结构

```text
subtitle-runtime/
  runtime.json
  environments/whisper/.venv/
  environments/whisper/python_deps/
  models/whisper/faster-whisper-large-v3/
  bin/
  cuda-runtime/
  locks/concert-subtitle-gpu-queue/
```

安装来源、固定revision和估算体积记录在`runtime-requirements.json`。下载必须先进入临时文件，完成后再原子改名。安装后必须实际导入依赖、打开模型并确认ffmpeg与ffprobe可执行

`runtime_manager.py plan`只生成计划，不修改磁盘。安装属于获得用户确认后的执行步骤，禁止把`doctor`失败直接解释为可以静默下载

## 运行入口

```powershell
python scripts/runtime_manager.py run --project-root <项目根> --task mc --profile whisper -- <处理器脚本> <参数>
```

管理器会注入共享bin、CUDA和Whisper模型路径。业务代码仍留在Skill，外部运行时只保存环境、模型和通用工具

GPU队列优先读取管理器注入的`SUBTITLE_RUNTIME_ROOT`，未通过管理器启动时读取项目`.subtitle-runtime.json`，并把机器级共享锁放入外部运行时`locks/concert-subtitle-gpu-queue`。没有任何运行时绑定时停止并报告缺项，不向Skill目录写入锁或日志
