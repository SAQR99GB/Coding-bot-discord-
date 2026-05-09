import os
import discord
import requests
import asyncio
import sqlite3
import urllib.parse
import cloudscraper
import uvicorn

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from discord.ext import commands
from contextlib import asynccontextmanager

# =====================
# ENV & CONFIG
# =====================
load_dotenv()

TOKEN = os.getenv("TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
GUILD_ID = int(os.getenv("GUILD_ID"))

DISCORD_API = "https://discord.com/api"

# =====================
# DATABASE SETUP
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
# DISCORD BOT SETUP
# =====================
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
MY_GUILD = discord.Object(id=GUILD_ID)

# =====================
# FASTAPI & LIFESPAN (FIXES RUNTIME ERROR)
# =====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # This runs when the server starts
    asyncio.create_task(bot.start(TOKEN))
    yield
    # This runs when the server stops
    await bot.close()

app = FastAPI(lifespan=lifespan)

# =====================
# RANK MAP
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

def fetch_tracker_data(epic_name):
    encoded = urllib.parse.quote(epic_name)
    url = f"https://api.tracker.gg/api/v2/rocket-league/standard/profile/epic/{encoded}"
    scraper = cloudscraper.create_scraper()
    return scraper.get(url)

# =====================
# FASTAPI ROUTES (OAuth2)
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
    # Exchange code for token
    token_data = requests.post(
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

    access_token = token_data.get("access_token")
    if not access_token:
        return {"error": "Failed to get Discord token"}

    headers = {"Authorization": f"Bearer {access_token}"}
    user = requests.get(f"{DISCORD_API}/users/@me", headers=headers).json()
    connections = requests.get(f"{DISCORD_API}/users/@me/connections", headers=headers).json()

    # Find the Epic Games connection name
    epic_connected_name = None
    for conn_item in connections:
        if conn_item["type"] == "epicgames":
            epic_connected_name = conn_item["name"]
            break

    if not epic_connected_name:
        return {"error": "No Epic Games account is connected to your Discord profile."}

    # Save the REAL connected name to the database
    c.execute("INSERT OR REPLACE INTO verified_users (discord_id, epic_name) VALUES (?, ?)", 
              (int(user["id"]), epic_connected_name))
    conn.commit()

    return {"success": True, "message": f"Successfully linked! You can now use /link in Discord with the name: {epic_connected_name}"}

# =====================
# SLASH COMMAND (The Comparison Logic)
# =====================
@bot.tree.command(name="link", description="Verify RL rank", guild=MY_GUILD)
async def link(interaction: discord.Interaction, epic_name: str):
    await interaction.response.defer()

    # 1. Check if they have done the OAuth2 /login
    c.execute("SELECT epic_name FROM verified_users WHERE discord_id = ?", (interaction.user.id,))
    row = c.fetchone()
    
    if not row:
        login_url = REDIRECT_URI.replace("/callback", "/login")
        return await interaction.followup.send(f"❌ You must first link your Epic account here: {login_url}")

    connected_epic = row[0]

    # 2. COMPARE: Provided name vs Connected name
    if epic_name.lower() != connected_epic.lower():
        return await interaction.followup.send(f"❌ Error: The name you provided (`{epic_name}`) doesn't match the account connected to your Discord (`{connected_epic}`).")

    # 3. Proceed if they match
    await interaction.followup.send(f"✅ Identity verified! Fetching rank for `{connected_epic}`...")
    
    response = await asyncio.to_thread(fetch_tracker_data, connected_epic)
    if response.status_code != 200:
        return await interaction.followup.send("❌ Tracker API failed. Make sure your profile is public on Tracker.gg.")

    data = response.json()
    segments = data.get("data", {}).get("segments", [])

    rank = None
    for s in segments:
        if s.get("metadata", {}).get("name") == "Ranked Doubles 2v2":
            rank = s.get("stats", {}).get("tier", {}).get("metadata", {}).get("name")
            break

    if not rank:
        return await interaction.followup.send(f"❌ No 2v2 rank found for `{connected_epic}`.")

    # 4. Map and Assign Role
    role_name = next((RANK_ROLES[k] for k in RANK_ROLES if rank.startswith(k)), None)
    role = discord.utils.get(interaction.guild.roles, name=role_name)

    if role:
        try:
            # Remove old rank roles
            old_roles = [r for r in interaction.user.roles if r.name in RANK_ROLES.values() and r.id != role.id]
            if old_roles: await interaction.user.remove_roles(*old_roles)
            
            await interaction.user.add_roles(role)
            await interaction.followup.send(f"🎉 Rank verified: **{rank}**. Role assigned!")
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to manage your roles.")
    else:
        await interaction.followup.send(f"⚠️ Could not find a server role for the rank: {rank}")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.tree.sync(guild=MY_GUILD)

# =====================
# RUN APPLICATION
# =====================
if __name__ == "__main__":
    # Start the FastAPI server (which handles the bot via lifespan)
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
