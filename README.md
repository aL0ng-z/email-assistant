# Email Assistant Skill

Email Assistant 是一个用于邮件查询、总结和管理的 Codex Skill。它通过用户本地 `.env` 文件中的 IMAP/SMTP 配置连接邮箱，支持搜索邮件、读取正文、总结会话、保存附件、标记邮件、移动或删除邮件，以及在用户明确确认后发送、回复、转发邮件。

本仓库的根目录就是 Skill 根目录，必须保留 `SKILL.md`。

## 安装

从 GitHub 仓库安装：

```bash
python ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py --url https://github.com/<owner>/<repo>/tree/main
```

安装后重启 Codex，让新 Skill 生效。

本地开发时，也可以直接在本仓库根目录运行脚本和测试。

## 环境要求

- Python 3.10 或更高版本。
- 不需要第三方 Python 依赖，脚本只使用标准库。
- 邮箱账号已开启 IMAP/SMTP 服务。
- 邮箱账号支持授权码或应用专用密码。
- 本机网络可以访问对应邮箱服务商的 IMAP/SMTP 端口。

## 配置 `.env`

复制 `.env.example` 为 `.env`：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

也可以让脚本在安装后的 Skill 根目录中创建或更新 `.env`。163 邮箱推荐：

```bash
python scripts/email_cli.py configure --provider 163
```

这个命令会提示输入邮箱地址和授权码，并把配置写入和 `SKILL.md` 同级的 `.env`。

如果是在 Hermes 或其它 Agent 中做非交互式配置，不要把授权码放进命令行参数。可以把授权码通过标准输入传给脚本：

```bash
printf '%s\n' '<authorization-code>' | python scripts/email_cli.py configure --provider 163 --username yourname@163.com --auth-code-stdin --non-interactive
```

Windows PowerShell：

```powershell
"your-client-authorization-code" | python scripts\email_cli.py configure --provider 163 --username yourname@163.com --auth-code-stdin --non-interactive
```

默认情况下，脚本会读取 Skill 根目录下的 `.env`，也就是和 `SKILL.md` 同级的文件。这样可以避免 Hermes、Codex 或其它 Agent 因工作目录不同而读取到错误的 `.env`。

如果你确实想使用其它位置的配置文件，可以设置环境变量：

```bash
export EMAIL_ASSISTANT_ENV=/absolute/path/to/.env
```

Windows PowerShell：

```powershell
$env:EMAIL_ASSISTANT_ENV="C:\absolute\path\to\.env"
```

也可以在命令中显式传入：

```bash
python scripts/email_cli.py check-config --env /absolute/path/to/.env
```

然后填写邮箱配置：

```env
EMAIL_IMAP_HOST=
EMAIL_IMAP_PORT=993
EMAIL_IMAP_SSL=true
EMAIL_IMAP_SEND_ID=true
EMAIL_IMAP_ID_NAME=email-assistant
EMAIL_IMAP_ID_VERSION=1.0
EMAIL_IMAP_ID_VENDOR=email-assistant
EMAIL_SMTP_HOST=
EMAIL_SMTP_PORT=465
EMAIL_SMTP_SSL=true
EMAIL_SMTP_STARTTLS=false
EMAIL_USERNAME=
EMAIL_AUTH_CODE=
EMAIL_FROM=
EMAIL_DEFAULT_FOLDER=INBOX
EMAIL_SENT_FOLDER=
EMAIL_DRAFTS_FOLDER=
EMAIL_TRASH_FOLDER=
EMAIL_TIMEOUT_SECONDS=30
EMAIL_ASSISTANT_WORK_DIR=
```

不要提交 `.env`。其中包含邮箱授权码。

验证配置：

```bash
python scripts/email_cli.py check-config
```

检查输出中的 `config.env_path`，确认它指向你期望使用的 `.env`。如果 Hermes 中读到了错误路径，优先使用 `EMAIL_ASSISTANT_ENV` 指向绝对路径。

## 中间文件管理

运行中可能产生 compose JSON、下载附件和 `.eml` 草稿。默认情况下，这些文件会写入系统临时目录下的 `email-assistant` 工作目录。可以在 `.env` 中固定位置：

```env
EMAIL_ASSISTANT_WORK_DIR=C:\absolute\path\to\email-assistant-work
```

也可以在单次命令中传入：

```bash
python scripts/email_cli.py artifacts --work-dir /absolute/path/to/email-assistant-work
```

查看当前托管目录和文件清单：

```bash
python scripts/email_cli.py artifacts
```

把待发送内容暂存为受控 compose JSON：

```bash
python scripts/email_cli.py stage-compose --stdin
```

清理托管中间文件需要显式确认：

```bash
python scripts/email_cli.py cleanup-artifacts --max-age-hours 24 --confirm
python scripts/email_cli.py cleanup-artifacts --all --confirm
```

