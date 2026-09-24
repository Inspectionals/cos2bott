import os, json, re, math
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

ROOT = Path(__file__).parent
DATA = json.loads((ROOT / "cos2_knowledge.json").read_text())
CASES = DATA["cases"]
MODES = DATA.get("battle_modes") or []
MODS = DATA.get("battle_modifiers") or []

TOKEN = os.getenv("DISCORD_TOKEN", "")
AI_KEY = os.getenv("OPENAI_API_KEY", "")
AI_BASE = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
AI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def find_case(q: str):
    nq = norm(q)
    if not nq:
        return None, None
    if q in CASES:
        return q, CASES[q]
    hits = []
    for name in CASES:
        nn = norm(name)
        if nq == nn:
            return name, CASES[name]
        if nq in nn or nn in nq:
            hits.append(name)
    if len(hits) == 1:
        return hits[0], CASES[hits[0]]
    if hits:
        hits.sort(key=lambda n: abs(len(n) - len(q)))
        return hits[0], CASES[hits[0]]
    return None, None


def money(n):
    if n is None:
        return "?"
    if n >= 1000:
        return f"${n:,.0f}"
    return f"${n:,.2f}"


def case_embed(name, c):
    items = c.get("items") or []
    items = sorted(items, key=lambda x: -(x.get("odds_num") or 0))
    e = discord.Embed(title=name, color=0x7C5CFF)
    price = c.get("price") or "?"
    e.add_field(name="Price", value=str(price), inline=True)
    e.add_field(name="Difficulty", value=str(c.get("difficulty") or "?"), inline=True)
    ev = c.get("ev")
    rtp = c.get("rtp")
    cov = c.get("odds_covered")
    stats = []
    if ev is not None:
        stats.append(f"EV {money(ev)}")
    if rtp:
        stats.append(f"RTP {rtp*100:.1f}%")
    if cov:
        stats.append(f"listed odds {cov:.1f}%")
    if stats:
        e.add_field(name="Math", value=" · ".join(stats), inline=False)
    lines = []
    for it in items[:15]:
        lines.append(f"`{it.get('odds','?'):>8}`  {it.get('name')} — {it.get('price','?')}")
    e.description = "\n".join(lines) if lines else "No items listed."
    if len(items) > 15:
        e.set_footer(text=f"{len(items)-15} more items")
    else:
        e.set_footer(text="RTP uses only listed rows. Hidden fillers make real RTP lower.")
    return e


def rtp_list(limit=10, min_cover=25):
    rows = []
    for name, c in CASES.items():
        if not c.get("rtp") or (c.get("odds_covered") or 0) < min_cover:
            continue
        if not c.get("price_num"):
            continue
        rows.append((c["rtp"], name, c))
    rows.sort(reverse=True)
    return rows[:limit]


def search_item(q: str, limit=15):
    nq = norm(q)
    hits = []
    for cname, c in CASES.items():
        for it in c.get("items") or []:
            if nq in norm(it.get("name") or ""):
                hits.append((cname, it))
    hits.sort(key=lambda x: -(x[1].get("odds_num") or 0))
    return hits[:limit]


def seats_for_mode(mode: str) -> int:
    m = mode.lower().strip()
    if m.startswith("big"):
        return 8
    parts = [p for p in m.split("v") if p.isdigit()]
    if parts:
        return sum(int(p) for p in parts)
    return 2


def suggest_battle(mode: str, modifier: str, budget: float):
    seats = seats_for_mode(mode)
    pool = []
    for name, c in CASES.items():
        cost = c.get("price_num")
        rtp = c.get("rtp")
        if not cost or cost <= 0 or not rtp:
            continue
        pool.append((rtp, cost, name, c))
    pool.sort(reverse=True)
    picked, spent = [], 0.0
    # pick `seats` copies? battles usually add several cases into a shared pool.
    # Use 3-6 distinct cases that fit budget.
    target_n = min(6, max(2, seats))
    for rtp, cost, name, c in pool:
        if spent + cost > budget:
            continue
        picked.append((name, cost, rtp, c))
        spent += cost
        if len(picked) >= target_n:
            break
    return picked, spent, seats


async def ask_ai(question: str) -> str:
    if not AI_KEY:
        return local_answer(question)
    import aiohttp
    # compact context: top rtp + matching cases
    ctx_cases = []
    words = [w for w in re.findall(r"[A-Za-z0-9%+\-]+", question) if len(w) > 2]
    for name, c in CASES.items():
        nn = norm(name)
        if any(norm(w) in nn for w in words):
            ctx_cases.append({
                "name": name,
                "price": c.get("price"),
                "rtp": c.get("rtp"),
                "ev": c.get("ev"),
                "items": [
                    {"name": it.get("name"), "price": it.get("price"), "odds": it.get("odds")}
                    for it in (c.get("items") or [])[:8]
                ],
            })
        if len(ctx_cases) >= 8:
            break
    top = [
        {"name": n, "price": c.get("price"), "rtp": c.get("rtp"), "ev": c.get("ev")}
        for _, n, c in rtp_list(8)
    ]
    system = (
        "You are a Case Opening Simulator 2 helper. Use ONLY the catalog data. "
        "Be direct. RTP is expected item value / case price from listed odds only "
        "(rows often do not add to 100%). "
        "Battle modes: " + ", ".join(MODES) + ". "
        "Modifiers: " + ", ".join(MODS) + ". "
        "Do not invent items or odds. If unknown, say so. "
        "Do not give exploits, cheats, or account theft advice."
    )
    user = json.dumps({"question": question, "matching_cases": ctx_cases, "top_rtp": top})
    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {AI_KEY}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as sess:
        async with sess.post(f"{AI_BASE}/chat/completions", json=payload, headers=headers, timeout=60) as r:
            body = await r.json()
            if r.status >= 400:
                return f"AI error {r.status}: {body}\n\nFallback:\n{local_answer(question)}"
            return body["choices"][0]["message"]["content"]


