"""Direct Member Add Manager for SamiBOT.

Uses the authenticated Telethon user session to invite eligible source-group
members one at a time. Telegram remains authoritative: users whose privacy
settings do not permit the invite are skipped. Jobs are rate-limited and
support pause, resume, cancel, and progress reporting.
"""
import asyncio
import html
import sqlite3
import time
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telethon import functions, types
from telethon.errors import FloodWaitError, RPCError

DB_PATH = "bot_data.db"
MAX_TARGET = 500
INVITE_DELAY_SECONDS = 3
MAX_SOURCE_SCAN = 20000


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS member_add_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by INTEGER NOT NULL,
            source_group TEXT NOT NULL,
            destination_group TEXT NOT NULL,
            target_count INTEGER NOT NULL,
            attempted_count INTEGER NOT NULL DEFAULT 0,
            added_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            last_error TEXT NOT NULL DEFAULT '',
            report_chat_id INTEGER,
            report_message_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            floodwait_until REAL NOT NULL DEFAULT 0
        )
    """)
    # Safe schema migration for existing installations.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(member_add_jobs)")}
    if "floodwait_until" not in columns:
        conn.execute("ALTER TABLE member_add_jobs ADD COLUMN floodwait_until REAL NOT NULL DEFAULT 0")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS member_add_processed (
            job_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            PRIMARY KEY (job_id, user_id)
        )
    """)
    conn.commit()
    return conn


def _esc(value):
    return html.escape(str(value or ""))


def _keyboard(job_id, status):
    rows = []
    if status == "active":
        rows.append([InlineKeyboardButton("⏸ Pause", callback_data=f"ma_pause|{job_id}")])
    elif status == "paused":
        rows.append([InlineKeyboardButton("▶️ Resume", callback_data=f"ma_resume|{job_id}")])
    if status in ("active", "paused"):
        rows.append([InlineKeyboardButton("🛑 Cancel", callback_data=f"ma_cancel|{job_id}")])
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"ma_refresh|{job_id}")])
    return InlineKeyboardMarkup(rows)


def _job_text(job):
    target = max(1, int(job["target_count"]))
    return (
        "👥 <b>DIRECT MEMBER ADD</b>\n\n"
        f"📤 Source: <code>{_esc(job['source_group'])}</code>\n"
        f"📥 Destination: <code>{_esc(job['destination_group'])}</code>\n"
        f"🎯 Target additions: <b>{job['target_count']}</b>\n"
        f"✅ Added: <b>{job['added_count']}</b>\n"
        f"⏭️ Skipped/failed: <b>{job['skipped_count']}</b>\n"
        f"🔎 Checked: <b>{job['attempted_count']}</b>\n"
        f"📊 Target progress: <b>{min(100, int(job['added_count'] * 100 / target))}%</b>\n"
        f"🔔 Status: <b>{_esc(job['status']).upper()}</b>\n"
        + (f"⏳ Telegram wait remaining: <b>{max(0, int(float(job['floodwait_until'] or 0) - time.time()))}s</b>\n" if float(job["floodwait_until"] or 0) > time.time() else "")
        + (f"⚠️ Last issue: <code>{_esc(job['last_error'])}</code>\n" if job["last_error"] else "")
        + "\nTelegram may reject users due to privacy settings, membership limits, or group permissions. "
          "Those users are skipped; SamiBOT does not bypass these restrictions."
    )


