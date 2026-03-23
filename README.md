# poke-mail

An MCP server that bridges IMAP/SMTP email accounts to [Poke](https://poke.com). Provides AI agents with tools to search, read, send, and manage emails, and automatically forwards new incoming emails to the Poke inbound endpoint.

## Features

- **12 MCP tools**: search, read, send, draft, archive, move, mark, list/create/rename/delete folders, server info
- **Send toggle**: Disable `send_email` globally or per account — agents use `create_draft` instead
- **IMAP IDLE watcher**: Real-time monitoring of new emails, forwarded to Poke automatically
- **Multi-account support**: Configure multiple email accounts in a single config file
- **Bearer token auth**: Secure the server with `MCP_API_KEY` so only you can use it
- **No delete tool**: Emails can be archived or moved, never deleted by an agent

## Quick Start

Copy this prompt into your AI coding agent (Claude Code, Cursor, etc.):

```text
Set up poke-mail (https://github.com/kacperkwapisz/poke-mail) for me — clone the repo, create a Python virtualenv named .venv, install requirements.txt, copy config.example.yml to config.yml, then help me configure config.yml — you can guide me on which IMAP/SMTP host and port to use for my email provider (iCloud, Gmail, Outlook, etc.) and what username format to use, but do NOT type or suggest passwords or API keys, tell me to enter those myself outside of this terminal and confirm when done — then generate a random 32+ character MCP_API_KEY, save it to a .env file as MCP_API_KEY=<the-key>, install the poke npm package globally, run poke login so I can authenticate, and run start.sh to start the server and tunnel it to Poke.
```

To start the server again later:

```bash
./start.sh
```

## Manual Setup

### 1. Configure accounts

```bash
cp config.example.yml config.yml
```

Edit `config.yml` with your email credentials:

```yaml
webhook_url: https://poke.com/api/v1/inbound/api-message
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
  ghcr.io/kacperkwapisz/poke-mail:main
```

### Resource Limits

The server is mostly idle (IMAP IDLE + lightweight HTTP). Recommended limits for container orchestrators:

| Resource | Reservation | Limit |
|----------|-------------|-------|
| Memory   | 128 MB      | 256 MB |
| CPU      | 0.25        | 0.5    |

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

## Poke Setup

Connect your MCP server to Poke at [poke.com/settings/connections](https://poke.com/settings/connections). Add the bearer token (`MCP_API_KEY`) in the connection auth settings.

The IDLE watcher automatically forwards new emails to Poke. You can also use the tools directly through Poke's AI agent.
