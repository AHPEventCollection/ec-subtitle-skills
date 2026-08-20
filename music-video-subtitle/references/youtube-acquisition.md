# YouTube取源

## 固定边界

- 正式入口是`mv_pipeline.py download`，不得绕过它直接拼接yt-dlp命令
- 使用运行时合同中的base Python、`yt-dlp[default]`、FFmpeg、ffprobe和JS运行时
- 使用`--ignore-config`隔离用户级yt-dlp配置，缓存固定进入共享运行时`cache/yt-dlp/`
- 普通公开视频不读取浏览器Cookie，不改变浏览器状态，不安装临时客户端
- 下载先进入当前MV的`work/youtube-acquire/`，成功后原子移入`source/`，失败和成功后都清理暂存目录

## 有界策略

1. 首次使用yt-dlp上游默认客户端和完整EJS挑战解析
2. 只有格式403、缺少格式、签名或挑战解析失败时，执行一次`default,web_embedded`回退
3. HTTP429立即停止客户端轮询，报告`rate-limited`
4. 登录、年龄限制、会员或私有内容报告`authentication-required`
5. 其他错误报告`fatal`并保留输出尾部，不继续猜测客户端

`web_embedded`只作为允许嵌入视频的回退，不是永久默认客户端。不得为了继续流程自动降低清晰度、改用非官方片源或提取正在使用的浏览器Cookie库

## 证据

每次运行更新`review/source-acquisition.json`，至少记录运行时根、Python、FFmpeg、ffprobe、JS运行时、每次策略、返回码、耗时、错误分类、输出尾部和最终选择

`source/source.md`只记录最终成功策略。证据文件是取源审计，不是工作流状态，也不得包含Cookie、PO Token或账户信息

## 维护

客户端或挑战策略变化时先核对yt-dlp官方README、EJS说明和PO Token Guide，再修改脚本与测试并递增Skill版本。单次项目中出现的临时参数不能直接提升为永久规则
