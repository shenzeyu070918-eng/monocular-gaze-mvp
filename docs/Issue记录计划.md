# Issue记录计划（第四阶段，尚未创建）

顺序：整理PR合并→历史验证/确认强推→tag/Release→本页gh补录→Pages。前三步优先；本阶段不创建Issue或label。所有Issue必须用gh、正文文件，不能用API/网页伪造历史。

## 身份和labels

先`gh auth status`核对实际登录为shenzeyu070918-eng。准备labels：task、bug、documentation、repository、history-rewrite、release、demo、privacy、validation、retrospective，以及milestone:M0至milestone:M11。先用`gh label list`检查，缺少的用`gh label create`创建，已有的保留，不为了统一颜色强改。任务/缺陷模板上的task/bug由此确保存在。

## 后续与补录清单

| Issue | body文件 | labels |
| --- | --- | --- |
| M10收尾含DeepSeek | [m10-closeout.md](issue-drafts/m10-closeout.md) | task, milestone:M10, validation |
| 仓库整理/身份/Release事后汇总 | [repository-retrospective.md](issue-drafts/repository-retrospective.md) | task, repository, retrospective |
| M11彩排 | [m11-rehearsal.md](issue-drafts/m11-rehearsal.md) | task, milestone:M11, demo |
| M0–M9每里程碑一条事后记录 | issue-drafts/m0-retrospective.md 至 m9-retrospective.md | task, retrospective, 对应milestone:M编号 |
| Pages发布 | 届时按真实范围另写body | task, demo, privacy |

M3/M8可链接多个真实PR，M6先于M5的实际合并顺序不改。历史Issue正文已写“事后补录”及真实PR/验证记录链接，不冒充实施前的过程；不补建虚构bug、不改创建/关闭日期。

## 命令示例（届时执行）

```sh
gh issue create --repo shenzeyu070918-eng/monocular-gaze-mvp --title 'M10: 收尾与DeepSeek实机验收' --assignee shenzeyu070918-eng --label task,milestone:M10,validation --body-file docs/issue-drafts/m10-closeout.md

gh issue create --repo shenzeyu070918-eng/monocular-gaze-mvp --title 'M9: 屏幕注意力记录（事后补录）' --assignee shenzeyu070918-eng --label task,retrospective,milestone:M9 --body-file docs/issue-drafts/m9-retrospective.md
```

每次记录gh返回的真实编号/URL，创建前查重避免重跑重复建Issue。关闭历史汇总前逐项核查正文与真实PR/验证记录，把当时已发布tag/Release补入；缺的不能编造。仓库汇总草稿的“创建前替换”段必须换成实际整理PR/Release/验证链接，不带占位发布。

M10为未来恢复工作，不加事后补录标签也不提前关闭，恢复后给PR #17关联实际Issue；不会为了Issue链接另建第二份M10实现。所有公开内容去除个人录制/原始轨迹/屏幕内容和密钥。
