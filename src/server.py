#!/usr/bin/env python3
import asyncio
import logging
import os
import smtplib
import ssl
from contextlib import asynccontextmanager
from datetime import date
from email import policy
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.parser import BytesParser
from typing import Optional

import hmac

import httpx
import yaml
from imapclient import IMAPClient
from fastmcp import FastMCP, Context
from fastmcp.server.auth import TokenVerifier, AccessToken

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("poke-mail")
logging.getLogger("httpx").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Auth — simple bearer token verification
# ---------------------------------------------------------------------------


class ApiKeyAuth(TokenVerifier):
    """Validates incoming requests against a static API key (MCP_API_KEY)."""

    def __init__(self, api_key: str):
        super().__init__()
        self._api_key = api_key

    async def verify_token(self, token: str) -> AccessToken | None:
        if hmac.compare_digest(token, self._api_key):
            return AccessToken(token=token, client_id="owner", scopes=["all"])
        return None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config() -> dict:
    path = os.environ.get("CONFIG_PATH", "config.yml")
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("Config file %s not found, using env vars", path)
        return {}


def parse_accounts(config: dict) -> list[dict]:
    accounts = config.get("accounts", [])
    if not accounts:
        # Fallback to flat env vars for single account
        imap_host = os.environ.get("IMAP_HOST")
        if not imap_host:
            raise RuntimeError(
                "No accounts configured. Set POKE_MAIL_ACCOUNTS env var or create config.yml"
            )
        accounts = [
            {
                "id": "default",
                "imap_host": imap_host,
                "imap_port": int(os.environ.get("IMAP_PORT", "993")),
                "imap_username": os.environ["IMAP_USERNAME"],
                "imap_password": os.environ["IMAP_PASSWORD"],
                "smtp_host": os.environ.get("SMTP_HOST", imap_host),
                "smtp_port": int(os.environ.get("SMTP_PORT", "587")),
                "smtp_username": os.environ.get(
                    "SMTP_USERNAME", os.environ["IMAP_USERNAME"]
                ),
                "smtp_password": os.environ.get(
                    "SMTP_PASSWORD", os.environ["IMAP_PASSWORD"]
                ),
                "watch_folders": ["INBOX"],
            }
        ]

    global_allow_send = config.get("allow_send", False)
    required = ("imap_host", "imap_username", "imap_password")
    for i, acc in enumerate(accounts):
        acc.setdefault("id", f"account-{i}")
        acc.setdefault("imap_port", 993)
        acc.setdefault("watch_folders", ["INBOX"])
        # SMTP falls back to IMAP if not specified
        acc.setdefault("smtp_host", acc.get("imap_host"))
        acc.setdefault("smtp_port", 587)
        acc.setdefault("smtp_username", acc.get("imap_username"))
        acc.setdefault("smtp_password", acc.get("imap_password"))
        acc.setdefault("allow_send", global_allow_send)
        for field in required:
            if field not in acc:
                raise RuntimeError(
                    f"Account '{acc['id']}' missing required field: {field}"
                )
    return accounts


def resolve_account(accounts: list[dict], account_id: Optional[str] = None) -> dict:
    if not account_id:
        return accounts[0]
    for acc in accounts:
        if acc["id"] == account_id:
            return acc
    raise ValueError(
        f"Unknown account_id: {account_id}. Available: {[a['id'] for a in accounts]}"
    )


# ---------------------------------------------------------------------------
# IMAP helpers
# ---------------------------------------------------------------------------


def get_imap_client(account: dict) -> IMAPClient:
    port = account["imap_port"]
    use_ssl = port == 993
    client = IMAPClient(account["imap_host"], port=port, ssl=use_ssl)
    if not use_ssl:
        client.starttls()
    client.login(account["imap_username"], account["imap_password"])
    return client


