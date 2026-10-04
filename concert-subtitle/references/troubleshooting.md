# 故障处理

## 找不到ffmpeg或ffprobe

先按任务运行Skill自带预检：

```powershell
python scripts/runtime_manager.py doctor --project-root "<concert-project-root>" --task prepare
```

管理器依次检查显式运行时、项目`.subtitle-runtime.json`、环境变量和本机索引，只接受通过版本实测的外部运行时`bin`目录，不从Skill目录或其他软件私有目录借用同名程序

## 分段目录出现Python或pip-cache

停止任务。Skill禁止安装依赖和建立临时环境

应使用项目`.subtitle-runtime.json`绑定的外部Python Profile。缺少依赖时运行`runtime_manager.py plan`并向用户报告内容、预计空间、来源和候选位置，不允许分段任务自行下载

## GPU任务互相抢显存

检查所有命令是否经过`gpu_queue.py`

机器级共享锁默认位于外部`subtitle-runtime/locks/concert-subtitle-gpu-queue`，每场演唱会的`run/runtime`只保存本场运行日志

解析顺序为`CONCERT_SUBTITLE_GPU_QUEUE_DIR`、`SUBTITLE_RUNTIME_ROOT`、项目`.subtitle-runtime.json`。三者都没有时直接停止，不在Skill目录内创建运行文件。显式指定另一处目录时，所有演唱会必须使用同一值

删除锁文件前先确认记录的PID不存在。正常异常退出会自动释放锁

## 歌词在伴奏区提前出现

检查：

- `first_vocal_start`
- `instrumental_gaps`
- 歌曲正式区间
- 是否把ASR幻觉当作歌词锚点

不得通过整体平移掩盖错段，先确认真实歌曲区间

## 少量尾部锚点导致整首错位

检查：

- `first_reliable_anchor_ratio`
- `max_extrapolation_seconds`
- `alignment_slope`
- `unsafe_reference_extrapolation`

补首段人工或音频定位锚点后，只重跑当前歌曲

## 审查片段未自动加载字幕

确认无字幕MP4与外挂ASS位于同一目录且文件名相同，播放器未自动加载时手动选择该ASS

失败时保留审查窗口JSON和错误报告，MP4与同名ASS缺一项都不算完整审查材料

## MC重排后没有明显改善

先确认观看的是审查ASS还是尚未应用建议的正式字幕。若审查ASS仍无明显改善，
检查`mc_complexity_assessment.json`

出现3人以上、交叠、快速抢话、说话人不可靠、画外音或人群插话时，停止继续
调整词级拆分阈值。自动重排只能整理已有ASR事件，不能重建说话人、语意和翻译

正确恢复路径：

1. 连续播放完整音画并标记话轮
2. 重新确定日文语句和必要的听写
3. 在新断句上重做中文翻译
4. 重跑gate并记录高风险MC全部审查点

静态画面、事件数增加、零重叠和Whisper词时间戳都不能作为自然断句改善证据
