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

# We add .strip() to prevent hidden spaces from breaking the URL
TOKEN = os.getenv("TOKEN").strip()
CLIENT_ID = os.getenv("CLIENT_ID").strip()
CLIENT_SECRET = os.getenv("CLIENT_SECRET").strip()
REDIRECT_URI = os.getenv("REDIRECT_URI").strip()
GUILD_ID = int(os.getenv("GUILD_ID").strip())

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(bot.start(TOKEN))
    yield
    await bot.close()

app = FastAPI(lifespan=lifespan)

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
    # FIX: We use urlencode on a dictionary to ensure EVERY character 
    # (including slashes) is perfectly encoded for Discord.
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify connections"
    }
    query_string = urllib.parse.urlencode(params)
    url = f"{DISCORD_API}/oauth2/authorize?{query_string}"
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
        return {"error": "Failed to get Discord token", "debug": token_data}

    headers = {"Authorization": f"Bearer {access_token}"}
    user = requests.get(f"{DISCORD_API}/users/@me", headers=headers).json()
    connections = requests.get(f"{DISCORD_API}/users/@me/connections", headers=headers).json()

    epic_connected_name = None
    for conn_item in connections:
        if conn_item["type"] == "epicgames":
            epic_connected_name = conn_item["name"]
            break

    if not epic_connected_name:
        return {"error": "No Epic Games account is connected to your Discord profile. Please add it in Discord Settings > Connections."}

    c.execute("INSERT OR REPLACE INTO verified_users (discord_id, epic_name) VALUES (?, ?)", 
              (int(user["id"]), epic_connected_name))
    conn.commit()

    return {"success": True, "message": f"Successfully verified as Epic User: {epic_connected_name}. You can now use /link in the server!"}

# =====================
# SLASH COMMAND (The Comparison Logic)
# =====================
@bot.tree.command(name="link", description="Verify RL rank", guild=MY_GUILD)
async def link(interaction: discord.Interaction, epic_name: str):
    await interaction.response.defer()

    c.execute("SELECT epic_name FROM verified_users WHERE discord_id = ?", (interaction.user.id,))
    row = c.fetchone()
    
    if not row:
        # Construct the login URL dynamically based on current domain
        login_url = f"{REDIRECT_URI.rsplit('/', 1)[0]}/login"
        return await interaction.followup.send(f"❌ You haven't verified your Epic connection yet! Link here: {login_url}")

    connected_epic = row[0]

    # THE COMPARISON: Provided name vs Real Connected name
    if epic_name.lower() != connected_epic.lower():
        return await interaction.followup.send(
            f"❌ **Error:** The name you provided (`{epic_name}`) doesn't match your connected Discord account (`{connected_epic}`). "
            "Please use the exact name shown on your Discord profile connections!"
        )

    await interaction.followup.send(f"✅ Identity verified! Fetching rank for `{connected_epic}`...")
    
    response = await asyncio.to_thread(fetch_tracker_data, connected_epic)
    if response.status_code != 200:
        return await interaction.followup.send("❌ Tracker API failed. Ensure your Epic profile is Public on Tracker.gg.")

    data = response.json()
    segments = data.get("data", {}).get("segments", [])

    rank = None
    for s in segments:
        if s.get("metadata", {}).get("name") == "Ranked Doubles 2v2":
            rank = s.get("stats", {}).get("tier", {}).get("metadata", {}).get("name")
            break

    if not rank:
        return await interaction.followup.send(f"❌ No 2v2 rank data found for `{connected_epic}`.")

    # Map rank to role
    role_name = next((RANK_ROLES[k] for k in RANK_ROLES if rank.startswith(k)), None)
    role = discord.utils.get(interaction.guild.roles, name=role_name)

    if role:
        try:
            # Cleanup old ranks
            old_roles = [r for r in interaction.user.roles if r.name in RANK_ROLES.values() and r.id != role.id]
            if old_roles: await interaction.user.remove_roles(*old_roles)
            
            await interaction.user.add_roles(role)
            await interaction.followup.send(f"🎉 Rank verified: **{rank}**. Your role has been updated!")
        except discord.Forbidden:
            await interaction.followup.send("❌ Permission Error: Move my Bot Role to the TOP of the role list!")
    else:
        await interaction.followup.send(f"⚠️ Found rank {rank}, but no matching role exists in this server.")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.tree.sync(guild=MY_GUILD)

if __name__ == "__main__":
    # Standard Railway port logic
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
