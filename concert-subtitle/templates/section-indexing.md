# 演唱会区间识别任务

根据`{{setlist_path}}`和预检后的`{{audio_path}}`识别全部歌曲与MC区间，输出`{{sections_path}}`

要求：

1. 保持歌单顺序
2. 区分歌曲、MC、返场和纯伴奏
3. 每段写入开始、结束、证据来源和0至1置信度
4. 音频识别和定位只使用独立音频
5. 允许查看源视频确认画面转场，但不得把视频直接交给ASR
6. 低置信、相似副歌和疑似错段必须标记，不得自行假定
7. 不生成正式字幕
8. 整场图形覆盖歌曲、MC、回顾、过场和片尾MV；歌曲与MC独立编号，不能因缺歌词推断新增歌曲
9. 非字幕工作包的展示区间写入同级presentation.timeline_extras，每项为type（recap/transition/mv）、start、end，不得与sections重叠；展示配置不替代字幕覆盖审计或批准
10. 默认progress_bar为x=0、y=8、width=1920、height=18；只显示当前段内嵌文字，歌曲为“1. 歌名”，MC独立编号，字号24，文字块按字宽贴合、两侧各8像素、高34；其他段无文字，剩余宽度均分；其余空隙自动显示过场
11. 已有工作包用presentation.section_kinds按稳定ID覆盖展示分类（song/mc/recap/transition/mv），不改业务type；节目展示范围用presentation.timeline_range的start/end全片绝对秒明确指定，范围外宣传不纳入，边界不得截断已有工作包

输出格式必须能被`index_concert.py --sections`直接读取