Agent 不应把临时 compose JSON、附件或 `.eml` 草稿写到 Skill 源码目录。若用户明确指定下载或导出路径，则以用户指定路径为准。

## 163 邮箱配置示例

以下示例适用于网易 163 个人邮箱，即 `yourname@163.com`。网易企业邮箱的服务器地址不同，不要混用。

使用前需要先登录 [163 邮箱网页版](https://mail.163.com/)，在设置中开启 `POP3/SMTP/IMAP` 或 `IMAP/SMTP` 服务，并生成客户端授权码。`EMAIL_AUTH_CODE` 填授权码，不要填网页登录密码。

`.env` 示例：

```env
EMAIL_IMAP_HOST=imap.163.com
EMAIL_IMAP_PORT=993
EMAIL_IMAP_SSL=true
EMAIL_IMAP_SEND_ID=true
EMAIL_IMAP_ID_NAME=email-assistant
EMAIL_IMAP_ID_VERSION=1.0
EMAIL_IMAP_ID_VENDOR=email-assistant
EMAIL_SMTP_HOST=smtp.163.com
EMAIL_SMTP_PORT=465
EMAIL_SMTP_SSL=true
EMAIL_SMTP_STARTTLS=false
EMAIL_USERNAME=yourname@163.com
EMAIL_AUTH_CODE=your-client-authorization-code
EMAIL_FROM=yourname@163.com
EMAIL_DEFAULT_FOLDER=INBOX
EMAIL_SENT_FOLDER=
EMAIL_DRAFTS_FOLDER=
EMAIL_TRASH_FOLDER=
EMAIL_TIMEOUT_SECONDS=30
EMAIL_ASSISTANT_WORK_DIR=
```

163 邮箱配置要点：

- IMAP 收信服务器：`imap.163.com`
- IMAP SSL 端口：`993`
- IMAP 客户端身份声明：`EMAIL_IMAP_SEND_ID=true`
- SMTP 发信服务器：`smtp.163.com`
- SMTP SSL 端口：`465`
- 用户名：完整邮箱地址，例如 `yourname@163.com`
- 密码：客户端授权码，不是网页登录密码
- 加密方式：SSL/TLS

如果连接失败，优先检查：

- 是否已在网页邮箱中开启 IMAP/SMTP 服务。
- 授权码是否复制完整，且没有多余空格。
- `.env` 中是否使用完整邮箱地址作为 `EMAIL_USERNAME`。
- `.env` 中是否保留 `EMAIL_IMAP_SEND_ID=true`；163 邮箱可能会把未声明客户端身份的 IMAP 登录拦截为 `Unsafe Login`。
- 当前网络是否允许访问 `993` 和 `465` 端口。
- 是否触发邮箱服务商的安全限制；必要时重新生成授权码。

## 安全模型

只读命令可以直接执行。会改变邮箱状态，或保存/删除用户可见本地文件的命令必须带 `--confirm`：

- `send`
- `reply`
- `forward`
- `draft`
- `mark`
- `move`
- `delete`
- `save-attachments`
- `cleanup-artifacts`

Agent 应先向用户展示操作摘要，例如收件人、主题、目标文件夹、UID、附件路径等；只有用户明确确认后，才能传入 `--confirm`。

`stage-compose` 只把 compose JSON 暂存到受控工作目录，不触发邮箱操作，默认不要求 `--confirm`；真正发送、回复或转发前仍必须让用户确认内容。

`delete` 会优先把邮件移动到 `EMAIL_TRASH_FOLDER`。如果没有配置垃圾箱，只有当 IMAP 服务器支持 UIDPLUS/UID EXPUNGE 时才允许直接删除，避免误清理同一文件夹中其他已标记删除的邮件。

## CLI 示例

列出文件夹：

```bash
python scripts/email_cli.py folders
```

搜索收件箱未读邮件：

```bash
python scripts/email_cli.py search --folder INBOX --unseen --limit 20
```

读取一封邮件：

```bash
python scripts/email_cli.py fetch --folder INBOX --uid 123 --max-chars 12000
```

通过 compose JSON 发送邮件：

```bash
python scripts/email_cli.py stage-compose --stdin
python scripts/email_cli.py send --input <returned-compose-path> --confirm
```

## 开发检查

运行测试：

```bash
python -m unittest discover -s tests
```

验证 Skill 元数据：

```bash
python C:\Users\shr08\.codex\skills\.system\skill-creator\scripts\quick_validate.py .
```

## 参考

- [163 邮箱网页版](https://mail.163.com/)
- [网易 163 企业邮箱 POP、SMTP、IMAP 参数](https://www.163email.com.cn/help/343)
- [163 个人邮箱常见 IMAP/SMTP 设置参考](https://github.com/chatmail/provider-db/blob/master/_providers/163.md)

## License

MIT
