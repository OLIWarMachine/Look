import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import io
import datetime
import asyncio
from dotenv import load_dotenv

# Load environment variables from a .env file if present
load_dotenv()

# --- CONFIGURATION ---
ROLES_IA = [1423566596496035882]   # Base IA
ROLES_MGT = [1514164027088044112]  # MGT
ROLES_SHR = [1423564797185884199]  # SHR
ROLES_LS = [1519003718060605440]   # LS

PANEL_ACCESS = {
    "general": ROLES_IA + ROLES_MGT + ROLES_SHR + ROLES_LS,
    "appeal":  ROLES_IA + ROLES_MGT + ROLES_SHR + ROLES_LS,
    "report":  ROLES_IA + ROLES_MGT + ROLES_SHR + ROLES_LS,
    "board":   ROLES_SHR + ROLES_LS
}

TICKET_ADMIN_ROLES = ROLES_MGT + ROLES_SHR + ROLES_LS

PANEL_CATEGORIES = {
    "general": 1527050651790741614,
    "appeal":  1527201724140355645,
    "report":  1527236437320273921,
    "board":   1527301502576627733
}

TRANSCRIPT_CHANNEL_ID = 1519756472135716894
ALLOWED_COMMAND_GUILDS = [1423564132539564052]
OWNER_USER_ID = 1155479443272384532 
ALERT_CHANNEL_ID = 1528676955166216203

# --- DATA STORAGE ---
DATA_FILE = "tickets_data.json"
CACHE_FILE = "role_perm_cache.json"

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"ticket_count": 0, "blacklisted": []}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

# --- FILE-BASED PERMISSION CACHE MANAGEMENT ---

def load_perm_cache() -> dict:
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_perm_cache(cache_data: dict):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache_data, f, indent=4)

def apply_role_permissions(overwrites: dict, guild: discord.Guild, role_ids: list, read: bool = True, send: bool = True):
    for role_id in role_ids:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(read_messages=read, send_messages=send)

def is_ticket_channel(channel: discord.TextChannel) -> bool:
    """Checks if the channel belongs to one of the designated ticket category IDs."""
    if channel.category_id in PANEL_CATEGORIES.values():
        return True
    return False

def is_allowed_guild():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild_id in ALLOWED_COMMAND_GUILDS or is_ticket_channel(interaction.channel):
            return True

        embed = discord.Embed(
            title="Command Restricted",
            description="Commands can only be used in designated servers or active ticket channels.",
            color=discord.Color.red()
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False

    return app_commands.check(predicate)

# --- TRANSCRIPT GENERATION & CLOSURE ---

async def generate_transcript(channel: discord.TextChannel, original_name: str) -> discord.File:
    messages = []

    async for msg in channel.history(limit=500, oldest_first=True):
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        content = msg.clean_content or "[No text content]"
        attachments = (
            f" (Attachments: {', '.join([a.url for a in msg.attachments])})"
            if msg.attachments else ""
        )

        messages.append(
            f"[{timestamp}] {msg.author.name} ({msg.author.id}): {content}{attachments}"
        )

    transcript_text = (
        f"=== TRANSCRIPT FOR #{original_name} ===\n"
        + "\n".join(messages)
    )

    buffer = io.BytesIO(transcript_text.encode("utf-8"))

    return discord.File(
        fp=buffer,
        filename=f"transcript-{original_name}.txt"
    )

async def process_ticket_closure(
    channel: discord.TextChannel,
    closed_by: discord.User,
    reason: str = "No reason provided",
    claimed_by_id: int = None
):
    creator = None
    creator_id = None
    panel_type = "Unknown"
    original_name = channel.name

    if channel.topic:
        for part in channel.topic.split("|"):
            part = part.strip()

            if part.startswith("creator_id:"):
                try:
                    creator_id = int(
                        part.replace("creator_id:", "").strip()
                    )
                except ValueError:
                    pass

            elif part.startswith("panel:"):
                panel_type = part.replace(
                    "panel:", ""
                ).strip().capitalize()

            elif part.startswith("orig_name:"):
                original_name = part.replace(
                    "orig_name:", ""
                ).strip()

    if panel_type == "Unknown":
        parts = original_name.split("-")

        if len(parts) >= 2 and parts[0] in PANEL_CATEGORIES:
            panel_type = parts[0].capitalize()

        else:
            for p_name, cat_id in PANEL_CATEGORIES.items():
                if channel.category_id == cat_id:
                    panel_type = p_name.capitalize()
                    break

    if creator_id:
        creator = channel.guild.get_member(creator_id)

        if not creator:
            try:
                creator = await channel.guild.fetch_member(creator_id)
            except Exception:
                try:
                    creator = await channel._state._get_client().fetch_user(creator_id)
                except Exception:
                    pass

    else:
        all_staff_roles = set(
            ROLES_IA + ROLES_MGT + ROLES_SHR + ROLES_LS
        )

        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Member) and target != channel.guild.me:
                if (
                    overwrite.read_messages is True
                    and not any(
                        r.id in all_staff_roles
                        for r in target.roles
                    )
                ):
                    creator = target
                    break

    claimer_text = (
        f"<@{claimed_by_id}>"
        if claimed_by_id
        else "Unclaimed"
    )

    creator_text = (
        creator.mention
        if creator
        else (
            "Unknown User"
            if not creator_id
            else f"<@{creator_id}>"
        )
    )

    embed = discord.Embed(
        title=f"Ticket Closed - #{original_name}",
        color=discord.Color.red(),
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )

    embed.add_field(
        name="Panel",
        value=panel_type,
        inline=True
    )

    embed.add_field(
        name="Opened By",
        value=creator_text,
        inline=True
    )

    embed.add_field(
        name="Claimed By",
        value=claimer_text,
        inline=True
    )

    embed.add_field(
        name="Closed By",
        value=closed_by.mention,
        inline=True
    )

    embed.add_field(
        name="Reason",
        value=reason,
        inline=False
    )

    log_channel = channel.guild.get_channel(
        TRANSCRIPT_CHANNEL_ID
    )

    if log_channel:
        file_log = await generate_transcript(
            channel,
            original_name
        )

        await log_channel.send(
            embed=embed,
            file=file_log
        )

    if creator:
        file_dm = await generate_transcript(
            channel,
            original_name
        )

        dm_embed = embed.copy()

        dm_embed.description = (
            "Your ticket has been closed. "
            "A copy of the transcript is attached below."
        )

        try:
            await creator.send(
                embed=dm_embed,
                file=file_dm
            )
        except (discord.HTTPException, discord.Forbidden):
            pass

    await channel.delete()