def parse_email_message(raw: bytes) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(raw)

    body_text = ""
    body_html = ""
    attachments = []

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                try:
                    content = part.get_content()
                    size = len(content) if hasattr(content, "__len__") else 0
                except Exception:
                    size = 0
                attachments.append(
                    {
                        "filename": part.get_filename() or "unnamed",
                        "content_type": ct,
                        "size": size,
                    }
                )
            elif ct == "text/plain" and not body_text:
                try:
                    body_text = part.get_content()
                except Exception:
                    body_text = part.get_payload(decode=True).decode(errors="replace")
            elif ct == "text/html" and not body_html:
                try:
                    body_html = part.get_content()
                except Exception:
                    body_html = part.get_payload(decode=True).decode(errors="replace")
    else:
        ct = msg.get_content_type()
        try:
            content = msg.get_content()
        except Exception:
            payload = msg.get_payload(decode=True)
            content = payload.decode(errors="replace") if payload else ""
        if ct == "text/html":
            body_html = content
        else:
            body_text = content

    to_header = msg["to"] or ""
    cc_header = msg["cc"] or ""

    def parse_addresses(header):
        if not header:
            return []
        return [addr.strip() for addr in str(header).split(",") if addr.strip()]

    return {
        "from": str(msg["from"] or ""),
        "to": parse_addresses(to_header),
        "cc": parse_addresses(cc_header),
        "subject": str(msg["subject"] or ""),
        "date": str(msg["date"] or ""),
        "body_text": body_text,
        "body_html": body_html,
        "headers": {k: str(v) for k, v in msg.items()},
        "attachments": attachments,
    }


def build_search_criteria(
    from_addr: Optional[str] = None,
    to_addr: Optional[str] = None,
    subject: Optional[str] = None,
    since: Optional[str] = None,
    before: Optional[str] = None,
) -> list:
    criteria = []
    if from_addr:
        criteria.extend(["FROM", from_addr])
    if to_addr:
        criteria.extend(["TO", to_addr])
    if subject:
        criteria.extend(["SUBJECT", subject])
    if since:
        criteria.extend(["SINCE", date.fromisoformat(since)])
    if before:
        criteria.extend(["BEFORE", date.fromisoformat(before)])
    if not criteria:
        criteria = ["ALL"]
    return criteria


def detect_archive_folder(client: IMAPClient) -> str:
    folders = client.list_folders()
    for flags, _delim, name in folders:
        if b"\\Archive" in flags:
            return name
        if name in ("[Gmail]/All Mail", "Archive"):
            return name
    return "Archive"


def detect_drafts_folder(client: IMAPClient) -> str:
    import imapclient as imc

    result = client.find_special_folder(imc.DRAFTS)
    if result:
        return result
    for name in ("Drafts", "[Gmail]/Drafts", "INBOX.Drafts"):
        if client.folder_exists(name):
            return name
    return "Drafts"


# ---------------------------------------------------------------------------
# Poke webhook
# ---------------------------------------------------------------------------


