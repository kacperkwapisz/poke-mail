# poke-mail

An MCP server that bridges IMAP/SMTP email accounts to [Poke](https://poke.com). Provides AI agents with tools to search, read, send, and manage emails, and automatically forwards new incoming emails to the Poke inbound endpoint.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/kacperkwapisz/poke-mail)
[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/github?repo=https://github.com/kacperkwapisz/poke-mail)

## Features

- **12 MCP tools**: search, read, send, draft, archive, move, mark, list/create/rename/delete folders, server info
- **Send toggle**: Disable `send_email` globally or per account — agents use `create_draft` instead
- **IMAP IDLE watcher**: Real-time monitoring of new emails, forwarded to Poke automatically
- **Multi-account support**: Configure multiple email accounts in a single config file
- **Bearer token auth**: Secure the server with `MCP_API_KEY` so only you can use it
- **No delete tool**: Emails can be archived or moved, never deleted by an agent

## Setup

### 1. Configure accounts

```bash
cp config.example.yml config.yml
```

Edit `config.yml` with your email credentials:

```yaml
webhook_url: https://poke.com/api/v1/inbound-sms/webhook
poke_api_key: your-api-key  # from https://poke.com/settings/advanced

accounts:
  # iCloud Mail — login is @icloud.com, send as your custom domain
  - id: icloud
    imap_host: imap.mail.me.com
    imap_username: you@icloud.com
    imap_password: your-app-password
    smtp_host: smtp.mail.me.com
    smtp_username: you@icloud.com
    smtp_password: your-app-password
    from_address: you@yourdomain.com  # optional — override From: header
    watch_folders:
      - INBOX

  # Custom SMTP server
  - id: work
    imap_host: imap.example.com
    imap_username: you@example.com
    imap_password: your-password
    smtp_host: smtp.example.com
    smtp_username: you@example.com
    smtp_password: your-password
    watch_folders:
      - INBOX
```

For iCloud, generate an [App-Specific Password](https://support.apple.com/en-us/102654).

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run

```bash
MCP_API_KEY=your-secret-key python src/server.py
```

### 4. Test

```bash
npx @modelcontextprotocol/inspector
```

Open http://localhost:3000 and connect to `http://localhost:3000/mcp` using "Streamable HTTP" transport. Pass `Authorization: Bearer your-secret-key` header.

## Authentication

Set `MCP_API_KEY` to secure the server. All requests must include `Authorization: Bearer <MCP_API_KEY>`.

If `MCP_API_KEY` is not set, the server runs unauthenticated (with a warning). **Always set it in production.**

When connecting from Poke, add the bearer token in your connection settings.

## Docker

```bash
docker build -t poke-mail .

docker run -d \
  -p 3000:3000 \
  -v $(pwd)/config.yml:/app/config.yml:ro \
  -e MCP_API_KEY=your-secret-key \
  poke-mail
```

Or use the pre-built image from GitHub Container Registry:

```bash
docker run -d \
  -p 3000:3000 \
  -v $(pwd)/config.yml:/app/config.yml:ro \
  -e MCP_API_KEY=your-secret-key \
  ghcr.io/OWNER/poke-mail:main
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `search_emails` | Search by from, to, subject, date range |
| `read_email` | Read full email content by UID |
| `send_email` | Send email with text/HTML, CC/BCC, reply threading (can be disabled) |
| `create_draft` | Save email as draft for review before sending |
| `archive_email` | Move email to Archive folder |
| `move_email` | Move email between folders |
| `mark_email` | Set read/unread/flagged/unflagged |
| `list_folders` | List all IMAP folders |
| `create_folder` | Create a new folder |
| `rename_folder` | Rename a folder |
| `delete_folder` | Delete a folder (protected folders blocked) |
| `get_server_info` | Server status and account connectivity |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_API_KEY` | — | **Required in production.** Bearer token to secure the MCP server |
| `CONFIG_PATH` | `config.yml` | Path to config file |
| `POKE_WEBHOOK_URL` | from config | Overrides webhook URL in config |
| `POKE_API_KEY` | from config | Overrides Poke API key in config |
| `PORT` | `3000` | HTTP server port |

## Deployment

### Docker (recommended)

The GitHub Actions workflow automatically builds and pushes to `ghcr.io` on every push to `main` and on tags.

1. Set repository secrets: none needed (uses `GITHUB_TOKEN` for GHCR)
2. Push to `main` or tag `v*` to trigger a build
3. Pull and run the image with your `config.yml` and `MCP_API_KEY`

### Render

Click the **Deploy to Render** button above, or manually:

1. Create a Web Service on Render connected to your repo
2. Fill in `IMAP_HOST`, `IMAP_USERNAME`, `IMAP_PASSWORD`, `POKE_WEBHOOK_URL`, and `POKE_API_KEY`
3. `MCP_API_KEY` is auto-generated — copy it for your Poke connection settings

For multi-account setups, mount a `config.yml` as a secret file instead of using env vars.

### Railway

Click the **Deploy on Railway** button above, then set the same environment variables as Render.

## Poke Setup

Connect your MCP server to Poke at [poke.com/settings/connections](https://poke.com/settings/connections). Add the bearer token (`MCP_API_KEY`) in the connection auth settings.

The IDLE watcher automatically forwards new emails to Poke. You can also use the tools directly through Poke's AI agent.