# --- INSTRUCTIONS DICTIONARY ---

TICKET_INSTRUCTIONS = {
    "general": {
        "title": "General Enquiry Instructions",
        "description": "Welcome! Please state your enquiry within **15 minutes** or this ticket will be automatically closed."
    },
    "appeal": {
        "title": "Appeal Ticket Instructions",
        "description": (
            "Welcome! If you wish to appeal a moderation action, please fill out the template below.\n\n"
            "**Required Format:**\n"
            "```\n"
            "1. Username / Roblox ID:\n"
            "2. Reason for Moderation Action:\n"
            "3. Why should this action be appealed?:\n"
            "4. Relevant Proof/Screenshots:\n"
            "```"
        )
    },
    "report": {
        "title": "Player & Staff Report Instructions",
        "description": (
            "⚠️ **Notice:** This ticket panel is strictly for reporting **LR and MR staff members or general server members**.\n\n"
            "**Required Format:**\n"
            "```\n"
            "1. Reported User/Staff:\n"
            "2. User's Rank (LR/MR/Member):\n"
            "3. Incident Description:\n"
            "4. Evidence (Video/Image links required):\n"
            "```"
        )
    },
    "board": {
        "title": "Board Report Instructions",
        "description": (
            "⚠️ **Notice:** This ticket panel is strictly reserved for reporting **IA+ staff members**.\n\n"
            "**Required Format:**\n"
            "```\n"
            "1. Reported IA+ Member:\n"
            "2. Alleged Violation/Issue:\n"
            "3. Detailed Statement:\n"
            "4. Supporting Evidence:\n"
            "```"
        )
    }
}

# --- VIEWS & MODALS ---

class CloseReasonModal(discord.ui.Modal, title="Close Ticket with Reason"):
    reason = discord.ui.TextInput(
        label="Reason for closing",
        style=discord.TextStyle.paragraph,
        placeholder="Type the closure reason here...",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Ticket Closing",
            description=(
                f"Ticket is being closed.\n"
                f"**Reason:** {self.reason.value}"
            ),
            color=discord.Color.red()
        )

        await interaction.response.send_message(embed=embed)

        await asyncio.sleep(5)

        view = getattr(self, "view", None)
        claimed_by = view.claimed_by if view else None

        await process_ticket_closure(
            interaction.channel,
            interaction.user,
            self.reason.value,
            claimed_by
        )


class CloseRequestView(discord.ui.View):
    def __init__(
        self,
        requester: discord.User,
        reason: str,
        claimed_by: int = None,
        delay_hours: float = None
    ):
        super().__init__(timeout=None)

        self.requester = requester
        self.reason = reason
        self.claimed_by = claimed_by
        self.delay_hours = delay_hours
        self.auto_close_task = None

    def start_timer(self, channel: discord.TextChannel):
        if self.delay_hours and self.delay_hours > 0:
            self.auto_close_task = asyncio.create_task(
                self._auto_close_delay(channel)
            )

    async def _auto_close_delay(self, channel: discord.TextChannel):
        await asyncio.sleep(
            self.delay_hours * 3600
        )

        embed = discord.Embed(
            title="Ticket Auto-Closed",
            description=(
                f"Ticket closed automatically after "
                f"{self.delay_hours} hour(s) timer expired.\n"
                f"**Reason:** {self.reason}"
            ),
            color=discord.Color.red()
        )

        try:
            await channel.send(embed=embed)

            await asyncio.sleep(5)

            await process_ticket_closure(
                channel,
                self.requester,
                f"Auto-closed after delay timer ({self.reason})",
                self.claimed_by
            )

        except Exception:
            pass

    @discord.ui.button(
        label="Accept Closure",
        style=discord.ButtonStyle.danger,
        custom_id="accept_close_req"
    )
    async def accept_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if self.auto_close_task:
            self.auto_close_task.cancel()

        embed = discord.Embed(
            title="Close Request Accepted",
            description=(
                f"Request accepted by {interaction.user.mention}.\n"
                f"**Reason:** {self.reason}\n\n"
                f"Closing ticket in 5 seconds..."
            ),
            color=discord.Color.red()
        )

        await interaction.response.edit_message(
            embed=embed,
            view=None
        )

        await asyncio.sleep(5)

        await process_ticket_closure(
            interaction.channel,
            self.requester,
            self.reason,
            self.claimed_by
        )

    @discord.ui.button(
        label="Deny",
        style=discord.ButtonStyle.secondary,
        custom_id="deny_close_req"
    )
    async def deny_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if self.auto_close_task:
            self.auto_close_task.cancel()

        embed = discord.Embed(
            title="Close Request Denied",
            description=(
                f"Request denied by "
                f"{interaction.user.mention}."
            ),
            color=discord.Color.orange()
        )

        await interaction.response.edit_message(
            embed=embed,
            view=None
        )

        self.stop()


