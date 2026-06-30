# 人大教务成绩手机提醒

这个项目会定时检查中国人民大学教务系统成绩页，一旦发现新课程成绩或已有成绩变化，就通过 PushPlus 推送到手机。

默认运行方式是 GitHub Actions，每 15 分钟执行一次。不需要自己的服务器，也不需要电脑常开。

另有一条健康检查 GitHub Actions，每 6 小时执行一次，从北京时间每天 00:00 开始，随后在 06:00、12:00、18:00 运行。健康检查会跑单元测试、查询教务接口并发送 PushPlus 消息；如果检查失败，也会尽量发送失败提醒。

## 需要准备

建一个 GitHub 仓库，然后在 `Settings -> Secrets and variables -> Actions` 里添加：

| Secret | 说明 |
| --- | --- |
| `RUC_TOKEN` | 登录教务系统后请求头里的 `TOKEN` |
| `PUSHPLUS_TOKEN` | PushPlus token |
| `GRADE_HASH_SALT` | 任意随机字符串，用来保护状态文件里的成绩指纹 |
| `RUC_COOKIE` | 可选。若接口只带 `TOKEN` 不稳定，再填浏览器请求里的 Cookie |

`PUSHPLUS_TOKEN` 和 `RUC_TOKEN` 不要写进代码。

## 获取 RUC_TOKEN

1. 浏览器登录 `https://jw.ruc.edu.cn/Njw2017/index.html#/student/course-score-search/`。
2. 打开开发者工具，进入 Network。
3. 刷新或重新点开成绩查询。
4. 找到 `findKccjList` 请求。
5. 复制请求头里的 `TOKEN` 值，放入 GitHub Secret `RUC_TOKEN`。

也可以在 Console 尝试：

```js
localStorage.getItem("qzdatasoft")
```

如果 GitHub Actions 运行时报登录态失效，再把同一个请求里的 Cookie 放到 `RUC_COOKIE`。

## 本地测试

复制一份本地配置：

```bash
cp .env.example .env
```

填入 `.env` 后运行：

```bash
python check_grades.py --config-check
python check_grades.py --dry-run
python check_grades.py --notify-test
python check_grades.py
```

`--config-check` 只输出脱敏后的配置摘要，可用来确认 `RUC_TOKEN`、`RUC_COOKIE`、`PUSHPLUS_TOKEN` 是否真的被脚本读到。

第一次正常运行只会创建基线状态，不会推送历史成绩。之后发现新增或变化才会推送。

## 部署

把这些文件推到私有 GitHub 仓库后，在仓库页面进入：

`Settings -> Secrets and variables -> Actions -> New repository secret`

添加这些 Secrets：

| Secret | 说明 |
| --- | --- |
| `RUC_TOKEN` | 最新 `findKccjList` 请求头里的 `TOKEN` 值 |
| `RUC_COOKIE` | 同一次 `findKccjList` 请求里 `-b '...'` 单引号内的完整 Cookie |
| `PUSHPLUS_TOKEN` | PushPlus 个人 token |
| `GRADE_HASH_SALT` | 任意随机字符串 |

然后进入 `Actions -> grade-monitor -> Run workflow` 手动运行一次。之后 GitHub Actions 会按 `.github/workflows/grade-monitor.yml` 每 15 分钟执行。

健康检查在 `.github/workflows/grade-health-check.yml` 中配置。它按北京时间每天 00:00、06:00、12:00、18:00 执行，也可以在 `Actions -> grade-health-check -> Run workflow` 手动触发。

状态保存在 `seen_grades.json`，里面只有 hash 指纹，不保存课程名和分数明文。

## 更新登录态

教务 `TOKEN` 过期后，脚本会通过 PushPlus 提醒。重新登录教务系统，找到最新的 `findKccjList` 请求，同时更新 GitHub Secrets 里的 `RUC_TOKEN` 和 `RUC_COOKIE`。

`RUC_TOKEN` 和 `RUC_COOKIE` 必须来自同一次请求。脚本会检查 `TOKEN` 里的 `sid` 和 Cookie 里的 `SESSION`，如果二者不一致，会提前报错，避免新旧登录态混用。

## 运行测试

```bash
python -m unittest discover -s tests
```
