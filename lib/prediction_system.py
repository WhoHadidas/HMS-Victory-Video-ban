import json, os, io, discord
from PIL import Image, ImageDraw
import uuid
from lib.ukpence import add_bb, remove_bb

PRED_FILE = "predictions.json"

def _load() -> dict:
    return json.load(open(PRED_FILE)) if os.path.exists(PRED_FILE) else {}

def _save(d: dict) -> None:
    json.dump(d, open(PRED_FILE, "w"), indent=4)

class Prediction:
    def __init__(self, msg_id: int, title: str, opt1: str, opt2: str, end_ts: float, channel_id: int | None = None):
        self.msg_id = msg_id
        self.channel_id = channel_id
        self.title = title
        self.opt1 = opt1
        self.opt2 = opt2
        self.bets = {1: {}, 2: {}}
        self.locked = False
        self.end_ts = end_ts

    def stake(self, uid: int, side: int, amount: int) -> bool:
        if self.locked or side not in (1, 2) or uid in self.bets[3 - side]:
            return False
        if amount > 100_000 or not remove_bb(uid, amount):
            return False
        self.bets[side][uid] = self.bets[side].get(uid, 0) + amount
        return True

    def totals(self) -> tuple[int, int]:
        return sum(self.bets.get(1, {}).values()), sum(self.bets.get(2, {}).values())

    def resolve(self, win_side: int) -> dict[int, int]:
        lose_side = 2 if win_side == 1 else 1
        lose_pool = sum(self.bets.get(lose_side, {}).values())
        win_total = sum(self.bets.get(win_side, {}).values())
        payouts = {}
        if win_total == 0:
            return payouts
        for uid, stake in self.bets[win_side].items():
            share = stake / win_total
            winnings = stake + int(share * lose_pool)
            payouts[uid] = winnings
            add_bb(uid, winnings)
        return payouts

    def to_dict(self) -> dict:
        bets_dump = {str(side): {str(uid): amt for uid, amt in pool.items()} for side, pool in self.bets.items()}
        return {
            "msg_id": self.msg_id,
            "channel_id": self.channel_id,
            "title": self.title,
            "opt1": self.opt1,
            "opt2": self.opt2,
            "bets": bets_dump,
            "locked": self.locked,
            "end": self.end_ts,
        }


    @staticmethod
    def from_dict(d: dict):
        p = Prediction(d["msg_id"], d["title"], d["opt1"], d["opt2"], d["end"], d.get("channel_id"))
        p.bets = {int(side): {int(uid): amt for uid, amt in pool.items()} for side, pool in d["bets"].items()}
        p.locked = d["locked"]
        return p


def _progress_png(pct: float) -> io.BytesIO:
    W, H = 400, 18
    green, blurple = (46, 204, 113), (88, 101, 242)
    img = Image.new("RGB", (W, H), blurple)
    ImageDraw.Draw(img).rectangle([0, 0, int(W * pct), H], fill=green)
    buff = io.BytesIO()
    img.save(buff, format="PNG")
    buff.seek(0)
    return buff

CASH, TROPHY, USER, COIN, MEDAL = "💰", "🏆", "👥", "🪙", "🏅"

def _fmt_money(n: int) -> str:
    return f"{n:,}"

def _odds(t1: int, t2: int, side: int) -> float:
    win, lose = (t1, t2) if side == 1 else (t2, t1)
    return 0 if win == 0 else max((win + lose) / win, 1)

def _top_bettor(bets: dict[int, int], client: discord.Client | None) -> str:
    if not bets:
        return "-"
    uid = max(bets, key=bets.get)
    if client:
        user = client.get_user(uid)
        name = user.display_name if user else f"{uid}"
    else:
        name = f"<@{uid}>"
    return f"{name} {_fmt_money(bets[uid])}"