class TicketControlView(discord.ui.View):
    def __init__(
        self,
        creator_id: int = None,
        claimed_by: int = None
    ):
        super().__init__(timeout=None)

        self.creator_id = creator_id
        self.claimed_by = claimed_by

    @discord.ui.button(
        label="Claim",
        style=discord.ButtonStyle.success,
        custom_id="ticket_claim"
    )
    async def claim_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        user_role_ids = [
            role.id for role in interaction.user.roles
        ]

        all_staff_roles = set(
            ROLES_IA + ROLES_MGT + ROLES_SHR + ROLES_LS
        )

        is_staff = any(
            role_id in all_staff_roles
            for role_id in user_role_ids
        )

        if not is_staff:
            embed = discord.Embed(
                title="Access Denied",
                description=(
                    "Only authorized staff members can "
                    "claim or unclaim tickets."
                ),
                color=discord.Color.red()
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

            return

        target_creator = self.creator_id

        if target_creator is None and interaction.channel.topic:
            for part in interaction.channel.topic.split("|"):
                if part.strip().startswith("creator_id:"):
                    try:
                        target_creator = int(
                            part.strip().replace(
                                "creator_id:",
                                ""
                            )
                        )
                    except ValueError:
                        pass

        if interaction.user.id == target_creator:
            embed = discord.Embed(
                title="Action Denied",
                description=(
                    "You cannot claim a ticket "
                    "that you created."
                ),
                color=discord.Color.red()
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

            return

        if self.claimed_by is not None:
            has_admin_role = any(
                role_id in user_role_ids
                for role_id in TICKET_ADMIN_ROLES
            )

            if (
                interaction.user.id == self.claimed_by
                or has_admin_role
            ):
                prev_claimer = self.claimed_by

                self.claimed_by = None

                button.disabled = False
                button.label = "Claim"
                button.style = discord.ButtonStyle.success

                await interaction.message.edit(
                    view=self
                )

                embed = discord.Embed(
                    description=(
                        f"Ticket unclaimed by "
                        f"{interaction.user.mention} "
                        f"(Previously claimed by "
                        f"<@{prev_claimer}>)."
                    ),
                    color=discord.Color.orange()
                )

                await interaction.response.send_message(
                    embed=embed
                )

                return

            else:
                embed = discord.Embed(
                    description=(
                        f"This ticket is currently "
                        f"claimed by <@{self.claimed_by}>."
                    ),
                    color=discord.Color.red()
                )

                await interaction.response.send_message(
                    embed=embed,
                    ephemeral=True
                )

                return

        self.claimed_by = interaction.user.id

        button.label = (
            f"Claimed by "
            f"{interaction.user.display_name} "
            f"(Click to Unclaim)"
        )

        button.style = discord.ButtonStyle.secondary

        await interaction.message.edit(
            view=self
        )

        embed = discord.Embed(
            description=(
                f"Ticket claimed by "
                f"{interaction.user.mention}."
            ),
            color=discord.Color.green()
        )

        await interaction.response.send_message(
            embed=embed
        )

    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_close"
    )
    async def close_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        embed = discord.Embed(
            description="Closing ticket in 5 seconds...",
            color=discord.Color.red()
        )

        await interaction.response.send_message(
            embed=embed
        )

        await asyncio.sleep(5)

        await process_ticket_closure(
            interaction.channel,
            interaction.user,
            "Closed via button",
            self.claimed_by
        )

    @discord.ui.button(
        label="Close with Reason",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_close_reason"
    )
    async def close_reason_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        modal = CloseReasonModal()
        modal.view = self

        await interaction.response.send_modal(
            modal
        )


class PanelSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="General Enquiry",
                value="general",
                description="Ask general questions"
            ),
            discord.SelectOption(
                label="Appeal Ticket",
                value="appeal",
                description="Submit an appeal"
            ),
            discord.SelectOption(
                label="Report Ticket",
                value="report",
                description="Report LR/MR staff or members"
            ),
            discord.SelectOption(
                label="Board Report",
                value="board",
                description="Report IA+ staff members"
            ),
        ]

        super().__init__(
            placeholder="Select a category to open a ticket...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="panel_select_dropdown"
        )

    async def callback(
        self,
        interaction: discord.Interaction
    ):
        data = load_data()

        if interaction.user.id in data.get(
            "blacklisted",
            []
        ):
            embed = discord.Embed(
                title="Access Denied",
                description=(
                    "You are blacklisted from "
                    "creating tickets."
                ),
                color=discord.Color.red()
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

            return

        selected = self.values[0]
        guild = interaction.guild

        category = guild.get_channel(
            PANEL_CATEGORIES.get(selected)
        )

        overwrites = {
            guild.default_role:
                discord.PermissionOverwrite(
                    read_messages=False
                ),

            interaction.user:
                discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True
                ),

            guild.me:
                discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    manage_channels=True
                )
        }

        allowed_roles = PANEL_ACCESS.get(
            selected,
            []
        )

        apply_role_permissions(
            overwrites,
            guild,
            allowed_roles
        )

        data["ticket_count"] += 1
        ticket_num = data["ticket_count"]

        save_data(data)

        channel_name = (
            f"{selected}-{ticket_num}"
        )

        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=(
                f"creator_id:{interaction.user.id} | "
                f"panel:{selected} | "
                f"orig_name:{channel_name}"
            )
        )

        ping_role_id = (
            ROLES_IA[0]
            if selected in [
                "general",
                "appeal",
                "report"
            ]
            else ROLES_SHR[0]
        )

        ping_text = (
            f"<@&{ping_role_id}>"
            if ping_role_id
            else ""
        )

        instruction_data = TICKET_INSTRUCTIONS.get(
            selected,
            {
                "title":
                    f"{selected.capitalize()} Ticket #{ticket_num}",
                "description":
                    "Please describe your issue clearly."
            }
        )

        ticket_embed = discord.Embed(
            title=instruction_data["title"],
            description=instruction_data["description"],
            color=discord.Color.blue()
        )

        ticket_embed.set_footer(
            text=(
                f"Ticket #{ticket_num} • Created by "
                f"{interaction.user.display_name}"
            )
        )

        await channel.send(
            content=(
                f"{interaction.user.mention} "
                f"{ping_text}"
            ),
            embed=ticket_embed,
            view=TicketControlView(
                creator_id=interaction.user.id
            )
        )

        response_embed = discord.Embed(
            description=(
                f"Your ticket has been created: "
                f"{channel.mention}"
            ),
            color=discord.Color.green()
        )

        await interaction.response.send_message(
            embed=response_embed,
            ephemeral=True
        )


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(PanelSelect())

