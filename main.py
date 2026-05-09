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

# LOAD ENV
load_dotenv()

TOKEN = os.getenv("TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
GUILD_ID = int(os.getenv("GUILD_ID"))

DISCORD_API = "https://discord.com/api"

# BOT SETUP
intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

app = FastAPI()

# DATABASE
conn = sqlite3.connect("verified.db")
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS verified_users (
    discord_id INTEGER PRIMARY KEY,
    epic_name TEXT
)
""")
conn.commit()
conn.close()

# ROLES
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

# RL TRACKER
def fetch_tracker_data(epic_name):
    encoded_name = urllib.parse.quote(epic_name)
    url = f"https://api.tracker.gg/api/v2/rocket-league/standard/profile/epic/{encoded_name}"

    scraper = cloudscraper.create_scraper()
    return scraper.get(url)

# LOGIN ROUTE
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

# CALLBACK ROUTE
@app.get("/callback")
async def callback(code: str):

    token_data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    token_response = requests.post(
        f"{DISCORD_API}/oauth2/token",
        data=token_data,
        headers=headers
    )

    token_json = token_response.json()

    access_token = token_json["access_token"]

    user_headers = {
        "Authorization": f"Bearer {access_token}"
    }

    user = requests.get(
        f"{DISCORD_API}/users/@me",
        headers=user_headers
    ).json()

    connections = requests.get(
        f"{DISCORD_API}/users/@me/connections",
        headers=user_headers
    ).json()

    epic_name = None

    for connection in connections:
        if connection["type"] == "epicgames":
            epic_name = connection["name"]
            break

    if not epic_name:
        return {"error": "No Epic Games account connected to Discord."}

    response = await asyncio.to_thread(fetch_tracker_data, epic_name)

    if response.status_code != 200:
        return {"error": "Could not fetch RL rank."}

    data = response.json()

    segments = data.get('data', {}).get('segments', [])

    twos_rank = None

    for segment in segments:
        if segment.get('metadata', {}).get('name') == "Ranked Doubles 2v2":
            stats = segment.get('stats', {})
            twos_rank = stats.get('tier', {}).get('metadata', {}).get('name')
            break

    if not twos_rank:
        return {"error": "No 2v2 rank found."}

    base_rank = None

    for rank_key in RANK_ROLES.keys():
        if twos_rank.startswith(rank_key):
            base_rank = RANK_ROLES[rank_key]
            break

    guild = bot.get_guild(GUILD_ID)

    member = guild.get_member(int(user["id"]))

    role = discord.utils.get(guild.roles, name=base_rank)

    if role:
        await member.add_roles(role)

    return {
        "success": True,
        "epic_name": epic_name,
        "rank": twos_rank
    }

# BOT READY
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

# START FASTAPI
def run_api():
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)   

Thread(target=run_api).start()

# RUN BOT
bot.run(TOKEN)