"""Consent-based Member Add Manager for SamiBOT.

This feature never scrapes a source group's member list or bulk-adds people.
It uses destination join-request links so users actively request to join.
"""
import sqlite3
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


DB_PATH = "bot_data.db"
MAX_TARGET = 5000


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS member_invite_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by INTEGER NOT NULL,
            source_group TEXT NOT NULL,
            destination_group TEXT NOT NULL,
            target_count INTEGER NOT NULL,
            approved_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            invite_link TEXT NOT NULL,
            bot_created_link INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _keyboard(job_id, status):
    rows = []
    if status == "active":
        rows.append([InlineKeyboardButton("⏸ Pause", callback_data=f"ma_pause|{job_id}")])
    elif status == "paused":
        rows.append([InlineKeyboardButton("▶️ Resume", callback_data=f"ma_resume|{job_id}")])
    if status in ("active", "paused"):
        rows.append([InlineKeyboardButton("🛑 Cancel campaign", callback_data=f"ma_cancel|{job_id}")])
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"ma_refresh|{job_id}")])
    return InlineKeyboardMarkup(rows)


def _job_text(job):
    return (
        "👥 <b>MEMBER ADD MANAGER</b>\n\n"
        f"📤 Source group: <code>{_esc(job['source_group'])}</code>\n"
        f"📥 Destination: <code>{_esc(job['destination_group'])}</code>\n"
        f"🎯 Requested joins: <b>{job['target_count']}</b>\n"
        f"✅ Approved join requests: <b>{job['approved_count']}</b>\n"
        f"📊 Progress: <b>{min(100, int(job['approved_count'] * 100 / max(1, job['target_count'])))}%</b>\n"
        f"🔔 Status: <b>{job['status'].upper()}</b>\n\n"
        "People join only after they open the invite and submit a join request. "
        "No one is added without their action."
    )


def _esc(value):
    import html
    return html.escape(str(value or ""))


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
        "👥 <b>MEMBER ADD MANAGER</b>\n\n"
        "Step 1/3 — Send the source group's public @username or t.me link. "
        "SamiBOT will validate the group but will not scrape its member list.\n\n"
        "Send /cancel to stop.",
        parse_mode="HTML",
    )