async def forward_to_poke(email_data: dict, webhook_url: str, api_key: str) -> bool:
    payload = {
        "from": email_data["from"],
        "to": email_data["to"],
        "subject": email_data["subject"],
        "date": email_data["date"],
        "body_text": email_data["body_text"],
        "body_html": email_data["body_html"],
        "headers": email_data.get("headers", {}),
        "attachments": email_data.get("attachments", []),
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        logger.warning(
            "No Poke API key configured — webhook request will be unauthenticated"
        )

    logger.debug(
        "Forwarding to %s (api_key set: %s, key prefix: %s)",
        webhook_url,
        bool(api_key),
        api_key[:8] + "..." if api_key and len(api_key) > 8 else "***",
    )

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                resp = await http.post(webhook_url, json=payload, headers=headers)
                resp.raise_for_status()
                logger.info(
                    "Forwarded email '%s' to Poke (status %d)",
                    email_data["subject"],
                    resp.status_code,
                )
                return True
        except Exception as e:
            logger.warning("Forward attempt %d failed: %s", attempt + 1, e)
            if attempt == 0:
                await asyncio.sleep(2)
    return False


# ---------------------------------------------------------------------------
# IDLE watcher
# ---------------------------------------------------------------------------


async def watch_folder(
    account: dict,
    folder: str,
    webhook_url: str,
    api_key: str,
    stop_event: asyncio.Event,
):
    backoff = 5
    max_backoff = 60

    while not stop_event.is_set():
        client = None
        try:
            client = await asyncio.to_thread(get_imap_client, account)
            await asyncio.to_thread(client.select_folder, folder)

            # Check IDLE support
            if not client.has_capability("IDLE"):
                logger.warning(
                    "[%s/%s] Server does not support IDLE, falling back to polling",
                    account["id"],
                    folder,
                )
                await _poll_folder(
                    client, account, folder, webhook_url, api_key, stop_event
                )
                return

            # Record existing unseen UIDs so we only forward truly new ones
            existing_unseen = set(await asyncio.to_thread(client.search, ["UNSEEN"]))
            logger.info(
                "[%s/%s] Watching for new emails via IDLE (%d existing unseen skipped)",
                account["id"],
                folder,
                len(existing_unseen),
            )
            backoff = 5

            while not stop_event.is_set():
                await asyncio.to_thread(client.idle)
                try:
                    responses = await asyncio.to_thread(client.idle_check, 120)
                except Exception:
                    try:
                        await asyncio.to_thread(client.idle_done)
                    except Exception:
                        pass
                    break
                await asyncio.to_thread(client.idle_done)

                has_new = any(
                    isinstance(r, tuple) and len(r) >= 2 and r[1] == b"EXISTS"
                    for r in responses
                )
                if not has_new:
                    continue

                uids = await asyncio.to_thread(client.search, ["UNSEEN"])
                # Only forward emails that arrived after we started watching
                new_uids = [u for u in uids if u not in existing_unseen]
                if not new_uids:
                    continue

                raw_messages = await asyncio.to_thread(
                    client.fetch, new_uids, ["RFC822"]
                )
                for uid, data in raw_messages.items():
                    raw = data.get(b"RFC822", b"")
                    if not raw:
                        continue
                    email_data = parse_email_message(raw)
                    await forward_to_poke(email_data, webhook_url, api_key)

                await asyncio.to_thread(client.set_flags, new_uids, [b"\\Seen"])

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(
                "[%s/%s] Watcher error: %s (reconnecting in %ds)",
                account["id"],
                folder,
                e,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
        finally:
            if client:
                try:
                    await asyncio.to_thread(client.logout)
                except Exception:
                    pass


async def _poll_folder(
    client: IMAPClient,
    account: dict,
    folder: str,
    webhook_url: str,
    api_key: str,
    stop_event: asyncio.Event,
):
    """Fallback polling for servers without IDLE support. Checks every 60 seconds."""
    logger.info("[%s/%s] Polling for new emails every 60s", account["id"], folder)
    while not stop_event.is_set():
        try:
            uids = await asyncio.to_thread(client.search, ["UNSEEN"])
            if uids:
                raw_messages = await asyncio.to_thread(client.fetch, uids, ["RFC822"])
                for uid, data in raw_messages.items():
                    raw = data.get(b"RFC822", b"")
                    if not raw:
                        continue
                    email_data = parse_email_message(raw)
                    await forward_to_poke(email_data, webhook_url, api_key)
                await asyncio.to_thread(client.set_flags, uids, [b"\\Seen"])
        except Exception as e:
            logger.warning("[%s/%s] Poll error: %s", account["id"], folder, e)
            raise  # reconnect via outer loop
        await asyncio.sleep(60)


async def idle_watcher(
    accounts: list[dict], webhook_url: str, api_key: str, stop_event: asyncio.Event
):
    tasks = []
    for acc in accounts:
        for folder in acc.get("watch_folders", ["INBOX"]):
            tasks.append(
                asyncio.create_task(
                    watch_folder(acc, folder, webhook_url, api_key, stop_event)
                )
            )
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(server: FastMCP):
    config = load_config()
    accounts = parse_accounts(config)
    webhook_url = os.environ.get(
        "POKE_WEBHOOK_URL",
        config.get("webhook_url", "https://poke.com/api/v1/inbound-sms/webhook"),
    )
    api_key = os.environ.get("POKE_API_KEY", config.get("poke_api_key", ""))
    stop_event = asyncio.Event()

    watcher_task = asyncio.create_task(
        idle_watcher(accounts, webhook_url, api_key, stop_event)
    )
    logger.info("poke-mail started with %d account(s)", len(accounts))

    try:
        yield {
            "accounts": accounts,
            "webhook_url": webhook_url,
            "api_key": api_key,
            "config": config,
        }
    finally:
        stop_event.set()
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass
        logger.info("poke-mail shut down")


# ---------------------------------------------------------------------------
# MCP Server & Tools
# ---------------------------------------------------------------------------

mcp_api_key = os.environ.get("MCP_API_KEY", "")
auth = ApiKeyAuth(mcp_api_key) if mcp_api_key else None
if not mcp_api_key:
    logger.warning(
        "MCP_API_KEY not set — server is unauthenticated. Set MCP_API_KEY to secure it."
    )

mcp = FastMCP("poke-mail", lifespan=lifespan, auth=auth)


@mcp.tool(
    description="Search emails by criteria. Returns a list of matching emails with metadata."
)
async def search_emails(
    ctx: Context,
    folder: str = "INBOX",
    account_id: Optional[str] = None,
    from_addr: Optional[str] = None,
    to_addr: Optional[str] = None,
    subject: Optional[str] = None,
    since: Optional[str] = None,
    before: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    accounts = ctx.lifespan_context["accounts"]
    acc = resolve_account(accounts, account_id)
    criteria = build_search_criteria(from_addr, to_addr, subject, since, before)

    def _search():
        client = get_imap_client(acc)
        try:
            client.select_folder(folder, readonly=True)
            uids = client.search(criteria)
            uids = uids[-limit:]  # most recent
            if not uids:
                return []
            data = client.fetch(uids, ["ENVELOPE", "FLAGS", "RFC822.SIZE"])
            results = []
            for uid, msg_data in data.items():
                env = msg_data.get(b"ENVELOPE")
                if not env:
                    continue

                def _fmt_addr(addr):
                    """Format an IMAP envelope address safely."""
                    try:
                        name = addr.name.decode(errors="replace") if addr.name else ""
                        mailbox = (
                            addr.mailbox.decode(errors="replace")
                            if addr.mailbox
                            else ""
                        )
                        host = addr.host.decode(errors="replace") if addr.host else ""
                        email = f"{mailbox}@{host}" if mailbox else ""
                        return f"{name} <{email}>" if name else email
                    except Exception:
                        return str(addr)

                results.append(
                    {
                        "uid": uid,
                        "from": _fmt_addr(env.from_[0]) if env.from_ else "",
                        "to": [_fmt_addr(a) for a in (env.to or [])],
                        "subject": env.subject.decode(errors="replace")
                        if env.subject
                        else "",
                        "date": str(env.date) if env.date else "",
                        "flags": [
                            f.decode(errors="replace")
                            for f in msg_data.get(b"FLAGS", [])
                        ],
                        "size": msg_data.get(b"RFC822.SIZE", 0),
                    }
                )
            return results
        finally:
            client.logout()

    return await asyncio.to_thread(_search)


@mcp.tool(
    description="Read a specific email by UID. Returns full email content including body and attachment metadata."
)
async def read_email(
    ctx: Context,
    uid: int,
    folder: str = "INBOX",
    account_id: Optional[str] = None,
) -> dict:
    accounts = ctx.lifespan_context["accounts"]
    acc = resolve_account(accounts, account_id)

    def _read():
        client = get_imap_client(acc)
        try:
            client.select_folder(folder, readonly=True)
            data = client.fetch([uid], ["RFC822"])
            if uid not in data:
                return {"error": f"Email UID {uid} not found in {folder}"}
            return parse_email_message(data[uid][b"RFC822"])
        finally:
            client.logout()

    return await asyncio.to_thread(_read)


@mcp.tool(
    description="Send an email via SMTP. Supports plain text and HTML, CC/BCC, and reply threading."
)
async def send_email(
    ctx: Context,
    to: str,
    subject: str,
    body: str,
    account_id: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    html: Optional[str] = None,
    reply_to_uid: Optional[int] = None,
    reply_to_folder: Optional[str] = None,
) -> dict:
    accounts = ctx.lifespan_context["accounts"]
    acc = resolve_account(accounts, account_id)

    if not acc.get("allow_send", False):
        return {
            "error": f"Sending is disabled for account '{acc['id']}'. Use create_draft instead."
        }

    def _send():
        msg = MIMEMultipart("alternative") if html else MIMEText(body)
        if html:
            msg.attach(MIMEText(body, "plain"))
            msg.attach(MIMEText(html, "html"))

        msg["From"] = acc["smtp_username"]
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc

        # Threading headers for replies
        if reply_to_uid and reply_to_folder:
            imap = None
            try:
                imap = get_imap_client(acc)
                imap.select_folder(reply_to_folder or "INBOX", readonly=True)
                orig_data = imap.fetch([reply_to_uid], ["RFC822.HEADER"])
                if reply_to_uid in orig_data:
                    orig = BytesParser(policy=policy.default).parsebytes(
                        orig_data[reply_to_uid][b"RFC822.HEADER"]
                    )
                    if orig["Message-ID"]:
                        msg["In-Reply-To"] = orig["Message-ID"]
                        refs = orig.get("References", "")
                        msg["References"] = f"{refs} {orig['Message-ID']}".strip()
            except Exception as e:
                logger.warning("Could not fetch reply headers: %s", e)
            finally:
                if imap:
                    try:
                        imap.logout()
                    except Exception:
                        pass

        recipients = [addr.strip() for addr in to.split(",")]
        if cc:
            recipients.extend(addr.strip() for addr in cc.split(","))
        if bcc:
            recipients.extend(addr.strip() for addr in bcc.split(","))

        port = acc["smtp_port"]
        if port == 465:
            ctx_ssl = ssl.create_default_context()
            with smtplib.SMTP_SSL(acc["smtp_host"], port, context=ctx_ssl) as smtp:
                smtp.login(acc["smtp_username"], acc["smtp_password"])
                smtp.sendmail(acc["smtp_username"], recipients, msg.as_string())
        else:
            with smtplib.SMTP(acc["smtp_host"], port) as smtp:
                smtp.starttls()
                smtp.login(acc["smtp_username"], acc["smtp_password"])
                smtp.sendmail(acc["smtp_username"], recipients, msg.as_string())

        return {"success": True, "message_id": msg.get("Message-ID", "")}

    return await asyncio.to_thread(_send)


@mcp.tool(
    description="Save an email as a draft for review before sending. The draft appears in the account's Drafts folder."
)
async def create_draft(
    ctx: Context,
    to: str,
    subject: str,
    body: str,
    account_id: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    html: Optional[str] = None,
) -> dict:
    accounts = ctx.lifespan_context["accounts"]
    acc = resolve_account(accounts, account_id)

    def _draft():
        msg = MIMEMultipart("alternative") if html else MIMEText(body)
        if html:
            msg.attach(MIMEText(body, "plain"))
            msg.attach(MIMEText(html, "html"))

        msg["From"] = acc["smtp_username"]
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc

        client = get_imap_client(acc)
        try:
            drafts_folder = detect_drafts_folder(client)
            if not client.folder_exists(drafts_folder):
                client.create_folder(drafts_folder)
            client.append(drafts_folder, msg.as_bytes(), flags=[b"\\Draft", b"\\Seen"])
            return {"success": True, "folder": drafts_folder}
        finally:
            client.logout()

    return await asyncio.to_thread(_draft)


@mcp.tool(
    description="Archive an email by moving it to the Archive folder instead of deleting."
)
async def archive_email(
    ctx: Context,
    uid: int,
    folder: str = "INBOX",
    account_id: Optional[str] = None,
) -> dict:
    accounts = ctx.lifespan_context["accounts"]
    acc = resolve_account(accounts, account_id)

    def _archive():
        client = get_imap_client(acc)
        try:
            client.select_folder(folder)
            archive_folder = detect_archive_folder(client)
            # Ensure archive folder exists
            if not client.folder_exists(archive_folder):
                client.create_folder(archive_folder)
            client.copy([uid], archive_folder)
            client.delete_messages([uid])
            client.expunge()
            return {"success": True, "archived_to": archive_folder}
        finally:
            client.logout()

    return await asyncio.to_thread(_archive)


@mcp.tool(description="Move an email from one folder to another.")
async def move_email(
    ctx: Context,
    uid: int,
    to_folder: str,
    from_folder: str = "INBOX",
    account_id: Optional[str] = None,
) -> dict:
    accounts = ctx.lifespan_context["accounts"]
    acc = resolve_account(accounts, account_id)

    def _move():
        client = get_imap_client(acc)
        try:
            client.select_folder(from_folder)
            client.copy([uid], to_folder)
            client.delete_messages([uid])
            client.expunge()
            return {"success": True, "moved_to": to_folder}
        finally:
            client.logout()

    return await asyncio.to_thread(_move)


@mcp.tool(description="Mark an email as read, unread, flagged, or unflagged.")
async def mark_email(
    ctx: Context,
    uid: int,
    action: str,
    folder: str = "INBOX",
    account_id: Optional[str] = None,
) -> dict:
    accounts = ctx.lifespan_context["accounts"]
    acc = resolve_account(accounts, account_id)

    flag_map = {
        "read": (b"\\Seen", "add"),
        "unread": (b"\\Seen", "remove"),
        "flagged": (b"\\Flagged", "add"),
        "unflagged": (b"\\Flagged", "remove"),
    }
    if action not in flag_map:
        return {
            "error": f"Invalid action: {action}. Use: read, unread, flagged, unflagged"
        }

    flag, op = flag_map[action]

    def _mark():
        client = get_imap_client(acc)
        try:
            client.select_folder(folder)
            if op == "add":
                client.add_flags([uid], [flag])
            else:
                client.remove_flags([uid], [flag])
            return {"success": True}
        finally:
            client.logout()

    return await asyncio.to_thread(_mark)


@mcp.tool(description="List all IMAP folders for an account.")
async def list_folders(
    ctx: Context,
    account_id: Optional[str] = None,
) -> list[dict]:
    accounts = ctx.lifespan_context["accounts"]
    acc = resolve_account(accounts, account_id)

    def _list():
        client = get_imap_client(acc)
        try:
            folders = client.list_folders()
            return [
                {
                    "name": name,
                    "flags": [f.decode(errors="replace") for f in flags],
                    "delimiter": delim.decode(errors="replace") if delim else "/",
                }
                for flags, delim, name in folders
            ]
        finally:
            client.logout()

    return await asyncio.to_thread(_list)


@mcp.tool(description="Create a new IMAP folder.")
async def create_folder(
    ctx: Context,
    name: str,
    account_id: Optional[str] = None,
) -> dict:
    accounts = ctx.lifespan_context["accounts"]
    acc = resolve_account(accounts, account_id)

    def _create():
        client = get_imap_client(acc)
        try:
            client.create_folder(name)
            return {"success": True}
        finally:
            client.logout()

    return await asyncio.to_thread(_create)


@mcp.tool(description="Rename an existing IMAP folder.")
async def rename_folder(
    ctx: Context,
    old_name: str,
    new_name: str,
    account_id: Optional[str] = None,
) -> dict:
    accounts = ctx.lifespan_context["accounts"]
    acc = resolve_account(accounts, account_id)

    def _rename():
        client = get_imap_client(acc)
        try:
            client.rename_folder(old_name, new_name)
            return {"success": True}
        finally:
            client.logout()

    return await asyncio.to_thread(_rename)


@mcp.tool(
    description="Delete an IMAP folder. Refuses to delete INBOX or system folders."
)
async def delete_folder(
    ctx: Context,
    name: str,
    account_id: Optional[str] = None,
) -> dict:
    protected = {
        "INBOX",
        "[Gmail]",
        "[Gmail]/All Mail",
        "[Gmail]/Trash",
        "[Gmail]/Spam",
        "[Gmail]/Drafts",
        "[Gmail]/Sent Mail",
    }
    if name in protected:
        return {"error": f"Cannot delete protected folder: {name}"}

    accounts = ctx.lifespan_context["accounts"]
    acc = resolve_account(accounts, account_id)

    def _delete():
        client = get_imap_client(acc)
        try:
            client.delete_folder(name)
            return {"success": True}
        finally:
            client.logout()

    return await asyncio.to_thread(_delete)


@mcp.tool(description="Get server information and account connection status.")
async def get_server_info(ctx: Context) -> dict:
    accounts = ctx.lifespan_context["accounts"]
    webhook_url = ctx.lifespan_context["webhook_url"]

    account_info = []
    for acc in accounts:
        status = "unknown"
        try:
            client = await asyncio.to_thread(get_imap_client, acc)
            await asyncio.to_thread(client.logout)
            status = "connected"
        except Exception as e:
            status = f"error: {e}"
        account_info.append(
            {
                "id": acc["id"],
                "imap_host": acc["imap_host"],
                "smtp_host": acc["smtp_host"],
                "watch_folders": acc.get("watch_folders", []),
                "status": status,
            }
        )

    return {
        "server_name": "poke-mail",
        "version": "1.0.0",
        "accounts": account_info,
        "webhook_url": webhook_url,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    host = "0.0.0.0"
    logger.info("Starting poke-mail on %s:%d", host, port)
    mcp.run(transport="http", host=host, port=port, stateless_http=True)
