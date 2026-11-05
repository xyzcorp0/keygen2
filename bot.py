"""
bot.py — Bot Discord + API HTTP
Lance les deux en parallèle dans le même process.

Dépendances :
    pip install discord.py flask

Variables à configurer (section CONFIG ci-dessous) :
    DISCORD_TOKEN   — token de ton bot Discord
    ADMIN_ID        — ton user ID Discord (clic droit → Copier l'ID)
    API_SECRET      — secret partagé avec snipe.py (change-le)
    API_PORT        — port sur lequel l'API écoute (défaut 5000)
"""

import sqlite3
import threading
import os
from datetime import datetime

import discord
from discord.ext import commands
from flask import Flask, request, jsonify

from keep_alive import keep_alive

# ─────────────────────────────────────────────
# CONFIG — modifie ces 4 valeurs
# ─────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ID      = 242887061055602688          # ton ID Discord (int)
API_SECRET    = "keygentesting" # même valeur que dans snipe.py
API_PORT      = 5000
# ─────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "licenses.db")

# ══════════════════════════════════════════════
# DB
# ══════════════════════════════════════════════

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            key          TEXT PRIMARY KEY,
            hwid         TEXT DEFAULT NULL,
            status       TEXT DEFAULT 'inactive',
            created_at   TEXT,
            activated_at TEXT DEFAULT NULL
        )
    """)
    con.commit()
    con.close()

def db():
    return sqlite3.connect(DB_PATH)

# ══════════════════════════════════════════════
# API Flask (appelée par snipe.py)
# ══════════════════════════════════════════════

app = Flask(__name__)

def check_secret(req):
    return req.headers.get("X-API-Secret") == API_SECRET

@app.route("/activate", methods=["POST"])
def activate():
    if not check_secret(request):
        return jsonify({"ok": False, "msg": "unauthorized"}), 403

    data = request.get_json(force=True)
    key  = data.get("key", "").strip().upper()
    hwid = data.get("hwid", "").strip().upper()

    if not key or not hwid:
        return jsonify({"ok": False, "msg": "missing fields"}), 400

    with db() as con:
        row = con.execute("SELECT status, hwid FROM keys WHERE key=?", (key,)).fetchone()

    if not row:
        return jsonify({"ok": False, "msg": "invalid_key"})

    status, bound_hwid = row

    if status == "revoked":
        return jsonify({"ok": False, "msg": "key_revoked"})

    if status == "active":
        if bound_hwid == hwid:
            # même machine qui relance
            return jsonify({"ok": True, "msg": "already_bound", "hwid": hwid})
        else:
            return jsonify({"ok": False, "msg": "hwid_mismatch"})

    # inactive → on bind
    with db() as con:
        con.execute(
            "UPDATE keys SET status='active', hwid=?, activated_at=? WHERE key=?",
            (hwid, datetime.utcnow().isoformat(), key)
        )

    return jsonify({"ok": True, "msg": "activated", "hwid": hwid})


@app.route("/verify", methods=["POST"])
def verify():
    if not check_secret(request):
        return jsonify({"ok": False, "msg": "unauthorized"}), 403

    data = request.get_json(force=True)
    key  = data.get("key", "").strip().upper()
    hwid = data.get("hwid", "").strip().upper()

    with db() as con:
        row = con.execute("SELECT status, hwid FROM keys WHERE key=?", (key,)).fetchone()

    if not row:
        return jsonify({"ok": False, "msg": "invalid_key"})

    status, bound_hwid = row

    if status == "revoked":
        return jsonify({"ok": False, "msg": "key_revoked"})

    if status == "active" and bound_hwid == hwid:
        return jsonify({"ok": True, "msg": "ok"})

    return jsonify({"ok": False, "msg": "hwid_mismatch"})


def run_api():
    app.run(host="0.0.0.0", port=API_PORT, debug=False, use_reloader=False)

# ══════════════════════════════════════════════
# Bot Discord
# ══════════════════════════════════════════════

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def is_admin(ctx):
    return ctx.author.id == ADMIN_ID

@bot.event
async def on_ready():
    print(f"[Bot] Connecté : {bot.user}")
    print(f"[API] En écoute sur le port {API_PORT}")

# !genkey [n]  — génère n clés et les affiche en DM
@bot.command(name="genkey")
async def genkey(ctx, count: int = 1):
    if not is_admin(ctx):
        return
    if count < 1 or count > 50:
        await ctx.send("Entre 1 et 50 clés max.")
        return

    import random, string
    from datetime import datetime

    def gen():
        chars = string.ascii_uppercase + string.digits
        return '-'.join(''.join(random.choices(chars, k=5)) for _ in range(4))

    keys = []
    with db() as con:
        for _ in range(count):
            while True:
                k = gen()
                if not con.execute("SELECT 1 FROM keys WHERE key=?", (k,)).fetchone():
                    break
            con.execute("INSERT INTO keys (key, created_at) VALUES (?, ?)",
                        (k, datetime.utcnow().isoformat()))
            keys.append(k)

    msg = f"**{count} clé(s) générée(s) :**\n```\n" + "\n".join(keys) + "\n```"
    await ctx.author.send(msg)
    await ctx.message.add_reaction("✅")

# !keyinfo <clé>
@bot.command(name="keyinfo")
async def keyinfo(ctx, key: str = ""):
    if not is_admin(ctx):
        return
    key = key.upper()
    with db() as con:
        row = con.execute(
            "SELECT status, hwid, created_at, activated_at FROM keys WHERE key=?", (key,)
        ).fetchone()
    if not row:
        await ctx.send("❌ Clé introuvable.")
        return
    status, hwid, created, activated = row
    await ctx.send(
        f"**Clé :** `{key}`\n"
        f"**Statut :** {status}\n"
        f"**HWID :** {hwid or '—'}\n"
        f"**Créée :** {created}\n"
        f"**Activée :** {activated or '—'}"
    )

# !revoke <clé>
@bot.command(name="revoke")
async def revoke(ctx, key: str = ""):
    if not is_admin(ctx):
        return
    key = key.upper()
    with db() as con:
        row = con.execute("SELECT status FROM keys WHERE key=?", (key,)).fetchone()
        if not row:
            await ctx.send("❌ Clé introuvable.")
            return
        con.execute("UPDATE keys SET status='revoked' WHERE key=?", (key,))
    await ctx.send(f"🔴 Clé `{key}` révoquée.")

# !keys  — liste toutes les clés
@bot.command(name="keys")
async def list_keys(ctx):
    if not is_admin(ctx):
        return
    with db() as con:
        rows = con.execute(
            "SELECT key, status, hwid FROM keys ORDER BY created_at DESC LIMIT 30"
        ).fetchall()
    if not rows:
        await ctx.send("Aucune clé en DB.")
        return
    lines = []
    for key, status, hwid in rows:
        icon = {"inactive": "⚪", "active": "🟢", "revoked": "🔴"}.get(status, "❓")
        lines.append(f"{icon} `{key}` — {hwid or '—'}")
    await ctx.send("**Clés (30 dernières) :**\n" + "\n".join(lines))

# ══════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════

if __name__ == "__main__":
    init_db()
    # API dans un thread séparé
    t = threading.Thread(target=run_api, daemon=True)
    t.start()
    # Bot Discord dans le thread principal

    keep_alive()
    if not DISCORD_TOKEN:
        raise RuntimeError("La variable DISCORD_TOKEN est absente des variables d’environnement.")
    bot.run(DISCORD_TOKEN)
