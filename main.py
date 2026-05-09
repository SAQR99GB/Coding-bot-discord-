import os
import discord
import requests
import asyncio
import sqlite3
import urllib.parse
import cloudscraper

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from threading import Thread
from discord.ext import commands

# =====================
# ENV LOAD
# =====================
load_dotenv()

TOKEN = os.getenv("TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
GUILD_ID = int(os.getenv("GUILD_ID"))

DISCORD_API = "https://discord.com/api"

# =====================
# BOT SETUP
# =====================
intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
app = FastAPI()

MY_GUILD = discord.Object(id=GUILD_ID)

# =====================
# DATABASE
# =====================
conn = sqlite3.connect("verified.db", check_same_thread=False)
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS verified_users (
    discord_id INTEGER PRIMARY KEY,
    epic_name TEXT
)
""")
conn.commit()

# =====================
# ROLES
# =====================
RANK_ROLES = {
    "Supersonic Legend": "Supersonic Legend",
    "Grand Champion": "Grand Champion",
    "Champion": "Champion",
    "Diamond": "Diamond",
    "Platinum": "Platinum",
    "Gold": "Gold",
    "Silver": "Silver",
    "Bronze": "Bronze",
    "Unranked": "Unranked"
}

# =====================
# TRACKER
# =====================
def fetch_tracker_data(epic_name):
    encoded = urllib.parse.quote(epic_name)
    url = f"https://api.tracker.gg/api/v2/rocket-league/standard/profile/epic/{encoded}"
    scraper = cloudscraper.create_scraper()
    return scraper.get(url)

# =====================
# FASTAPI ROUTES
# =====================
@app.get("/login")
def login():
    url = (
        f"{DISCORD_API}/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20connections"
    )
    return RedirectResponse(url)

@app.get("/callback")
async def callback(code: str):

    token = requests.post(
        f"{DISCORD_API}/oauth2/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    ).json()

    access_token = token.get("access_token")

    if not access_token:
        return {"error": "Token failed"}

    headers = {"Authorization": f"Bearer {access_token}"}

    user = requests.get(f"{DISCORD_API}/users/@me", headers=headers).json()
    connections = requests.get(f"{DISCORD_API}/users/@me/connections", headers=headers).json()

    epic = None
    for c in connections:
        if c["type"] == "epicgames":
            epic = c["name"]

    if not epic:
        return {"error": "No Epic Games linked"}

    response = await asyncio.to_thread(fetch_tracker_data, epic)

    if response.status_code != 200:
        return {"error": "Tracker failed"}

    data = response.json()
    segments = data.get("data", {}).get("segments", [])

    rank = None
    for s in segments:
        if s.get("metadata", {}).get("name") == "Ranked Doubles 2v2":
            rank = s.get("stats", {}).get("tier", {}).get("metadata", {}).get("name")

    guild = bot.get_guild(GUILD_ID)
    member = guild.get_member(int(user["id"]))

    role_name = None
    for k in RANK_ROLES:
        if rank and rank.startswith(k):
            role_name = RANK_ROLES[k]

    role = discord.utils.get(guild.roles, name=role_name)

    if role and member:
        await member.add_roles(role)

    return {
        "success": True,
        "epic": epic,
        "rank": rank
    }

# =====================
# SLASH COMMAND
# =====================
@bot.tree.command(name="link", description="Verify RL rank", guild=MY_GUILD)
async def link(interaction: discord.Interaction, epic_name: str):
    await interaction.response.defer()

    await interaction.followup.send(f"Checking rank for {epic_name}...")

# =====================
# SYNC COMMANDS
# =====================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    try:
        synced = await bot.tree.sync(guild=MY_GUILD)
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print("Sync error:", e)

# =====================
# RUN FASTAPI
# =====================
def run_api():
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

Thread(target=run_api).start()

# =====================
# RUN BOT
# =====================
bot.run(TOKEN)