# --- FILE-BASED ANTI-ROLE PROTECTION ---

def sync_cache_to_file(guild):
    cache = {}

    for channel in guild.channels:
        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Role):
                str_role_id = str(target.id)
                str_channel_id = str(channel.id)

                if str_role_id not in cache:
                    cache[str_role_id] = {}

                pair = overwrite.pair()

                cache[str_role_id][str_channel_id] = {
                    "allow": pair[0].value,
                    "deny": pair[1].value
                }

    save_perm_cache(cache)

async def handle_offender(
    guild,
    executor,
    action_name
):
    if executor.bot or executor.id == OWNER_USER_ID:
        return False

    offender_manage_roles = [
        role
        for role in executor.roles
        if role.permissions.manage_roles
        and role.name != "@everyone"
    ]

    if offender_manage_roles:
        try:
            await executor.remove_roles(
                *offender_manage_roles,
                reason=(
                    f"Unauthorized role action: "
                    f"{action_name}"
                )
            )
        except discord.Forbidden:
            pass

        alert_channel = guild.get_channel(
            ALERT_CHANNEL_ID
        )

        if alert_channel:
            shr_ping = (
                f"<@&{ROLES_SHR[0]}>"
                if ROLES_SHR
                else ""
            )

            roles_removed_names = ", ".join(
                [
                    r.name
                    for r in offender_manage_roles
                ]
            )

            await alert_channel.send(
                f"🚨 {shr_ping} **SECURITY ALERT** 🚨\n"
                f"**Offender:** {executor.mention} (`{executor.id}`)\n"
                f"**Action Attempted:** {action_name}\n"
                f"**Manage Roles Stripped:** `{roles_removed_names}`\n"
                f"**Status:** Action automatically reverted!"
            )

        return True

    return False


