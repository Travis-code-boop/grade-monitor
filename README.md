# 人大教务成绩手机提醒

这个项目会定时检查中国人民大学教务系统成绩页，一旦发现新课程成绩或已有成绩变化，就通过 PushPlus 推送到手机。

默认运行方式是 GitHub Actions，每 15 分钟执行一次。不需要自己的服务器，也不需要电脑常开。

另有一条健康检查 GitHub Actions，每 6 小时执行一次，从北京时间每天 00:11 开始，随后在 06:11、12:11、18:11 运行。健康检查会跑单元测试、查询教务接口并发送 PushPlus 消息；如果检查失败，也会尽量发送失败提醒。

## 需要准备

建一个 GitHub 仓库，然后在 `Settings -> Secrets and variables -> Actions` 里添加：

| Secret | 说明 |
| --- | --- |
| `RUC_USERNAME` | 人大统一身份认证账号，通常是学号或工号 |
| `RUC_PASSWORD` | 人大统一身份认证密码 |
| `PUSHPLUS_TOKEN` | PushPlus token |
| `GRADE_HASH_SALT` | 任意随机字符串，用来保护状态文件里的成绩指纹 |
| `RUC_TOKEN` | 可选旧方案。账号密码直登不可用时，可填浏览器请求头里的 `TOKEN` |
| `RUC_COOKIE` | 可选旧方案。与 `RUC_TOKEN` 来自同一次成绩请求 |

`RUC_PASSWORD`、`PUSHPLUS_TOKEN`、`RUC_TOKEN` 和 `RUC_COOKIE` 不要写进代码。

## 登录方式

默认使用 `RUC_USERNAME` 和 `RUC_PASSWORD` 登录人大统一身份认证，成功后自动换取教务 `TOKEN` 和 Cookie，再查询成绩。

如果账号是普通学号/工号，脚本会按统一认证网页的规则自动加上 `ruc:` 前缀。邮箱、手机号、已经带 `:` 前缀的账号会原样使用。少数情况下如果学校代码不是 `ruc`，可以添加变量 `RUC_LOGIN_SCHOOL_CODE`。

如果统一身份认证临时要求图片验证码或二次验证码，GitHub Actions 无法自动完成登录，脚本会通过 PushPlus 发送失败提醒。此时可以稍后重试，或临时使用旧的 `RUC_TOKEN` / `RUC_COOKIE` 方案。

旧方案获取方式：

1. 浏览器登录 `https://jw.ruc.edu.cn/Njw2017/index.html#/student/course-score-search/`。
2. 打开开发者工具，进入 Network。
3. 刷新或重新点开成绩查询。
4. 找到 `findKccjList` 请求。
5. 复制请求头里的 `TOKEN` 值到 GitHub Secret `RUC_TOKEN`。
6. 如接口只带 `TOKEN` 不稳定，再把同一次请求里的 Cookie 填入 `RUC_COOKIE`。

## 本地测试

复制一份本地配置：

```bash
cp .env.example .env
```

填入 `.env` 后运行：

```bash
python3 check_grades.py --config-check
python3 check_grades.py --dry-run
python3 check_grades.py --notify-test
python3 check_grades.py
```

`--config-check` 只输出脱敏后的配置摘要，可用来确认 `RUC_USERNAME`、`RUC_PASSWORD`、`PUSHPLUS_TOKEN` 等配置是否真的被脚本读到。

第一次正常运行只会创建基线状态，不会推送历史成绩。之后发现新增或变化才会推送。

## 部署

把这些文件推到私有 GitHub 仓库后，在仓库页面进入：

`Settings -> Secrets and variables -> Actions -> New repository secret`

添加这些 Secrets：

| Secret | 说明 |
| --- | --- |
| `RUC_USERNAME` | 人大统一身份认证账号 |
| `RUC_PASSWORD` | 人大统一身份认证密码 |
| `PUSHPLUS_TOKEN` | PushPlus 个人 token |
| `GRADE_HASH_SALT` | 任意随机字符串 |
| `RUC_TOKEN` | 可选旧方案 |
| `RUC_COOKIE` | 可选旧方案 |

然后进入 `Actions -> grade-monitor -> Run workflow` 手动运行一次。之后 GitHub Actions 会按 `.github/workflows/grade-monitor.yml` 每 15 分钟执行。

健康检查在 `.github/workflows/grade-health-check.yml` 中配置。它按北京时间每天 00:11、06:11、12:11、18:11 执行，也可以在 `Actions -> grade-health-check -> Run workflow` 手动触发。

状态保存在 `seen_grades.json`，里面只有 hash 指纹，不保存课程名和分数明文。

## 更新登录信息

账号密码方案下，平时不需要手动更新教务 `TOKEN`。如果统一身份认证密码变更，只需要更新 GitHub Secret `RUC_PASSWORD`。

如果使用旧方案，教务 `TOKEN` 过期后脚本会通过 PushPlus 提醒。重新登录教务系统，找到最新的 `findKccjList` 请求，同时更新 GitHub Secrets 里的 `RUC_TOKEN` 和 `RUC_COOKIE`。二者必须来自同一次请求，脚本会检查 `TOKEN` 里的 `sid` 和 Cookie 里的 `SESSION`，避免新旧登录态混用。

## 运行测试

```bash
python3 -m unittest discover -s tests
```