async def member_add_callback(update, context, data, is_admin, bot):
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
    if action == "ma_confirm":
        draft = context.user_data.get("ma_draft") or {}
        if context.user_data.get("state") != "ma_confirm" or not all(
            draft.get(k) for k in ("source_group", "destination_group", "target_count")
        ):
            await query.edit_message_text("❌ Setup expired. Open Member Add Manager and try again.")
            return True
        dest = draft["destination_group"]
        try:
            invite = await bot.create_chat_invite_link(
                chat_id=dest,
                creates_join_request=True,
                name=f"SamiBOT consent job {user.id}",
            )
            link = invite.invite_link
            bot_created = 1
        except Exception as exc:
            context.user_data["state"] = "ma_invite_link"
            context.user_data["ma_last_error"] = type(exc).__name__
            await query.edit_message_text(
                "⚠️ SamiBOT cannot create a join-request link for that destination.\n\n"
                "Please make sure the destination group is a supergroup and either grant the bot "
                "<code>Invite Users</code> permission, or send an existing destination invite link "
                "that you control. I will not try to bypass Telegram permissions.\n\n"
                "Send /cancel to stop.",
                parse_mode="HTML",
            )
            return True
        now = datetime.now(timezone.utc).isoformat()
        conn = _connect()
        cur = conn.execute(
            "INSERT INTO member_invite_jobs "
            "(created_by, source_group, destination_group, target_count, approved_count, status, invite_link, bot_created_link, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0, 'active', ?, ?, ?, ?)",
            (user.id, draft["source_group"], dest, int(draft["target_count"]), link, bot_created, now, now),
        )
        job_id = cur.lastrowid
        conn.commit()
        job = conn.execute("SELECT * FROM member_invite_jobs WHERE id=?", (job_id,)).fetchone()
        conn.close()
        context.user_data["state"] = None
        context.user_data.pop("ma_draft", None)
        await query.edit_message_text(
            _job_text(job) + f"\n\n🔗 <b>Join-request link</b>\n{_esc(link)}",
            parse_mode="HTML",
            reply_markup=_keyboard(job_id, "active"),
            disable_web_page_preview=True,
        )
        return True
    if action == "ma_pause" or action == "ma_resume" or action == "ma_cancel" or action == "ma_refresh":
        try:
            job_id = int(parts[1])
        except (IndexError, ValueError):
            await query.answer("Invalid job.", show_alert=True)
            return True
        conn = _connect()
        job = conn.execute("SELECT * FROM member_invite_jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            conn.close()
            await query.answer("Job not found.", show_alert=True)
            return True
        if int(job["created_by"]) != user.id and not is_admin(user.id):
            conn.close()
            await query.answer("Not authorized.", show_alert=True)
            return True
        status = job["status"]
        if action == "ma_pause" and status == "active":
            status = "paused"
        elif action == "ma_resume" and status == "paused":
            status = "active"
        elif action == "ma_cancel" and status in ("active", "paused"):
            status = "cancelled"
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE member_invite_jobs SET status=?, updated_at=? WHERE id=?", (status, now, job_id))
        conn.commit()
        job = conn.execute("SELECT * FROM member_invite_jobs WHERE id=?", (job_id,)).fetchone()
        conn.close()
        if action == "ma_cancel" and job["bot_created_link"]:
            try:
                await bot.revoke_chat_invite_link(job["destination_group"], job["invite_link"])
            except Exception:
                pass
        await query.edit_message_text(
            _job_text(job) + f"\n\n🔗 <b>Invite link</b>\n{_esc(job['invite_link'])}",
            parse_mode="HTML",
            reply_markup=_keyboard(job_id, status),
            disable_web_page_preview=True,
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
    if state not in ("ma_source", "ma_destination", "ma_quantity", "ma_confirm", "ma_invite_link"):
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
            entity = await telethon_client.get_entity(value)
            if not (getattr(entity, "megagroup", False) or getattr(entity, "broadcast", False) or hasattr(entity, "participants_count")):
                await message.reply_text("❌ That does not look like a Telegram group/channel. Send its @username or link.")
                return True
            draft["source_group"] = value
            context.user_data["state"] = "ma_destination"
            await message.reply_text(
                "Step 2/3 — Send the destination group's public @username or numeric chat ID.\n\n"
                "The destination should be a supergroup to support join requests. The bot may need Invite Users permission."
            )
        except Exception as exc:
            await message.reply_text(f"❌ I could not access that source group ({type(exc).__name__}). Check the link and try again.")
        return True
    if state == "ma_destination":
        try:
            await telethon_client.get_entity(value)
            draft["destination_group"] = value
            context.user_data["state"] = "ma_quantity"
            await message.reply_text(f"Step 3/3 — How many consenting members should join {value}? Send a number from 1 to {MAX_TARGET}.")
        except Exception:
            await message.reply_text("❌ I could not access that destination. Send a valid @username, group link, or numeric ID.")
        return True
    if state == "ma_quantity":
        if not value.isdigit() or not 1 <= int(value) <= MAX_TARGET:
            await message.reply_text(f"❌ Enter a whole number from 1 to {MAX_TARGET}.")
            return True
        draft["target_count"] = int(value)
        context.user_data["state"] = "ma_confirm"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm consent-based campaign", callback_data="ma_confirm")],
            [InlineKeyboardButton("❎ Cancel", callback_data="ma_cancel_setup")],
        ])
        await message.reply_text(
            "🧾 <b>Review campaign</b>\n\n"
            f"📤 Source: <code>{_esc(draft['source_group'])}</code>\n"
            f"📥 Destination: <code>{_esc(draft['destination_group'])}</code>\n"
            f"🎯 Target: <b>{draft['target_count']}</b> approved join requests\n\n"
            "Confirm only if members choose to join themselves. This tool will not read/scrape the source member list "
            "or directly add users. The target is a cap on approved join requests.",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return True
    if state == "ma_invite_link":
        if not value.startswith(("https://t.me/", "http://t.me/")):
            await message.reply_text("❌ Send a valid Telegram invite link beginning with https://t.me/ or /cancel.")
            return True
        now = datetime.now(timezone.utc).isoformat()
        conn = _connect()
        cur = conn.execute(
            "INSERT INTO member_invite_jobs "
            "(created_by, source_group, destination_group, target_count, approved_count, status, invite_link, bot_created_link, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0, 'active', ?, 0, ?, ?)",
            (user.id, draft["source_group"], draft["destination_group"], int(draft["target_count"]), value, now, now),
        )
        job_id = cur.lastrowid
        conn.commit()
        job = conn.execute("SELECT * FROM member_invite_jobs WHERE id=?", (job_id,)).fetchone()
        conn.close()
        context.user_data["state"] = None
        context.user_data.pop("ma_draft", None)
        await message.reply_text(
            _job_text(job) + "\n\n⚠️ This is an admin-supplied link; SamiBOT cannot verify join requests or automatically count members unless it has destination admin permissions.",
            parse_mode="HTML",
            reply_markup=_keyboard(job_id, "active"),
        )
        return True
    return True


async def member_add_join_request(update, context):
    request = update.chat_join_request
    if not request:
        return
    link = getattr(getattr(request, "invite_link", None), "invite_link", None)
    if not link:
        return
    conn = _connect()
    job = conn.execute(
        "SELECT * FROM member_invite_jobs WHERE invite_link=? ORDER BY id DESC LIMIT 1",
        (link,),
    ).fetchone()
    if not job:
        conn.close()
        return
    if job["status"] == "paused":
        # Do not approve while paused. Decline so the person can submit again after resume.
        conn.close()
        try:
            await context.bot.decline_chat_join_request(request.chat.id, request.from_user.id)
        except Exception as exc:
            print(f"[MemberAdd] Could not decline paused join request: {type(exc).__name__}")
        return
    if job["status"] != "active" or int(job["approved_count"]) >= int(job["target_count"]):
        conn.close()
        try:
            await context.bot.decline_chat_join_request(request.chat.id, request.from_user.id)
        except Exception as exc:
            print(f"[MemberAdd] Could not decline cancelled/completed join request: {type(exc).__name__}")
        return
    try:
        await context.bot.approve_chat_join_request(request.chat.id, request.from_user.id)
    except Exception as exc:
        conn.close()
        print(f"[MemberAdd] Join request approval failed: {type(exc).__name__}: {exc}")
        return
    approved = int(job["approved_count"]) + 1
    status = "completed" if approved >= int(job["target_count"]) else "active"
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE member_invite_jobs SET approved_count=?, status=?, updated_at=? WHERE id=?",
        (approved, status, now, int(job["id"])),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM member_invite_jobs WHERE id=?", (int(job["id"]),)).fetchone()
    conn.close()
    if status == "completed" and job["bot_created_link"]:
        try:
            await context.bot.revoke_chat_invite_link(request.chat.id, link)
        except Exception:
            pass
    try:
        await context.bot.send_message(
            chat_id=int(job["created_by"]),
            text=_job_text(updated),
            parse_mode="HTML",
            reply_markup=_keyboard(int(job["id"]), status),
        )
    except Exception as exc:
        print(f"[MemberAdd] Progress notification failed: {type(exc).__name__}")


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