def prediction_embed(pred: Prediction, client: discord.Client | None = None) -> tuple[discord.Embed, discord.File]:
    t1, t2 = pred.totals()
    total = t1 + t2 or 1
    pct1 = t1 / total
    pct2 = 1 - pct1
    now = discord.utils.utcnow().timestamp()
    if pred.locked:
        time_line = "🔒 **locked**"
    elif pred.end_ts and pred.end_ts > now:
        time_line = f"⏰ closes <t:{int(pred.end_ts)}:R>"
    else:
        time_line = "🔓 **unlocked**"



    e = discord.Embed(title=pred.title, description=time_line)
    e.add_field(
        name=pred.opt1,
        value=(
            f"{CASH} **{_fmt_money(t1)}** `{int(pct1*100)}%`\n"
            f"{TROPHY} **{_odds(t1,t2,1):.2f}x**\n"
            f"{USER} {len(pred.bets.get(1, {}))}\n"
            f"{MEDAL} {_top_bettor(pred.bets.get(1, {}), client)}"
        ),
        inline=True,
    )
    e.add_field(
        name=pred.opt2,
        value=(
            f"{CASH} **{_fmt_money(t2)}** `{int(pct2*100)}%`\n"
            f"{TROPHY} **{_odds(t1,t2,2):.2f}x**\n"
            f"{USER} {len(pred.bets.get(2, {}))}\n"
            f"{MEDAL} {_top_bettor(pred.bets.get(2, {}), client)}"
        ),
        inline=True,
    )
    bar_file = discord.File(_progress_png(pct1), filename="bar.png")
    e.set_image(url="attachment://bar.png")
    return e, bar_file

class BetModal(discord.ui.Modal, title="Place your bet"):
    amount = discord.ui.TextInput(label="Amount (≤ 100 000)", placeholder="whole number")

    def __init__(self, pred: Prediction, side: int):
        super().__init__()
        self.pred = pred
        self.side = side

    async def on_submit(self, interaction: discord.Interaction):
        try:
            stake = int(self.amount.value.replace(",", "").strip())
        except ValueError:
            return await interaction.response.send_message("Enter a valid integer.", ephemeral=True)
        if self.pred.stake(interaction.user.id, self.side, stake):
            user_total = self.pred.bets[self.side][interaction.user.id]
            embed, bar = prediction_embed(self.pred, interaction.client)
            await interaction.message.edit(embed=embed, attachments=[bar])
            await interaction.response.send_message(
                f"✅ Bet placed! You now have **{_fmt_money(user_total)}** on **{self.pred.opt1 if self.side==1 else self.pred.opt2}**.",
                ephemeral=True
            )
            _save({k: v.to_dict() for k, v in interaction.client.predictions.items()})
        else:
            await interaction.response.send_message("Bet failed.", ephemeral=True)

class BetButtons(discord.ui.View):
    def __init__(self, pred: Prediction):
        super().__init__(timeout=None)
        self.pred = pred

        async def _handler(interaction: discord.Interaction, side: int):
            if self.pred.locked:
                await interaction.response.send_message("Betting locked.", ephemeral=True)
                return
            await interaction.response.send_modal(BetModal(self.pred, side))

        btn1 = discord.ui.Button(
            label=f"Bet on {pred.opt1}",
            style=discord.ButtonStyle.success,
            custom_id = f"prediction:{pred.msg_id}:bet1"
        )
        btn2 = discord.ui.Button(
            label=f"Bet on {pred.opt2}",
            style=discord.ButtonStyle.primary,
            custom_id = f"prediction:{pred.msg_id}:bet2"
        )

        async def btn1_cb(interaction: discord.Interaction):
            await _handler(interaction, 1)

        async def btn2_cb(interaction: discord.Interaction):
            await _handler(interaction, 2)

        btn1.callback = btn1_cb
        btn2.callback = btn2_cb

        self.add_item(btn1)
        self.add_item(btn2)



