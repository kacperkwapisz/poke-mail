# poke-mail

An MCP server that bridges IMAP/SMTP email accounts to [Poke](https://poke.com). Provides AI agents with tools to search, read, send, and manage emails, and automatically forwards new incoming emails to the Poke inbound endpoint.

## Features

- **11 MCP tools**: search, read, send, archive, move, mark, list/create/rename/delete folders, server info
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
  - id: work
    imap_host: imap.gmail.com
    imap_port: 993
    imap_username: you@gmail.com
    imap_password: your-app-password
    smtp_host: smtp.gmail.com
    smtp_port: 587
    smtp_username: you@gmail.com
    smtp_password: your-app-password
    watch_folders:
      - INBOX
```

For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833).

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
| `send_email` | Send email with text/HTML, CC/BCC, reply threading |
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

1. Push to GitHub
2. Create a Web Service on Render connected to your repo
3. Add `MCP_API_KEY`, `POKE_API_KEY`, and `POKE_WEBHOOK_URL` as environment variables
4. Mount your `config.yml` as a secret file or set `CONFIG_PATH`

## Poke Setup

Connect your MCP server to Poke at [poke.com/settings/connections](https://poke.com/settings/connections). Add the bearer token (`MCP_API_KEY`) in the connection auth settings.

The IDLE watcher automatically forwards new emails to Poke. You can also use the tools directly through Poke's AI agent.