def _get_job(job_id):
    conn = _connect()
    job = conn.execute("SELECT * FROM member_add_jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return job


def _set_job(job_id, **values):
    if not values:
        return
    conn = _connect()
    fields = list(values)
    assignments = ", ".join(f"{field}=?" for field in fields)
    conn.execute(
        f"UPDATE member_add_jobs SET {assignments}, updated_at=? WHERE id=?",
        [values[field] for field in fields] + [_now(), job_id],
    )
    conn.commit()
    conn.close()


async def _report(job_id, bot, force=False):
    job = _get_job(job_id)
    if not job:
        return
    try:
        await bot.edit_message_text(
            chat_id=job["report_chat_id"],
            message_id=job["report_message_id"],
            text=_job_text(job),
            parse_mode="HTML",
            reply_markup=_keyboard(job_id, job["status"]),
        )
    except Exception:
        # Message may have been deleted; progress remains persisted in SQLite.
        pass


def _is_processed(job_id, user_id):
    conn = _connect()
    row = conn.execute(
        "SELECT 1 FROM member_add_processed WHERE job_id=? AND user_id=?",
        (job_id, user_id),
    ).fetchone()
    conn.close()
    return row is not None


def _mark_processed(job_id, user_id, outcome, error=""):
    """Persist each user outcome atomically so restarts do not repeat completed work."""
    conn = _connect()
    cur = conn.execute(
        "INSERT OR IGNORE INTO member_add_processed (job_id, user_id, outcome) VALUES (?, ?, ?)",
        (job_id, user_id, outcome),
    )
    if cur.rowcount:
        if outcome == "added":
            conn.execute(
                "UPDATE member_add_jobs SET added_count=added_count+1, attempted_count=attempted_count+1, "
                "last_error=?, updated_at=? WHERE id=?",
                (error, _now(), job_id),
            )
        else:
            conn.execute(
                "UPDATE member_add_jobs SET skipped_count=skipped_count+1, attempted_count=attempted_count+1, "
                "last_error=?, updated_at=? WHERE id=?",
                (error, _now(), job_id),
            )
    conn.commit()
    conn.close()
    return bool(cur.rowcount)


async def _wait_for_floodwait(job_id, bot):
    """Wait until Telegram's deadline, updating the countdown and honoring Cancel."""
    last_reported = None
    while True:
        job = _get_job(job_id)
        if not job or job["status"] == "cancelled":
            return False
        deadline = float(job["floodwait_until"] or 0)
        remaining = max(0, int(deadline - time.time()))
        if remaining <= 0:
            _set_job(job_id, floodwait_until=0, last_error="")
            await _report(job_id, bot, True)
            return True
        message = f"Telegram FloodWait: waiting {remaining}s before continuing"
        if message != last_reported:
            _set_job(job_id, last_error=message)
            await _report(job_id, bot, True)
            last_reported = message
        await asyncio.sleep(min(5, max(1, remaining)))


async def _run_job(job_id, client, bot):
    job = _get_job(job_id)
    if not job or job["status"] not in ("active", "paused"):
        return
    try:
        # If Render restarted during a Telegram wait, honor the saved deadline.
        if float(job["floodwait_until"] or 0) > time.time():
            if not await _wait_for_floodwait(job_id, bot):
                await _report(job_id, bot, True)
                return

        source = await client.get_entity(job["source_group"])
        destination = await client.get_entity(job["destination_group"])
        scanned = 0
        async for participant in client.iter_participants(source):
            current = _get_job(job_id)
            if not current or current["status"] in ("cancelled", "completed", "failed"):
                break
            while current and current["status"] == "paused":
                await asyncio.sleep(2)
                current = _get_job(job_id)
            if not current or current["status"] in ("cancelled", "completed", "failed"):
                break
            if current["status"] != "active":
                break
            if int(current["added_count"]) >= int(current["target_count"]):
                _set_job(job_id, status="completed", last_error="")
                break

            scanned += 1
            if scanned > MAX_SOURCE_SCAN:
                _set_job(job_id, status="completed", last_error="Source scan limit reached")
                break
            if _is_processed(job_id, participant.id):
                continue

            while True:
                current = _get_job(job_id)
                if not current or current["status"] in ("cancelled", "completed", "failed"):
                    break
                while current and current["status"] == "paused":
                    await asyncio.sleep(2)
                    current = _get_job(job_id)
                if not current or current["status"] in ("cancelled", "completed", "failed"):
                    break
                if current["status"] != "active":
                    break
                if int(current["added_count"]) >= int(current["target_count"]):
                    _set_job(job_id, status="completed", last_error="")
                    break
                try:
                    await client(functions.channels.InviteToChannelRequest(
                        channel=destination,
                        users=[types.InputUser(user_id=participant.id, access_hash=participant.access_hash)],
                    ))
                    _mark_processed(job_id, participant.id, "added")
                    break
                except FloodWaitError as exc:
                    wait_seconds = max(1, int(getattr(exc, "seconds", 30)))
                    current_deadline = time.time() + wait_seconds
                    _set_job(
                        job_id,
                        floodwait_until=current_deadline,
                        last_error=f"Telegram FloodWait: waiting {wait_seconds}s before continuing",
                    )
                    await _report(job_id, bot, True)
                    if not await _wait_for_floodwait(job_id, bot):
                        break
                    # Retry this same eligible member only after Telegram's full wait.
                    continue
                except Exception as exc:
                    _mark_processed(job_id, participant.id, "skipped", type(exc).__name__)
                    break

            latest = _get_job(job_id)
            if not latest or latest["status"] in ("cancelled", "completed", "failed"):
                break
            if int(latest["attempted_count"]) % 5 == 0 or int(latest["added_count"]) >= int(latest["target_count"]):
                await _report(job_id, bot)
            await asyncio.sleep(INVITE_DELAY_SECONDS)

        latest = _get_job(job_id)
        if latest and latest["status"] == "active":
            final_status = "completed"
            if int(latest["added_count"]) < int(latest["target_count"]) and not latest["last_error"]:
                _set_job(job_id, status=final_status, last_error="Source members exhausted before reaching target")
            else:
                _set_job(job_id, status=final_status)
    except Exception as exc:
        latest = _get_job(job_id)
        if latest and latest["status"] not in ("cancelled", "completed"):
            _set_job(job_id, status="failed", last_error=f"{type(exc).__name__}: {str(exc)[:120]}")
    await _report(job_id, bot, True)


def resume_member_add_jobs(application, client, bot):
    """Restart persisted active jobs after the Telegram clients are initialized."""
    if not client or not client.is_connected():
        print("Member-add recovery skipped: Telethon client is not connected.")
        return 0
    conn = _connect()
    jobs = conn.execute(
        "SELECT id FROM member_add_jobs WHERE status='active' ORDER BY id"
    ).fetchall()
    conn.close()
    for row in jobs:
        job_id = int(row["id"])
        application.create_task(_run_job(job_id, client, bot), name=f"member-add-recovery-{job_id}")
    if jobs:
        print(f"Recovered {len(jobs)} active member-add job(s).")
    return len(jobs)

async def member_add_start(update, context, is_admin):
    user = update.effective_user
    query = update.callback_query
    if not user or not is_admin(user.id):
        if query:
            await query.answer("Admin only.", show_alert=True)
        elif update.effective_message:
            await update.effective_message.reply_text("🔒 Admin only.")
        return
    context.user_data["state"] = "ma_source"
    context.user_data["ma_draft"] = {}
    await query.edit_message_text(
        "👥 <b>DIRECT MEMBER ADD</b>\n\n"
        "Step 1/3 — Send the source group's public @username or t.me link. "
        "SamiBOT will check source members and let Telegram enforce each user's invite privacy.\n\n"
        "Send /cancel to stop.",
        parse_mode="HTML",
    )


async def member_add_callback(update, context, data, is_admin, bot, telethon_client=None):
    query = update.callback_query
    user = update.effective_user
    if not user or not is_admin(user.id):
        await query.answer("Admin only.", show_alert=True)
        return True
    parts = data.split("|", 1)
    action = parts[0]
    if action == "member_add":
        await member_add_start(update, context, is_admin)
        return True
    if action == "ma_cancel_setup":
        await member_add_cancel_setup(update, context, is_admin)
        return True
    if action == "ma_confirm":
        draft = context.user_data.get("ma_draft") or {}
        if context.user_data.get("state") != "ma_confirm" or not all(
            draft.get(key) for key in ("source_group", "destination_group", "target_count")
        ):
            await query.edit_message_text("❌ Setup expired. Open Member Add and try again.")
            return True
        if telethon_client is None or not telethon_client.is_connected():
            await query.edit_message_text("❌ SamiBOT's Telegram user session is not connected. Check Render logs and STRING_SESSION.")
            return True
        try:
            source = await telethon_client.get_entity(draft["source_group"])
            destination = await telethon_client.get_entity(draft["destination_group"])
            if not isinstance(destination, types.Channel) or not getattr(destination, "megagroup", False):
                await query.edit_message_text("❌ Destination must be a supergroup, not a broadcast channel.")
                return True
        except Exception as exc:
            await query.edit_message_text(f"❌ Could not validate groups: {_esc(type(exc).__name__)}")
            return True
        conn = _connect()
        cur = conn.execute(
            "INSERT INTO member_add_jobs (created_by, source_group, destination_group, target_count, status, report_chat_id, report_message_id, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)",
            (user.id, draft["source_group"], draft["destination_group"], int(draft["target_count"]),
             query.message.chat_id, query.message.message_id, _now(), _now()),
        )
        job_id = cur.lastrowid
        conn.commit()
        conn.close()
        context.user_data["state"] = None
        context.user_data.pop("ma_draft", None)
        await query.edit_message_text(
            _job_text(_get_job(job_id)),
            parse_mode="HTML",
            reply_markup=_keyboard(job_id, "active"),
        )
        context.application.create_task(_run_job(job_id, telethon_client, bot), name=f"member-add-{job_id}")
        return True
    if action in ("ma_pause", "ma_resume", "ma_cancel", "ma_refresh"):
        try:
            job_id = int(parts[1])
        except (IndexError, ValueError):
            await query.answer("Invalid job.", show_alert=True)
            return True
        job = _get_job(job_id)
        if not job:
            await query.answer("Job not found.", show_alert=True)
            return True
        if int(job["created_by"]) != user.id and not is_admin(user.id):
            await query.answer("Not authorized.", show_alert=True)
            return True
        status = job["status"]
        if action == "ma_pause" and status == "active":
            status = "paused"
        elif action == "ma_resume" and status == "paused":
            status = "active"
        elif action == "ma_cancel" and status in ("active", "paused"):
            status = "cancelled"
        _set_job(job_id, status=status)
        await query.edit_message_text(
            _job_text(_get_job(job_id)),
            parse_mode="HTML",
            reply_markup=_keyboard(job_id, status),
        )
        return True
    return False


async def member_add_text(update, context, telethon_client, is_admin):
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not is_admin(user.id):
        if message:
            await message.reply_text("🔒 Member Add Manager is admin-only.")
        return True
    state = context.user_data.get("state")
    if state not in ("ma_source", "ma_destination", "ma_quantity", "ma_confirm"):
        return False
    value = (message.text or "").strip()
    if value.lower() == "/cancel":
        context.user_data["state"] = None
        context.user_data.pop("ma_draft", None)
        await message.reply_text("❎ Member Add setup cancelled.")
        return True
    draft = context.user_data.setdefault("ma_draft", {})
    if state == "ma_source":
        try:
            await telethon_client.get_entity(value)
            draft["source_group"] = value
            context.user_data["state"] = "ma_destination"
            await message.reply_text("Step 2/3 — Send the destination supergroup's public @username or numeric ID.")
        except Exception as exc:
            await message.reply_text(f"❌ Cannot access source group ({type(exc).__name__}). Check the link and try again.")
        return True
    if state == "ma_destination":
        try:
            entity = await telethon_client.get_entity(value)
            if not isinstance(entity, types.Channel) or not getattr(entity, "megagroup", False):
                await message.reply_text("❌ Destination must be a supergroup.")
                return True
            draft["destination_group"] = value
            context.user_data["state"] = "ma_quantity"
            await message.reply_text(f"Step 3/3 — How many members should SamiBOT attempt to add? Enter 1–{MAX_TARGET}.")
        except Exception:
            await message.reply_text("❌ Cannot access destination group. Send a valid @username, t.me link, or numeric ID.")
        return True
    if state == "ma_quantity":
        if not value.isdigit() or not 1 <= int(value) <= MAX_TARGET:
            await message.reply_text(f"❌ Enter a whole number from 1 to {MAX_TARGET}.")
            return True
        draft["target_count"] = int(value)
        context.user_data["state"] = "ma_confirm"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm direct-add campaign", callback_data="ma_confirm")],
            [InlineKeyboardButton("❎ Cancel", callback_data="ma_cancel_setup")],
        ])
        await message.reply_text(
            "🧾 <b>Review direct-add campaign</b>\n\n"
            f"📤 Source: <code>{_esc(draft['source_group'])}</code>\n"
            f"📥 Destination: <code>{_esc(draft['destination_group'])}</code>\n"
            f"🎯 Target: <b>{draft['target_count']}</b> successful additions\n\n"
            "SamiBOT uses the logged-in Telegram user session. Telegram will reject users whose privacy settings "
            "or account/group limits prevent direct addition. The bot will not retry rejected users or bypass those restrictions.",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return True
    return True


async def member_add_join_request(update, context):
    # Direct-add campaigns do not use join requests.
    return


async def member_add_cancel_setup(update, context, is_admin):
    user = update.effective_user
    query = update.callback_query
    if not user or not is_admin(user.id):
        await query.answer("Admin only.", show_alert=True)
        return True
    context.user_data["state"] = None
    context.user_data.pop("ma_draft", None)
    await query.edit_message_text("❎ Member Add setup cancelled.")
    return True