class PredAdminView(discord.ui.View):
    def __init__(self, pred: Prediction, client: discord.Client):
        super().__init__(timeout=600)
        self.pred = pred
        self.client = client

    @discord.ui.button(label="Lock", style=discord.ButtonStyle.danger)
    async def lock(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if self.pred.locked:
            return await interaction.response.send_message("Already locked.", ephemeral=True)
        self.pred.locked = True
        msg = await interaction.channel.fetch_message(self.pred.msg_id)
        embed, bar = prediction_embed(self.pred, self.client)
        await msg.edit(embed=embed, attachments=[bar], view=None)
        _save({k: v.to_dict() for k, v in self.client.predictions.items()})
        await interaction.response.send_message("🔒 Locked.", ephemeral=True)

    @discord.ui.button(label="Unlock", style=discord.ButtonStyle.success)
    async def unlock(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if not self.pred.locked:
            return await interaction.response.send_message("Already unlocked.", ephemeral=True)

        self.pred.locked = False
        self.pred.end_ts = None

        msg = await interaction.channel.fetch_message(self.pred.msg_id)
        embed, bar = prediction_embed(self.pred, self.client)
        view = BetButtons(self.pred)

        await msg.edit(embed=embed, attachments=[bar], view=view)
        self.client.add_view(view, message_id=self.pred.msg_id)

        _save({k: v.to_dict() for k, v in self.client.predictions.items()})
        await interaction.response.send_message("🔓 Unlocked.", ephemeral=True)

    @discord.ui.button(label="Draw", style=discord.ButtonStyle.secondary)
    async def draw(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        for side in (1, 2):
            for uid, amt in self.pred.bets.get(side, {}).items():
                add_bb(uid, amt)
        self.pred.locked = True
        self.pred.bets = {1: {}, 2: {}}
        msg = await interaction.channel.fetch_message(self.pred.msg_id)
        embed, bar = prediction_embed(self.pred, self.client)
        await msg.edit(embed=embed, attachments=[bar], view=None)
        self.client.predictions.pop(self.pred.msg_id, None)
        _save({k: v.to_dict() for k, v in self.client.predictions.items()})
        await interaction.response.send_message("🟡 Draw called – all bets refunded.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Winner: Option 1", style=discord.ButtonStyle.primary)
    async def win1(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await self._resolve(interaction, 1)

    @discord.ui.button(label="Winner: Option 2", style=discord.ButtonStyle.primary)
    async def win2(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await self._resolve(interaction, 2)

async def _resolve(self, interaction: discord.Interaction, winner_side_int: int):
        if not self.pred.locked:
            self.pred.locked = True

        payouts = self.pred.resolve(winner_side_int)
        win_side_text = self.pred.opt1 if winner_side_int == 1 else self.pred.opt2
        lose_side_int = 3 - winner_side_int

        uk_timezone = pytz.timezone("Europe/London")
        activity_date_str = datetime.now(uk_timezone).strftime("%Y-%m-%d") 

        if not hasattr(interaction.client, 'temp_daily_specific_rewards'):
            interaction.client.temp_daily_specific_rewards = {}
        if activity_date_str not in interaction.client.temp_daily_specific_rewards:
            interaction.client.temp_daily_specific_rewards[activity_date_str] = []

        for user_id, total_received in payouts.items():
            original_stake = self.pred.bets[winner_side_int].get(user_id, 0)
            net_gain = total_received - original_stake
            if net_gain != 0: 
                interaction.client.temp_daily_specific_rewards[activity_date_str].append({
                    "user_id": str(user_id),
                    "amount": net_gain,
                    "type": f"Prediction Win ('{self.pred.title}')"
                })
        
        losing_bets_on_other_side = self.pred.bets.get(lose_side_int, {})
        for user_id, stake_lost in losing_bets_on_other_side.items():
            if stake_lost != 0:
                interaction.client.temp_daily_specific_rewards[activity_date_str].append({
                    "user_id": str(user_id),
                    "amount": -stake_lost,
                    "type": f"Prediction Loss ('{self.pred.title}')"
                })
        
        try:
            msg = await interaction.channel.fetch_message(self.pred.msg_id)
            embed, bar = prediction_embed(self.pred, self.client)
            await msg.edit(embed=embed, attachments=[bar] if bar else [], view=None)

            payout_lines = []
            if payouts:
                 for uid_winner, amt_total_returned in sorted(payouts.items(), key=lambda x: x[1], reverse=True):
                    stake_winner = self.pred.bets[winner_side_int].get(uid_winner, 0)
                    profit = amt_total_returned - stake_winner
                    member_display = f"<@{uid_winner}>" 
                    guild_member = interaction.guild.get_member(uid_winner)
                    if guild_member: member_display = guild_member.display_name
                    payout_lines.append(f"**{member_display}** received **{_fmt_money(amt_total_returned)}** UKP (Profit: **{_fmt_money(profit)}** UKP)")
            else: 
                if not self.pred.bets.get(winner_side_int) and self.pred.bets.get(lose_side_int):
                    payout_lines.append("*No one backed the winner. Losing bets were refunded.*")
                else: 
                    payout_lines.append("*No winning bets to pay out.*")


            descr = "\n".join(payout_lines) if payout_lines else "*No payouts processed.*"
            summary_embed_color = 0x2ECC71 if payouts and any(payouts.values()) else 0xFFA500 
            summary_embed = discord.Embed(title=f"🏁 Prediction Settled: **{win_side_text}** Wins!", description=descr, color=summary_embed_color)
            await msg.reply(embed=summary_embed, mention_author=False)
        except Exception as e:
            logger.error(f"Error during message update/reply in _resolve for pred {self.pred.msg_id}: {e}")

        self.client.predictions.pop(self.pred.msg_id, None)
        _save({k: v.to_dict() for k, v in self.client.predictions.items()})
        await interaction.response.send_message(f"✅ Prediction '{self.pred.title}' resolved. Winners: {win_side_text}. Outcomes logged.", ephemeral=True)
        self.stop()