class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- MERGED ON_READY EVENT ---

    @commands.Cog.listener()
    async def on_ready(self):
        print(
            f"Logged in as {self.bot.user} "
            f"(ID: {self.bot.user.id})"
        )

        self.bot.add_view(
            TicketPanelView()
        )

        self.bot.add_view(
            TicketControlView()
        )

        for guild in self.bot.guilds:
            sync_cache_to_file(guild)

        print(
            "Anti-Role Protection Active "
            "& Permission Cache Saved to File!"
        )

    # --- BOT SECURITY LISTENERS ---

    @commands.Cog.listener()
    async def on_guild_role_create(
        self,
        role: discord.Role
    ):
        guild = role.guild

        await asyncio.sleep(1)

        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.role_create,
            limit=3
        ):
            if entry.target.id == role.id:
                is_restricted = await handle_offender(
                    guild,
                    entry.user,
                    f"Created Role `{role.name}`"
                )

                if is_restricted:
                    try:
                        await role.delete(
                            reason=(
                                "Auto-restoration: "
                                "Unauthorized role creation."
                            )
                        )
                    except discord.Forbidden:
                        pass

                break

    @commands.Cog.listener()
    async def on_guild_role_delete(
        self,
        role: discord.Role
    ):
        guild = role.guild

        cache = load_perm_cache()

        saved_overrides = cache.get(
            str(role.id),
            {}
        ).copy()

        await asyncio.sleep(1)

        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.role_delete,
            limit=3
        ):
            if entry.target.id == role.id:
                is_restricted = await handle_offender(
                    guild,
                    entry.user,
                    f"Deleted Role `{role.name}`"
                )

                if is_restricted:
                    try:
                        original_position = role.position

                        new_role = await guild.create_role(
                            name=role.name,
                            permissions=role.permissions,
                            color=role.color,
                            hoist=role.hoist,
                            mentionable=role.mentionable,
                            reason=(
                                "Auto-restoration: "
                                "Role deleted by staff."
                            )
                        )

                        try:
                            await new_role.edit(
                                position=original_position
                            )
                        except (
                            discord.Forbidden,
                            discord.HTTPException
                        ):
                            pass

                        for str_channel_id, perm_data in saved_overrides.items():
                            channel = guild.get_channel(
                                int(str_channel_id)
                            )

                            if channel:
                                try:
                                    allow_perms = discord.Permissions(
                                        perm_data["allow"]
                                    )

                                    deny_perms = discord.Permissions(
                                        perm_data["deny"]
                                    )

                                    overwrite = (
                                        discord.PermissionOverwrite
                                        .from_pair(
                                            allow_perms,
                                            deny_perms
                                        )
                                    )

                                    await channel.set_permissions(
                                        new_role,
                                        overwrite=overwrite,
                                        reason=(
                                            "Auto-restoration: "
                                            "Re-applying channel permissions."
                                        )
                                    )

                                    await asyncio.sleep(0.3)

                                except (
                                    discord.Forbidden,
                                    discord.HTTPException
                                ):
                                    pass

                    except (
                        discord.Forbidden,
                        discord.HTTPException
                    ) as e:
                        print(
                            f"[CRITICAL ERROR] "
                            f"Failed during role restoration: {e}"
                        )

                break

    @commands.Cog.listener()
    async def on_guild_role_update(
        self,
        before: discord.Role,
        after: discord.Role
    ):
        if (
            before.name == after.name
            and before.permissions == after.permissions
            and before.color == after.color
            and before.hoist == after.hoist
            and before.mentionable == after.mentionable
            and before.position == after.position
        ):
            return

        guild = after.guild

        await asyncio.sleep(1.5)

        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.role_update,
            limit=5
        ):
            if entry.user.id == self.bot.user.id:
                continue

            is_restricted = await handle_offender(
                guild,
                entry.user,
                f"Edited/Rearranged Role `{before.name}`"
            )

            if is_restricted:
                try:
                    await after.edit(
                        name=before.name,
                        permissions=before.permissions,
                        color=before.color,
                        hoist=before.hoist,
                        mentionable=before.mentionable,
                        position=before.position,
                        reason=(
                            "Auto-restoration: "
                            "Unauthorized role edit/reorder."
                        )
                    )

                except (
                    discord.Forbidden,
                    discord.HTTPException
                ) as e:
                    print(
                        f"[ERROR] "
                        f"Failed to revert role changes: {e}"
                    )

                break

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel
    ):
        guild = after.guild

        sync_cache_to_file(guild)

        await asyncio.sleep(1)

        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.overwrite_update,
            limit=3
        ):
            if entry.user.id == self.bot.user.id:
                continue

            if isinstance(entry.target, discord.Role):
                target_role = entry.target

                is_restricted = await handle_offender(
                    guild,
                    entry.user,
                    (
                        f"Modified Channel Overrides "
                        f"for Role `{target_role.name}` "
                        f"in #{after.name}"
                    )
                )

                if is_restricted:
                    try:
                        old_overwrite = (
                            before.overwrites.get(
                                target_role
                            )
                        )

                        await after.set_permissions(
                            target_role,
                            overwrite=old_overwrite,
                            reason=(
                                "Auto-restoration: "
                                "Unauthorized channel "
                                "permission edit."
                            )
                        )

                    except (
                        discord.Forbidden,
                        discord.HTTPException
                    ) as e:
                        print(
                            f"[ERROR] "
                            f"Failed to revert channel "
                            f"overwrite: {e}"
                        )

                break

    # --- COMMANDS ---

    @app_commands.command(
        name="sendpanel",
        description="Send the ticket creation panel to a channel."
    )
    @app_commands.checks.has_any_role(*ROLES_LS)
    @is_allowed_guild()
    async def sendpanel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel
    ):
        embed = discord.Embed(
            title="🎫 Support Ticket Rules & Guidelines",
            description=(
                "Welcome to our Support Center! "
                "Before opening a ticket, please read "
                "the guidelines below and choose the "
                "appropriate category.\n\n"
                "⚠️ **Abusing the ticket system may "
                "result in a ticket blacklist or "
                "server mute.**"
            ),
            color=5814783
        )

        embed.set_footer(
            text=(
                "Please select a category below "
                "to open a ticket."
            )
        )

        embed.add_field(
            name="📜 General Ticket Rules",
            value=(
                "• **No Ping Spam:** Do not ping staff members. "
                "We are automatically notified.\n"
                "• **Be Detailed:** State your issue immediately "
                "in your first message. Do not just say \"hello\".\n"
                "• **No Joke Tickets:** Trolling or wasting staff "
                "time will result in an immediate punishment.\n"
                "• **No DMs:** Keep all support conversations "
                "inside the ticket channel for safety and logs."
            ),
            inline=False
        )

        embed.add_field(
            name="📧 1. General Enquiry",
            value=(
                "**Use for:** General questions about the server, "
                "roles or claiming prizes.\n"
                "**What to provide:** A clear and concise question."
            ),
            inline=False
        )

        embed.add_field(
            name="⚖️ 2. Appeal",
            value=(
                "**Use for:** Appealing a moderation action "
                "(mute, warn, kick, or ban).\n"
                "**What to provide:** Your Discord/Roblox ID, "
                "the reason for moderation, and why the penalty "
                "should be reconsidered.\n"
                "*Hostility will result in immediate appeal denial.*"
            ),
            inline=False
        )

        embed.add_field(
            name="🚨 3. Report",
            value=(
                "**Use for:** Reporting a server member or "
                "Low Ranking Staff Member for breaking rules.\n"
                "**What to provide:** Member's username/ID, "
                "description of the incident, and **unedited proof** "
                "(screenshots/video clips)."
            ),
            inline=False
        )

        embed.add_field(
            name="📌 4. Board Report",
            value=(
                "**Use for:** Reporting IA+ staff misconduct or "
                "critical matters requiring SHR attention.\n"
                "**What to provide:** Staff member name/issue "
                "details and direct proof.\n"
                "*Strictly confidential & visible only to "
                "SHR and EHR.*"
            ),
            inline=False
        )

        await channel.send(
            embed=embed,
            view=TicketPanelView()
        )

        confirm_embed = discord.Embed(
            description=(
                f"Ticket panel successfully sent to "
                f"{channel.mention}."
            ),
            color=discord.Color.green()
        )

        await interaction.response.send_message(
            embed=confirm_embed,
            ephemeral=True
        )

    @app_commands.command(
        name="close",
        description="Close the current ticket channel with an optional reason."
    )
    @app_commands.describe(
        reason="Reason for closing the ticket"
    )
    async def close_command(
        self,
        interaction: discord.Interaction,
        reason: str = "No reason provided"
    ):
        if not is_ticket_channel(interaction.channel):
            embed = discord.Embed(
                title="Command Restricted",
                description=(
                    "This command can only be used "
                    "inside an active ticket channel."
                ),
                color=discord.Color.red()
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

            return

        embed = discord.Embed(
            description=(
                f"Ticket closure initiated by "
                f"{interaction.user.mention}.\n"
                f"**Reason:** {reason}\n"
                f"Closing in 5 seconds..."
            ),
            color=discord.Color.red()
        )

        await interaction.response.send_message(
            embed=embed
        )

        await asyncio.sleep(5)

        await process_ticket_closure(
            interaction.channel,
            interaction.user,
            reason
        )

    @app_commands.command(
        name="closerequest",
        description="Request to close the ticket with a reason and optional delay."
    )
    @app_commands.describe(
        reason="Reason for requesting ticket closure",
        delay_hours="Optional auto-close delay in hours (e.g., 2 or 0.5)"
    )
    async def closerequest_command(
        self,
        interaction: discord.Interaction,
        reason: str,
        delay_hours: float = None
    ):
        if not is_ticket_channel(interaction.channel):
            embed = discord.Embed(
                title="Command Restricted",
                description=(
                    "This command can only be used "
                    "inside an active ticket channel."
                ),
                color=discord.Color.red()
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

            return

        creator_id = None

        if interaction.channel.topic:
            for part in interaction.channel.topic.split("|"):
                if part.strip().startswith("creator_id:"):
                    try:
                        creator_id = int(
                            part.strip().replace(
                                "creator_id:",
                                ""
                            )
                        )
                    except ValueError:
                        pass

        creator_ping = (
            f"<@{creator_id}>"
            if creator_id
            else ""
        )

        delay_text = (
            f"\n⏱️ **Auto-Close Delay:** "
            f"{delay_hours} hour(s)"
            if delay_hours and delay_hours > 0
            else ""
        )

        request_embed = discord.Embed(
            title="Closure Request",
            description=(
                f"{interaction.user.mention} "
                f"has requested to close this ticket.\n\n"
                f"**Reason:** {reason}"
                f"{delay_text}"
            ),
            color=discord.Color.orange()
        )

        view = CloseRequestView(
            interaction.user,
            reason,
            delay_hours=delay_hours
        )

        await interaction.response.send_message(
            content=creator_ping,
            embed=request_embed,
            view=view
        )

        view.start_timer(
            interaction.channel
        )

    @app_commands.command(
        name="add",
        description="Add a member to the current ticket."
    )
    @is_allowed_guild()
    async def add_member(
        self,
        interaction: discord.Interaction,
        member: discord.Member
    ):
        if not is_ticket_channel(interaction.channel):
            embed = discord.Embed(
                title="Command Restricted",
                description=(
                    "This command can only be used "
                    "inside an active ticket channel."
                ),
                color=discord.Color.red()
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

            return

        await interaction.channel.set_permissions(
            member,
            read_messages=True,
            send_messages=True
        )

        embed = discord.Embed(
            description=(
                f"Added {member.mention} to the ticket."
            ),
            color=discord.Color.green()
        )

        await interaction.response.send_message(
            embed=embed
        )

    @app_commands.command(
        name="remove",
        description="Remove a member from the current ticket."
    )
    @is_allowed_guild()
    async def remove_member(
        self,
        interaction: discord.Interaction,
        member: discord.Member
    ):
        if not is_ticket_channel(interaction.channel):
            embed = discord.Embed(
                title="Command Restricted",
                description=(
                    "This command can only be used "
                    "inside an active ticket channel."
                ),
                color=discord.Color.red()
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

            return

        await interaction.channel.set_permissions(
            member,
            overwrite=None
        )

        embed = discord.Embed(
            description=(
                f"Removed {member.mention} from the ticket."
            ),
            color=discord.Color.orange()
        )

        await interaction.response.send_message(
            embed=embed
        )

    @app_commands.command(
        name="rename",
        description="Rename the ticket channel."
    )
    @is_allowed_guild()
    async def rename_ticket(
        self,
        interaction: discord.Interaction,
        name: str
    ):
        if not is_ticket_channel(interaction.channel):
            embed = discord.Embed(
                title="Command Restricted",
                description=(
                    "This command can only be used "
                    "inside an active ticket channel."
                ),
                color=discord.Color.red()
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

            return

        await interaction.channel.edit(
            name=name
        )

        embed = discord.Embed(
            description=(
                f"Ticket channel renamed to `{name}`."
            ),
            color=discord.Color.blue()
        )

        await interaction.response.send_message(
            embed=embed
        )

    @app_commands.command(
        name="transfer",
        description="Transfer a claimed ticket to another staff member."
    )
    @app_commands.checks.has_any_role(
        *TICKET_ADMIN_ROLES
    )
    @is_allowed_guild()
    async def transfer_ticket(
        self,
        interaction: discord.Interaction,
        new_owner: discord.Member
    ):
        if not is_ticket_channel(interaction.channel):
            embed = discord.Embed(
                title="Command Restricted",
                description=(
                    "This command can only be used "
                    "inside an active ticket channel."
                ),
                color=discord.Color.red()
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

            return

        await interaction.channel.set_permissions(
            new_owner,
            read_messages=True,
            send_messages=True
        )

        embed = discord.Embed(
            description=(
                f"Ticket transferred to "
                f"{new_owner.mention}."
            ),
            color=discord.Color.blue()
        )

        await interaction.response.send_message(
            embed=embed
        )

    @app_commands.command(
        name="switchpanel",
        description="Switch ticket access permissions to another panel type."
    )
    @app_commands.checks.has_any_role(
        *TICKET_ADMIN_ROLES
    )
    @app_commands.choices(
        panel=[
            app_commands.Choice(
                name="General Enquiry",
                value="general"
            ),
            app_commands.Choice(
                name="Appeal Ticket",
                value="appeal"
            ),
            app_commands.Choice(
                name="Report Ticket",
                value="report"
            ),
            app_commands.Choice(
                name="Board Report",
                value="board"
            )
        ]
    )
    @is_allowed_guild()
    async def switch_panel(
        self,
        interaction: discord.Interaction,
        panel: app_commands.Choice[str]
    ):
        if not is_ticket_channel(interaction.channel):
            embed = discord.Embed(
                title="Command Restricted",
                description=(
                    "This command can only be used "
                    "inside an active ticket channel."
                ),
                color=discord.Color.red()
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

            return

        guild = interaction.guild
        channel = interaction.channel

        current_panel = None
        orig_name = channel.name
        creator_id = None

        if channel.topic:
            for part in channel.topic.split("|"):
                part = part.strip()

                if part.startswith("panel:"):
                    current_panel = part.replace(
                        "panel:",
                        ""
                    ).strip()

                elif part.startswith("orig_name:"):
                    orig_name = part.replace(
                        "orig_name:",
                        ""
                    ).strip()

                elif part.startswith("creator_id:"):
                    try:
                        creator_id = int(
                            part.replace(
                                "creator_id:",
                                ""
                            ).strip()
                        )
                    except ValueError:
                        pass

        if current_panel is None:
            parts = channel.name.split("-")

            if (
                len(parts) >= 2
                and parts[0] in PANEL_CATEGORIES
            ):
                current_panel = parts[0]

            else:
                for p_name, cat_id in PANEL_CATEGORIES.items():
                    if channel.category_id == cat_id:
                        current_panel = p_name
                        break

        if current_panel == panel.value:
            embed = discord.Embed(
                title="Action Denied",
                description=(
                    f"This ticket is already set to "
                    f"the **{panel.name}** panel."
                ),
                color=discord.Color.red()
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

            return

        await interaction.response.defer()

        new_category_id = PANEL_CATEGORIES.get(
            panel.value
        )

        new_category = guild.get_channel(
            new_category_id
        )

        edit_kwargs = {}

        if new_category:
            edit_kwargs["category"] = new_category

        valid_panels = list(
            PANEL_CATEGORIES.keys()
        )

        parts = channel.name.split("-")

        if (
            len(parts) == 2
            and parts[0] in valid_panels
            and parts[1].isdigit()
        ):
            edit_kwargs["name"] = (
                f"{panel.value}-{parts[1]}"
            )

        new_topic_parts = []

        if creator_id:
            new_topic_parts.append(
                f"creator_id:{creator_id}"
            )

        new_topic_parts.append(
            f"panel:{panel.value}"
        )

        new_topic_parts.append(
            f"orig_name:{orig_name}"
        )

        edit_kwargs["topic"] = (
            " | ".join(new_topic_parts)
        )

        if edit_kwargs:
            await channel.edit(
                **edit_kwargs
            )

        all_roles = set(
            ROLES_IA
            + ROLES_MGT
            + ROLES_SHR
            + ROLES_LS
        )

        for role_id in all_roles:
            role = guild.get_role(role_id)

            if role:
                await channel.set_permissions(
                    role,
                    overwrite=None
                )

        target_roles = PANEL_ACCESS.get(
            panel.value,
            []
        )

        for role_id in target_roles:
            role = guild.get_role(
                role_id
            )

            if role:
                await channel.set_permissions(
                    role,
                    read_messages=True,
                    send_messages=True
                )

        instruction_data = TICKET_INSTRUCTIONS.get(
            panel.value,
            {
                "title":
                    f"{panel.name} Instructions",
                "description":
                    "Please describe your issue clearly."
            }
        )

        first_message = None

        async for msg in channel.history(
            limit=20,
            oldest_first=True
        ):
            if (
                msg.author == self.bot.user
                and msg.embeds
            ):
                first_message = msg
                break

        if first_message:
            old_embed = first_message.embeds[0]

            new_embed = discord.Embed(
                title=instruction_data["title"],
                description=instruction_data["description"],
                color=discord.Color.blue()
            )

            if old_embed.footer:
                new_embed.set_footer(
                    text=old_embed.footer.text,
                    icon_url=old_embed.footer.icon_url
                )

            ping_role_id = (
                ROLES_IA[0]
                if panel.value in [
                    "general",
                    "appeal",
                    "report"
                ]
                else ROLES_SHR[0]
            )

            creator_ping = (
                f"<@{creator_id}>"
                if creator_id
                else ""
            )

            ping_text = (
                f"{creator_ping} "
                f"<@&{ping_role_id}>"
                if ping_role_id
                else creator_ping
            )

            await first_message.edit(
                content=ping_text,
                embed=new_embed
            )

        embed = discord.Embed(
            description=(
                f"Panel updated to **{panel.name}**. "
                f"Channel moved and permissions updated."
            ),
            color=discord.Color.green()
        )

        await interaction.followup.send(
            embed=embed
        )

    @app_commands.command(
        name="ticketblacklist",
        description="Blacklist a user from opening tickets."
    )
    @app_commands.checks.has_any_role(
        *TICKET_ADMIN_ROLES
    )
    @is_allowed_guild()
    async def ticketblacklist(
        self,
        interaction: discord.Interaction,
        member: discord.Member
    ):
        data = load_data()

        if member.id not in data["blacklisted"]:
            data["blacklisted"].append(
                member.id
            )

            save_data(data)

            embed = discord.Embed(
                description=(
                    f"{member.mention} has been "
                    f"blacklisted from opening tickets."
                ),
                color=discord.Color.red()
            )

            await interaction.response.send_message(
                embed=embed
            )

        else:
            embed = discord.Embed(
                description=(
                    f"{member.mention} is already blacklisted."
                ),
                color=discord.Color.orange()
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

    @app_commands.command(
        name="ticketunblacklist",
        description="Remove a user from the ticket blacklist."
    )
    @app_commands.checks.has_any_role(
        *TICKET_ADMIN_ROLES
    )
    @is_allowed_guild()
    async def ticketunblacklist(
        self,
        interaction: discord.Interaction,
        member: discord.Member
    ):
        data = load_data()

        if member.id in data["blacklisted"]:
            data["blacklisted"].remove(
                member.id
            )

            save_data(data)

            embed = discord.Embed(
                description=(
                    f"{member.mention} has been "
                    f"unblacklisted."
                ),
                color=discord.Color.green()
            )

            await interaction.response.send_message(
                embed=embed
            )

        else:
            embed = discord.Embed(
                description=(
                    f"{member.mention} is not "
                    f"currently blacklisted."
                ),
                color=discord.Color.orange()
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

    # --- GLOBAL APP COMMAND ERROR HANDLER ---

    @commands.Cog.listener()
    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError
    ):
        if isinstance(
            error,
            app_commands.CheckFailure
        ):
            return

        elif isinstance(
            error,
            (
                app_commands.MissingAnyRole,
                app_commands.MissingRole
            )
        ):
            embed = discord.Embed(
                title="Permission Denied",
                description=(
                    "You do not have the required "
                    "permissions or roles to use "
                    "this command."
                ),
                color=discord.Color.red()
            )

            if interaction.response.is_done():
                await interaction.followup.send(
                    embed=embed,
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    embed=embed,
                    ephemeral=True
                )

            return

        elif isinstance(
            error,
            app_commands.MissingPermissions
        ):
            embed = discord.Embed(
                title="Permission Denied",
                description=(
                    "You lack the required server "
                    "permissions to execute this command."
                ),
                color=discord.Color.red()
            )

            if interaction.response.is_done():
                await interaction.followup.send(
                    embed=embed,
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    embed=embed,
                    ephemeral=True
                )

            return

        else:
            embed = discord.Embed(
                title="Error",
                description=(
                    "An unexpected error occurred "
                    "while executing the command."
                ),
                color=discord.Color.dark_red()
            )

            if interaction.response.is_done():
                await interaction.followup.send(
                    embed=embed,
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    embed=embed,
                    ephemeral=True
                )

            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(
        TicketCog(bot)
    )