def local_answer(q: str) -> str:
    ql = q.lower()
    name, c = find_case(q)
    if any(w in ql for w in ("rtp", "best case", "ev", "value", "advantage")):
        rows = rtp_list(8)
        lines = ["Best listed RTP (only cases with enough visible odds):"]
        for rtp, n, cc in rows:
            lines.append(f"- {n}  cost {cc.get('price')}  EV {money(cc.get('ev'))}  RTP {rtp*100:.1f}%")
        lines.append("Real RTP is lower if listed odds do not add to 100%.")
        return "\n".join(lines)
    if "battle" in ql or "1v1" in ql or "2v2" in ql:
        return (
            "Battle layouts: " + ", ".join(MODES) + "\n"
            "Modifiers: " + ", ".join(MODS) + "\n"
            "Use /battle to build a lineup from a budget."
        )
    if name:
        items = c.get("items") or []
        lines = [f"{name} — {c.get('price') or '?'}  EV {money(c.get('ev'))}"]
        for it in items[:12]:
            lines.append(f"  {it.get('odds')}  {it.get('name')}  {it.get('price')}")
        return "\n".join(lines)
    hits = search_item(q, 8)
    if hits:
        lines = [f"Cases containing something like '{q}':"]
        for cn, it in hits:
            lines.append(f"- {it.get('name')} in {cn} ({it.get('odds')}, {it.get('price')})")
        return "\n".join(lines)
    return "Try /case, /rtp, /search, /battle, or name a case."


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
    except Exception as e:
        print("sync failed", e)
    print("ready", bot.user, "cases", len(CASES))


@bot.tree.command(description="Show a case: items, odds, EV, RTP")
@app_commands.describe(name="Case name, e.g. Positive Case")
async def case(interaction: discord.Interaction, name: str):
    n, c = find_case(name)
    if not c:
        await interaction.response.send_message(f"No case matching `{name}`.", ephemeral=True)
        return
    await interaction.response.send_message(embed=case_embed(n, c))


@bot.tree.command(description="Best cases by listed RTP")
@app_commands.describe(limit="How many to list (max 20)")
async def rtp(interaction: discord.Interaction, limit: int = 10):
    rows = rtp_list(max(1, min(limit, 20)))
    e = discord.Embed(title="Best listed RTP", color=0x32E062)
    lines = []
    for r, n, c in rows:
        lines.append(f"**{n}** — {c.get('price')} · EV {money(c.get('ev'))} · **{r*100:.1f}%**")
    e.description = "\n".join(lines) or "No ranked cases."
    e.set_footer(text="Listed odds often < 100%, so this is a ceiling-style estimate.")
    await interaction.response.send_message(embed=e)


@bot.tree.command(description="Find which cases contain an item")
@app_commands.describe(item="Item name, e.g. Motocross")
async def search(interaction: discord.Interaction, item: str):
    hits = search_item(item, 20)
    if not hits:
        await interaction.response.send_message(f"No item matching `{item}`.", ephemeral=True)
        return
    e = discord.Embed(title=f"Search: {item}", color=0x5B9DFF)
    lines = [f"**{it.get('name')}** — {cn} · {it.get('odds')} · {it.get('price')}" for cn, it in hits]
    e.description = "\n".join(lines)
    await interaction.response.send_message(embed=e)


@bot.tree.command(description="Suggest a case battle lineup")
@app_commands.describe(mode="1v1, 2v2, 3v3v3, 4v4, 5v5...", modifier="Normal / Inverted / Rumble...", budget="Max $ to spend on cases")
@app_commands.choices(mode=[app_commands.Choice(name=m, value=m) for m in MODES[:24]])
@app_commands.choices(modifier=[app_commands.Choice(name=m, value=m) for m in MODS])
async def battle(interaction: discord.Interaction, mode: str, modifier: str, budget: float):
    picked, spent, seats = suggest_battle(mode, modifier, budget)
    e = discord.Embed(title=f"{mode} · {modifier}", color=0xF1A228)
    if not picked:
        e.description = "No priced cases fit that budget."
    else:
        lines = [f"**{n}** — {money(cost)} · listed RTP {rtp*100:.1f}%" for n, cost, rtp, _ in picked]
        e.description = "\n".join(lines)
        e.add_field(name="Seats", value=str(seats), inline=True)
        e.add_field(name="Spent", value=money(spent), inline=True)
        e.add_field(name="Left", value=money(budget - spent), inline=True)
    if modifier.lower() == "inverted":
        e.set_footer(text="Inverted: worse items are favored. High-RTP trash cases can flip.")
    elif modifier.lower() == "crazy":
        e.set_footer(text="Crazy: odds get scrambled. Treat RTP as a rough hint only.")
    await interaction.response.send_message(embed=e)


@bot.tree.command(description="Ask anything about COS2 cases / battles / RTP")
@app_commands.describe(question="Your question")
async def ask(interaction: discord.Interaction, question: str):
    await interaction.response.defer(thinking=True)
    text = await ask_ai(question)
    if len(text) > 1900:
        text = text[:1900] + "…"
    await interaction.followup.send(text)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if bot.user and bot.user.mentioned_in(message):
        q = message.content
        q = q.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
        if q:
            async with message.channel.typing():
                text = await ask_ai(q)
            if len(text) > 1900:
                text = text[:1900] + "…"
            await message.reply(text)
    await bot.process_commands(message)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN")
    bot.run(TOKEN)
