import os
import sys
import re
import json
import random
import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

# Load .env from project root or backend folder
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
load_dotenv()

import pandas as pd
from fastapi import FastAPI, Request, Response, HTTPException, Header, Depends, Query, Path, status
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from models import (
    AttendeeCreate, AttendeeResponse, AttendeeRegisterResult,
    TerminateQrRequest, ReinstateQrRequest,
    ScanRequest, ScanResponse,
    TransactionRequest, TransactionResponse,
    SocialPost, FrictionAlert,
    RechargeRequest, RechargeResponse, RechargesListResponse,
    IncidentCreate, IncidentStatusUpdate, IncidentResponse,
    DiversionCreate, DiversionResponse, ThrottleRequest,
    NLPQueryRequest, NLPQueryResponse, MetricItem, QuickActionItem,
    AdminStatsResponse, AttendeeHistoryResponse, HealthCheckResponse
)
from database import db_manager, ZONES, ROLES, BOTTLENECK_THRESHOLD, SCAN_ENGAGEMENT_POINTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("cultura.server")

# Admin Security Key
ADMIN_ACCESS_KEY = os.getenv("ADMIN_KEY", "").strip()

def verify_admin(x_admin_key: Optional[str] = Header(None, alias="x-admin-key")):
    if not ADMIN_ACCESS_KEY or not x_admin_key or x_admin_key.strip() != ADMIN_ACCESS_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized Access")
    return True

# Dedicated Field Crew Key for on-site personnel
FIELD_CREW_KEY = os.getenv("FIELD_CREW_KEY", "FIELD-CREW-KEY").strip()

def verify_field_or_admin(
    x_field_crew_key: Optional[str] = Header(None, alias="x-field-crew-key"),
    x_admin_key: Optional[str] = Header(None, alias="x-admin-key"),
):
    # Check Admin authorization
    if ADMIN_ACCESS_KEY and x_admin_key and x_admin_key.strip() == ADMIN_ACCESS_KEY:
        return True

    # Check Field Crew authorization
    if FIELD_CREW_KEY:
        if x_field_crew_key and x_field_crew_key.strip() == FIELD_CREW_KEY:
            return True
        if x_admin_key and x_admin_key.strip() == FIELD_CREW_KEY:
            return True

    raise HTTPException(
        status_code=401,
        detail="Unauthorized: Valid field crew or admin credentials required"
    )

# ─────────────────────────────────────────────────────────────────────────────
# 1. AUTHENTIC CROWD MOVEMENT (DinoFun World VAST Challenge Dataset Integration)
# ─────────────────────────────────────────────────────────────────────────────
# Attraction to festival zone mapping taxonomy
ATTRACTION_ZONE_MAP: Dict[str, str] = {
    # Thrill Rides & Stages -> Main Stage
    "Flight of the Falcon": "Main Stage",
    "Galactic Coaster": "Main Stage",
    "Terror Trove": "Main Stage",
    "Thunder Mountain": "Main Stage",
    "Acrobat Amphitheater": "Main Stage",
    "Main Stage": "Main Stage",
    "Rock Amphitheater": "Main Stage",
    "Sky Coaster": "Main Stage",
    "Drop Tower": "Main Stage",
    "Stunt Show": "Main Stage",
    "Firework Plaza": "Main Stage",
    "Atmosfear": "Main Stage",
    "Wipeout": "Main Stage",
    "Kite Flyer": "Main Stage",
    "Thrill Rides": "Main Stage",

    # Food Village & Stalls -> Food Court
    "Food Village": "Food Court",
    "Dino Diner": "Food Court",
    "Paleo Pizza": "Food Court",
    "Bronto Burger": "Food Court",
    "Carnivore Grill": "Food Court",
    "Pterodactyl Tacos": "Food Court",
    "Ice Cream Oasis": "Food Court",
    "Beer Garden": "Food Court",
    "Sweet Treats": "Food Court",
    "Coffee Kiosk": "Food Court",
    "Tavern": "Food Court",
    "Cretaceous Cafe": "Food Court",
    "Snack Shack": "Food Court",

    # Souvenirs & Crafts -> Artisan Market
    "Dino Souvenirs": "Artisan Market",
    "Jurassic Crafts": "Artisan Market",
    "Artisan Bazaar": "Artisan Market",
    "Amber Emporium": "Artisan Market",
    "Gift Shop": "Artisan Market",
    "Traders Post": "Artisan Market",
    "Silk & Spice": "Artisan Market",
    "Fossil Finds": "Artisan Market",
    "Apparel Mart": "Artisan Market",

    # Discovery & Exhibitions -> Cultural Pavilion
    "Cultural Pavilion": "Cultural Pavilion",
    "Prehistoric Museum": "Cultural Pavilion",
    "Discovery Center": "Cultural Pavilion",
    "Dino Safari": "Cultural Pavilion",
    "Kiddie Carousel": "Cultural Pavilion",
    "Fossil Dig Experience": "Cultural Pavilion",
    "Herbivore Walkway": "Cultural Pavilion",
    "Science Dome": "Cultural Pavilion",
    "Heritage Hall": "Cultural Pavilion",

    # VIP Lounges -> VIP Lounge
    "VIP Lounge": "VIP Lounge",
    "Rex Club VIP": "VIP Lounge",
    "Fast Pass Lounge": "VIP Lounge",
    "Executive Terrace": "VIP Lounge",
    "Sky Deck Lounge": "VIP Lounge",
    "Sponsor Oasis": "VIP Lounge",
    "Platinum Veranda": "VIP Lounge",
    "Clubhouse": "VIP Lounge",
}

def map_attraction_to_zone(attraction_name: str) -> str:
    if not attraction_name or not isinstance(attraction_name, str):
        return "Food Court"
    name = attraction_name.strip()
    if name in ATTRACTION_ZONE_MAP:
        return ATTRACTION_ZONE_MAP[name]
    low = name.lower()
    if any(k in low for k in ["thrill", "coaster", "falcon", "terror", "stage", "theater", "music", "rock", "amphi", "tower", "drop", "stunt", "ride"]):
        return "Main Stage"
    if any(k in low for k in ["food", "diner", "pizza", "burger", "grill", "taco", "cream", "beer", "drink", "snack", "cafe", "eatery", "tavern"]):
        return "Food Court"
    if any(k in low for k in ["souvenir", "craft", "bazaar", "amber", "gift", "trader", "fossil", "market", "shop", "apparel", "merch"]):
        return "Artisan Market"
    if any(k in low for k in ["museum", "pavilion", "safari", "discovery", "carousel", "science", "dome", "gallery", "culture", "kiddie", "walk"]):
        return "Cultural Pavilion"
    if any(k in low for k in ["vip", "lounge", "club", "executive", "pass", "platinum", "oasis", "suite", "terrace", "deck"]):
        return "VIP Lounge"
    digest_val = int(hashlib.sha256(name.encode("utf-8")).hexdigest(), 16)
    return ZONES[digest_val % len(ZONES)]

class DinoFunMovementSimulator:
    def __init__(self):
        self.df: Optional[pd.DataFrame] = None
        self.current_index = 0
        self.dataset_loaded = False

    def load_dataset(self):
        paths_to_check = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "dinofun_checkins.csv"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "dinofun_checkins.csv"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "dinofun_checkins.csv"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dinofun_checkins.csv"),
        ]
        chosen_path = next((p for p in paths_to_check if os.path.exists(p)), None)
        if not chosen_path:
            # Self-generate sample DinoFun dataset so it functions out-of-the-box
            target_path = paths_to_check[0]
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            from seed_real_traffic import generate_sample_dinofun_csv
            chosen_path = generate_sample_dinofun_csv(target_path, num_records=3000)

        try:
            logger.info(f"Loading DinoFun World check-in dataset from: {chosen_path}")
            df = pd.read_csv(chosen_path)
            # Normalize column names
            col_map = {}
            for col in df.columns:
                c_low = col.strip().lower()
                if "time" in c_low: col_map[col] = "Timestamp"
                elif "visitor" in c_low or "id" in c_low or "cust" in c_low: col_map[col] = "Visitor_ID"
                elif "attract" in c_low or "loc" in c_low or "place" in c_low or "name" in c_low: col_map[col] = "Attraction_Name"
            df = df.rename(columns=col_map)
            self.df = df
            self.dataset_loaded = True
            logger.info(f"Successfully loaded {len(df)} DinoFun World check-in rows.")
        except Exception as e:
            logger.error(f"Error loading DinoFun dataset: {e}")
            self.dataset_loaded = False

    def get_next_movement(self) -> Optional[Dict[str, Any]]:
        if not self.dataset_loaded or self.df is None or len(self.df) == 0:
            return None
        row = self.df.iloc[self.current_index % len(self.df)]
        self.current_index += 1
        attraction = str(row.get("Attraction_Name", "Main Stage"))
        zone = map_attraction_to_zone(attraction)
        return {
            "visitor_id": str(row.get("Visitor_ID", f"VISITOR_{random.randint(1, 150)}")),
            "attraction_name": attraction,
            "zone_name": zone,
            "timestamp": str(row.get("Timestamp", datetime.now(timezone.utc).isoformat()))
        }

dino_simulator = DinoFunMovementSimulator()

async def crowd_movement_simulation_loop():
    logger.info("Starting DinoFun World authentic crowd movement simulation loop (3.5s interval)...")
    dino_simulator.load_dataset()

    while True:
        try:
            await asyncio.sleep(3.5)
            movement = dino_simulator.get_next_movement()
            att_list = await db_manager.list_attendees(limit=250)

            if movement and att_list:
                # Pick matching or random active attendee
                att = random.choice(att_list)
                if att.get('status') == 'terminated':
                    continue

                zone = movement["zone_name"]

                # Check active diversions and re-route if diversion is active
                active_div = next(
                    (d for d in db_manager.mem_diversions if d.get('active') and d.get('zone_name') == zone),
                    None
                )
                if active_div and random.random() < 0.65:
                    zone = active_div.get('target_zone', 'Food Court')

                await db_manager.log_zone_scan(att, zone)
        except asyncio.CancelledError:
            logger.info("Crowd movement simulation task cancelled.")
            break
        except Exception as e:
            logger.warning(f"Error in crowd movement loop: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. CASHLESS FEST COIN ECONOMY (Kaggle Retail Transactions Integration)
# ─────────────────────────────────────────────────────────────────────────────
class KaggleTransactionSimulator:
    def __init__(self):
        self.df: Optional[pd.DataFrame] = None
        self.current_index = 0
        self.dataset_loaded = False

    def load_dataset(self):
        paths_to_check = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "kaggle_retail_transactions.csv"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "kaggle_retail_transactions.csv"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "kaggle_retail_transactions.csv"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kaggle_retail_transactions.csv"),
        ]
        chosen_path = next((p for p in paths_to_check if os.path.exists(p)), None)
        if not chosen_path:
            # Fallback auto-creation
            target_path = paths_to_check[0]
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            categories = ['Food & Beverage', 'Fest Merchandise', 'Beverages & Cocktails', 'Artisan Crafts', 'Street Snacks', 'VIP Lounge Cocktails']
            rows = []
            for i in range(1, 1000):
                rows.append({
                    'Transaction_ID': f'TXN-{100000 + i}',
                    'Customer_ID': f'CUST-{random.randint(1, 300):04d}',
                    'Amount': random.randint(40, 250),
                    'Category': random.choice(categories)
                })
            df_gen = pd.DataFrame(rows)
            df_gen.to_csv(target_path, index=False)
            chosen_path = target_path

        try:
            logger.info(f"Loading Kaggle Retail Transactions from: {chosen_path}")
            df = pd.read_csv(chosen_path)
            self.df = df
            self.dataset_loaded = True
            logger.info(f"Loaded {len(df)} Kaggle retail transactions.")
        except Exception as e:
            logger.error(f"Error loading Kaggle dataset: {e}")
            self.dataset_loaded = False

    def get_next_transaction(self) -> Optional[Dict[str, Any]]:
        if not self.dataset_loaded or self.df is None or len(self.df) == 0:
            return None
        row = self.df.iloc[self.current_index % len(self.df)]
        self.current_index += 1
        cat = str(row.get("Category", "Food & Beverage"))
        raw_amt = float(row.get("Amount", 50))
        amt = max(10, min(int(raw_amt), 300))

        # Map retail Category to Festival Stall & Zone
        low = cat.lower()
        if "vip" in low or "cocktail" in low:
            stall = "VIP Lounge - Signature Cocktails"
            zone = "VIP Lounge"
        elif "merch" in low or "apparel" in low or "craft" in low:
            stall = "Artisan Market - Silk & Spice Merch"
            zone = "Artisan Market"
        elif "beverage" in low or "drink" in low:
            stall = "Main Stage - Beverage Bar"
            zone = "Main Stage"
        elif "culture" in low or "art" in low:
            stall = "Cultural Pavilion - Craft Hub"
            zone = "Cultural Pavilion"
        else:
            stall = "Food Court - Saffron Grill & Eatery"
            zone = "Food Court"

        return {
            "transaction_id": str(row.get("Transaction_ID", f"TXN-{random.randint(10000, 99999)}")),
            "customer_id": str(row.get("Customer_ID", f"CUST-{random.randint(1, 250)}")),
            "amount": amt,
            "category": cat,
            "stall_name": stall,
            "zone_name": zone,
        }

kaggle_simulator = KaggleTransactionSimulator()

async def kaggle_transaction_simulation_loop():
    logger.info("Starting Kaggle retail transactions background simulation loop (3.0s interval)...")
    kaggle_simulator.load_dataset()

    while True:
        try:
            await asyncio.sleep(3.0)
            tx_data = kaggle_simulator.get_next_transaction()
            if not tx_data:
                continue

            att_list = await db_manager.list_attendees(limit=250)
            if not att_list:
                continue

            att = random.choice(att_list)
            if att.get('status') == 'terminated':
                continue

            # Ensure attendee has sufficient coins; if low, auto-recharge
            current_balance = att.get('fest_coins', 0)
            req_amount = tx_data["amount"]
            if current_balance < req_amount:
                # Auto-recharge via UPI
                await db_manager.create_recharge(
                    attendee=att,
                    inr_amount=500,
                    payment_method="UPI - Auto Reload",
                    operator_mode="self_service",
                    notes="Autonomous cashless liquidity reload"
                )
                att = await db_manager.get_attendee_by_id(att['_id']) or att

            # Execute coin deduction
            await db_manager.create_transaction(
                attendee=att,
                amount=req_amount,
                stall_name=tx_data["stall_name"],
                zone_name=tx_data["zone_name"]
            )
        except asyncio.CancelledError:
            logger.info("Kaggle transaction simulator task cancelled.")
            break
        except Exception as e:
            logger.warning(f"Error in Kaggle transaction simulator: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. LIVE SENTIMENT TRIAGE & FRICTION ALERT (Hugging Face validation.json)
# ─────────────────────────────────────────────────────────────────────────────
class SocialSentimentManager:
    def __init__(self):
        self.raw_posts: List[Dict[str, Any]] = []
        self.positive_pool: List[Dict[str, Any]] = []
        self.neutral_pool: List[Dict[str, Any]] = []
        self.negative_pool: List[Dict[str, Any]] = []
        self.recent_stream: List[Dict[str, Any]] = []
        self.threshold: int = 8  # Elevated resistance threshold (requires >=8 complaints in 60s)
        self.cooldown_until: Optional[datetime] = None  # Prevents alert flapping & false alarms
        self.friction_alert: Dict[str, Any] = {
            "active": False,
            "negative_count_60s": 0,
            "threshold": 8,
            "window_seconds": 60,
            "severity": "NORMAL",
            "message": "Audience sentiment is nominal and positive across all festival sectors.",
            "triggered_at": None,
            "cooldown_until": None
        }
        self.dataset_loaded = False

    def load_dataset(self):
        paths_to_check = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "huggingface_sentiment.json"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "huggingface_sentiment.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "validation.json"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "validation.json"),
        ]
        chosen_path = next((p for p in paths_to_check if os.path.exists(p)), None)
        if chosen_path:
            try:
                with open(chosen_path, "r", encoding="utf-8") as f:
                    self.raw_posts = json.load(f)
                self.dataset_loaded = True
                self._partition_pools()
                logger.info(f"Loaded {len(self.raw_posts)} Hugging Face social sentiment posts from {chosen_path}.")
                return
            except Exception as e:
                logger.warning(f"Failed to read sentiment JSON ({e}). Falling back to internal generator.")

        templates = [
            ("Main stage visual lasers and sound acoustics are mind-blowing tonight! Pure magic! ✨", "positive"),
            ("Fastest festival entry ever! Scanned my digital QR pass and got in within 3 seconds.", "positive"),
            ("Fest Coins contactless payment is crazy fast at the food stalls. Zero lines! 🪙", "positive"),
            ("The artisan pottery and silk clothing at the market are incredible. Picked up a souvenir!", "positive"),
            ("VIP Lounge drinks are top-tier. Clean couches, chill ambient lighting, great respite.", "positive"),
            ("Delicious saffron barbecue at Food Court! Staff was super polite and attentive.", "positive"),
            ("Unannounced acoustic set just kicked off at Cultural Pavilion! Truly enchanting.", "positive"),
            ("Water refill station near gate 2 is clean, cold, and free. Excellent organizer planning!", "positive"),
            ("Heading over to Artisan Market to browse festival merchandise.", "neutral"),
            ("Line at water station 1 is moving at an average pace.", "neutral"),
            ("Notice: Northern parking express shuttles depart every 10 minutes.", "neutral"),
            ("Currently in Food Court dining with friends before the midnight headliner.", "neutral"),
            ("Just topped up 1,000 Fest Coins via UPI in the app without issues.", "neutral"),
            ("Water refill queue at station 3 is completely blocked, valve is leaking everywhere! Fix this!", "negative"),
            ("Massive bottleneck forming at Cultural Pavilion north entrance, people are pushing hard!", "negative"),
            ("Long wait times at Food Court grill counter, waited 35 minutes for a single taco order.", "negative"),
            ("Security bottleneck at Gate 2 perimeter! Only one scanner is operational, crowd getting restless.", "negative"),
            ("Audio glitch and microphone feedback screeching during the headline set near stage left.", "negative"),
            ("Overcrowding near the main pathway between Artisan Market and Main Stage, need diversion staff ASAP.", "negative"),
            ("Restroom queue near sector B is backed up by 50 meters, sanitation staff needed immediately.", "negative"),
        ]
        authors = ['aarav_fest', 'priya_sound', 'rohan_travels', 'neha_beats', 'aditya_rave', 'ananya_live', 'vikram_t', 'tanvi_vibes']
        self.raw_posts = []
        for i in range(1000):
            txt, lbl = random.choice(templates)
            self.raw_posts.append({
                "id": f"tweet_{10000 + i}",
                "text": txt,
                "label": lbl,
                "author": f"@{random.choice(authors)}{random.randint(10, 99)}"
            })
        self.dataset_loaded = True
        self._partition_pools()
        logger.info(f"Initialized {len(self.raw_posts)} sentiment posts.")

    def _partition_pools(self):
        self.positive_pool = [p for p in self.raw_posts if str(p.get("label", "")).lower() == "positive"]
        self.neutral_pool = [p for p in self.raw_posts if str(p.get("label", "")).lower() == "neutral"]
        self.negative_pool = [p for p in self.raw_posts if str(p.get("label", "")).lower() == "negative"]

    def sample_post(self) -> Dict[str, Any]:
        if not self.raw_posts:
            self.load_dataset()

        # High resistance baseline: 90% positive, 8% neutral, 2% negative ambient drift
        r = random.random()
        if r < 0.90 and self.positive_pool:
            item = random.choice(self.positive_pool)
        elif r < 0.98 and self.neutral_pool:
            item = random.choice(self.neutral_pool)
        elif self.negative_pool:
            item = random.choice(self.negative_pool)
        else:
            item = random.choice(self.raw_posts)

        now_dt = datetime.now(timezone.utc)
        post = {
            "id": item.get("id", f"tweet_{random.randint(1000, 9999)}"),
            "text": item.get("text", "Festival atmosphere is electrifying!"),
            "sentiment": str(item.get("label", "neutral")).lower(),
            "author": item.get("author", "@festival_guest"),
            "timestamp": now_dt.isoformat(),
            "_dt": now_dt,
            "resolved": False
        }
        self.recent_stream.insert(0, post)
        if len(self.recent_stream) > 100:
            self.recent_stream = self.recent_stream[:100]

        self._evaluate_friction_alert(now_dt)
        return post

    def inject_negative_spike(self, count: int = 8):
        """Bypasses cooldown and immediately injects a burst of negative attendee feedback to test friction triage."""
        self.cooldown_until = None
        neg_posts = self.negative_pool or [p for p in self.raw_posts if str(p.get("label", "")).lower() == "negative"]
        if not neg_posts:
            neg_posts = [{"text": "Major bottleneck at Cultural Pavilion entrance! High friction!", "label": "negative", "author": "@alert_user"}]
        now_dt = datetime.now(timezone.utc)
        for i in range(count):
            item = random.choice(neg_posts)
            post = {
                "id": f"spike_{random.randint(1000, 9999)}",
                "text": item.get("text", "Overcrowding detected at festival bottleneck!"),
                "sentiment": "negative",
                "author": item.get("author", f"@guest_sos_{i}"),
                "timestamp": now_dt.isoformat(),
                "_dt": now_dt,
                "resolved": False
            }
            self.recent_stream.insert(0, post)
        if len(self.recent_stream) > 100:
            self.recent_stream = self.recent_stream[:100]
        self._evaluate_friction_alert(now_dt)

    def resolve_alert(self, cooldown_seconds: int = 120) -> Dict[str, Any]:
        """Operator resolution: Marks all recent complaints as resolved and activates resistance cooldown."""
        now_dt = datetime.now(timezone.utc)
        self.cooldown_until = now_dt + timedelta(seconds=cooldown_seconds)
        for p in self.recent_stream:
            p["resolved"] = True
        self.friction_alert = {
            "active": False,
            "negative_count_60s": 0,
            "threshold": self.threshold,
            "window_seconds": 60,
            "severity": "NORMAL",
            "message": f"Triage acknowledged: System in {cooldown_seconds}s cooldown. Crowd operations stabilizing.",
            "triggered_at": None,
            "cooldown_until": self.cooldown_until.isoformat()
        }
        return self.friction_alert

    def _evaluate_friction_alert(self, current_dt: datetime):
        # Resistance Hysteresis: Suppress baseline alarms during active cooldown window
        if self.cooldown_until and current_dt < self.cooldown_until:
            rem = max(0, int((self.cooldown_until - current_dt).total_seconds()))
            self.friction_alert["active"] = False
            self.friction_alert["severity"] = "NORMAL"
            self.friction_alert["message"] = f"Resistance cooldown active ({rem}s remaining): crowd flow stabilizing."
            self.friction_alert["cooldown_until"] = self.cooldown_until.isoformat()
            return
        elif self.cooldown_until and current_dt >= self.cooldown_until:
            self.cooldown_until = None

        window_cutoff = current_dt - timedelta(seconds=60)
        recent_60s = [p for p in self.recent_stream if p.get("_dt", current_dt) >= window_cutoff and not p.get("resolved", False)]
        negative_count = sum(1 for p in recent_60s if p.get("sentiment") == "negative")

        # Temporal Density Resistance Check:
        # Require >= self.threshold (8 complaints) total in 60s
        # AND at least 2 negative complaints in the last 20 seconds (ensures an active ongoing surge, not decaying residue)
        burst_20s_cutoff = current_dt - timedelta(seconds=20)
        recent_20s_neg = sum(1 for p in recent_60s if p.get("_dt", current_dt) >= burst_20s_cutoff and p.get("sentiment") == "negative")

        is_cluster_active = (negative_count >= self.threshold) and (recent_20s_neg >= 2)

        if is_cluster_active:
            severity = "CRITICAL" if negative_count >= 10 else "WARNING"
            self.friction_alert = {
                "active": True,
                "negative_count_60s": negative_count,
                "threshold": self.threshold,
                "window_seconds": 60,
                "severity": severity,
                "message": f"🚨 FRICTION ALERT ACTIVE: High negative sentiment cluster detected ({negative_count} complaints in last 60s)! Immediate crowd experience triage required.",
                "triggered_at": current_dt.isoformat(),
                "cooldown_until": None
            }
        else:
            # Flap prevention: if transitioning from active -> inactive, establish automatic 60s cooldown
            was_active = self.friction_alert.get("active", False)
            if was_active:
                self.cooldown_until = current_dt + timedelta(seconds=60)
            self.friction_alert = {
                "active": False,
                "negative_count_60s": negative_count,
                "threshold": self.threshold,
                "window_seconds": 60,
                "severity": "NORMAL",
                "message": "Audience sentiment is nominal and positive across all festival sectors.",
                "triggered_at": None,
                "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None
            }

social_manager = SocialSentimentManager()

async def social_sentiment_streaming_loop():
    logger.info("Starting Social Listening live sentiment triage stream (4.5s interval)...")
    social_manager.load_dataset()
    while True:
        try:
            await asyncio.sleep(4.5)
            social_manager.sample_post()
        except asyncio.CancelledError:
            logger.info("Social sentiment streaming task cancelled.")
            break
        except Exception as e:
            logger.warning(f"Error in social sentiment streaming loop: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# APPLICATION LIFESPAN & INITIALIZATION
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Connect database
    await db_manager.connect()

    # Pre-seed DinoFun dataset if database is fresh
    dino_simulator.load_dataset()
    kaggle_simulator.load_dataset()
    social_manager.load_dataset()

    # Start all 3 background task loops
    sim_crowd_task = asyncio.create_task(crowd_movement_simulation_loop())
    sim_tx_task = asyncio.create_task(kaggle_transaction_simulation_loop())
    sim_social_task = asyncio.create_task(social_sentiment_streaming_loop())

    logger.info("All 3 Real-World Dataset Simulator Tasks Active (Crowd Movement, Fest Coins, Social Triage)!")
    yield

    # Shutdown
    sim_crowd_task.cancel()
    sim_tx_task.cancel()
    sim_social_task.cancel()
    await asyncio.gather(sim_crowd_task, sim_tx_task, sim_social_task, return_exceptions=True)

    if db_manager.client:
        db_manager.client.close()

# Initialize FastAPI app
app = FastAPI(
    title="Cultura Festival Hub API",
    description="Live festival crowd-management telemetry, pass checkpoint verification, cashless Fest Coin economy, and real-time social sentiment triage.",
    version="2.5.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# No-Cache Middleware
@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# ─────────────────────── System & Health Endpoints ───────────────────────
@app.get("/api/health", response_model=HealthCheckResponse, tags=["Health"])
async def get_health():
    status_info = db_manager.get_status_summary()
    return {
        "status": "healthy",
        "framework": "FastAPI (Python 3.11)",
        "server": "Uvicorn ASGI",
        "validation": "Pydantic v2",
        "database_driver": "Motor (async MongoDB Atlas) with Resilient Fallback",
        "database_status": status_info["connection_message"],
        "database_uri_configured": status_info["database_uri_configured"],
        "active_connections": 1,
        "version": "2.5.0"
    }

@app.get("/zones", tags=["Zones"])
async def get_zones():
    return {"zones": ZONES}

# ─────────────────────── Attendee Registration & Lookup ───────────────────────
@app.post("/register_attendee", status_code=status.HTTP_201_CREATED, tags=["Attendees"])
async def register_attendee(payload: AttendeeCreate):
    initial_coins = payload.initial_coins if payload.initial_coins is not None else payload.fest_coins
    attendee = await db_manager.create_attendee(
        name=payload.name,
        role=payload.role,
        ticket_type=payload.ticket_type,
        initial_coins=initial_coins
    )
    return {
        "message": "Attendee registered successfully",
        "qr_code_hash": attendee["qr_code_hash"],
        "attendee": attendee
    }

@app.get("/attendee/{qr_code_hash}", tags=["Attendees"])
async def get_attendee_by_qr(qr_code_hash: str = Path(..., description="16-byte QR pass hash")):
    attendee = await db_manager.get_attendee_by_qr(qr_code_hash)
    if not attendee:
        raise HTTPException(status_code=404, detail="Attendee not found")
    return attendee

@app.get("/api/attendees/search", tags=["Attendees"])
async def search_attendees(q: Optional[str] = Query("", description="Query string for name, role, ticket, or ID")):
    results = await db_manager.list_attendees(query=q or "", limit=50)
    return {"attendees": results}

@app.get("/api/attendee/{attendee_id}/history", response_model=AttendeeHistoryResponse, tags=["Attendees"])
async def get_attendee_history(attendee_id: str):
    history = await db_manager.get_attendee_history(attendee_id)
    if not history:
        raise HTTPException(status_code=404, detail="Attendee not found")
    return history

# ─────────────────────── Gate Checkpoint Scanning ───────────────────────
@app.post("/scan", status_code=status.HTTP_201_CREATED, tags=["Field Scanning"])
async def scan_checkpoint(payload: ScanRequest):
    if payload.zone_name not in ZONES:
        raise HTTPException(status_code=400, detail="Invalid scan payload or zone")

    attendee = await db_manager.get_attendee_by_qr(payload.qr_code_hash)
    if not attendee:
        raise HTTPException(status_code=404, detail="Attendee not found")

    if attendee.get("status") == "terminated":
        return JSONResponse(
            status_code=403,
            content={
                "detail": f"SECURITY ALERT: QR Pass is REVOKED/TERMINATED ({attendee.get('termination_reason') or 'Access Revoked'})",
                "terminated": True,
                "attendee": attendee
            }
        )

    log_entry = await db_manager.log_zone_scan(attendee, payload.zone_name)
    updated_attendee = await db_manager.get_attendee_by_id(attendee["_id"])

    return {
        "message": f"Scan logged at {payload.zone_name}!",
        "attendee": updated_attendee or attendee,
        "zone_log": log_entry
    }

# ─────────────────────── Cashless Fest Coin Transactions ───────────────────────
async def _execute_transaction_deduction(payload: TransactionRequest, is_simulator: bool = False):
    has_identifier = bool(payload.qr_code_hash or payload.attendee_id or payload.customer_id)
    attendee = None
    if payload.qr_code_hash:
        attendee = await db_manager.get_attendee_by_qr(payload.qr_code_hash)
    elif payload.attendee_id:
        attendee = await db_manager.get_attendee_by_id(payload.attendee_id)
    elif payload.customer_id:
        attendee = await db_manager.get_attendee_by_id(payload.customer_id)

    if has_identifier:
        if not attendee:
            raise HTTPException(
                status_code=404,
                detail="Attendee not found for supplied identifier."
            )
    elif is_simulator:
        att_list = await db_manager.list_attendees(limit=50)
        attendee = next((a for a in att_list if a.get("status") != "terminated"), None)
        if not attendee:
            # Create an attendee if store is fresh
            attendee = await db_manager.create_attendee(
                name="Attendee General",
                role="General",
                ticket_type="Standard Pass",
                initial_coins=1000
            )
    else:
        raise HTTPException(
            status_code=400,
            detail="Attendee identifier (qr_code_hash, attendee_id, or customer_id) is required."
        )

    if not attendee:
        raise HTTPException(status_code=404, detail="Attendee not found.")

    if attendee.get("status") == "terminated":
        return JSONResponse(
            status_code=403,
            content={
                "detail": f"SECURITY ALERT: Payment Denied! QR Pass is REVOKED ({attendee.get('termination_reason') or 'Pass Revoked'})",
                "terminated": True,
                "attendee": attendee
            }
        )

    current_balance = attendee.get("fest_coins", 0)
    if current_balance < payload.amount:
        if is_simulator:
            # Confine automatic wallet reload behavior to the simulator
            await db_manager.create_recharge(
                attendee=attendee,
                inr_amount=payload.amount + 500,
                payment_method="UPI - Auto Reload",
                operator_mode="self_service",
                notes="Auto cashless liquidity top-up"
            )
            updated = await db_manager.get_attendee_by_id(attendee["_id"])
            if updated:
                attendee = updated
                current_balance = attendee.get("fest_coins", 0)
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": f"Insufficient Fest Coins. Current balance: {current_balance} coins, Required: {payload.amount} coins",
                    "current_balance": current_balance,
                    "required_amount": payload.amount
                }
            )

    stall = payload.stall_name or "Vendor Stall"
    zone = payload.zone_name or "Food Court"
    tx = await db_manager.create_transaction(
        attendee=attendee,
        amount=payload.amount,
        stall_name=stall,
        zone_name=zone
    )
    updated_attendee = await db_manager.get_attendee_by_id(attendee["_id"])

    return {
        "message": f"Successfully deducted {payload.amount} Fest Coins for {stall}",
        "transaction": tx,
        "attendee": {
            "name": updated_attendee["name"] if updated_attendee else attendee["name"],
            "fest_coins": updated_attendee["fest_coins"] if updated_attendee else current_balance - payload.amount
        }
    }

@app.post("/api/transaction", status_code=status.HTTP_201_CREATED, tags=["Fest Coins"])
async def api_process_transaction(payload: TransactionRequest):
    return await _execute_transaction_deduction(payload)

@app.post("/transaction", status_code=status.HTTP_201_CREATED, tags=["Fest Coins"])
async def public_process_transaction(payload: TransactionRequest):
    return await _execute_transaction_deduction(payload)

# ─────────────────────── Rupee-to-Coin Cashless Top-up ───────────────────────
@app.post("/api/recharge", status_code=status.HTTP_201_CREATED, tags=["Recharges"])
async def recharge_wallet(payload: RechargeRequest):
    attendee = None
    if payload.attendee_id:
        attendee = await db_manager.get_attendee_by_id(payload.attendee_id)
    elif payload.qr_code_hash:
        attendee = await db_manager.get_attendee_by_qr(payload.qr_code_hash)

    if not attendee:
        raise HTTPException(
            status_code=404,
            detail="Attendee not found. Please verify attendee name or scan QR pass."
        )

    if attendee.get("status") == "terminated":
        return JSONResponse(
            status_code=403,
            content={
                "detail": f"SECURITY ALERT: Recharge Rejected! QR Pass is REVOKED ({attendee.get('termination_reason') or 'Administrative Hold'}).",
                "terminated": True,
                "attendee": attendee
            }
        )

    recharge_entry = await db_manager.create_recharge(
        attendee=attendee,
        inr_amount=payload.inr_amount,
        payment_method=payload.payment_method or "UPI",
        operator_mode=payload.operator_mode or "self_service",
        operator_name=payload.operator_name,
        notes=payload.notes or ""
    )

    updated_attendee = await db_manager.get_attendee_by_id(attendee["_id"])

    return {
        "message": f"Payment of ₹{payload.inr_amount:,} successful! Added {payload.inr_amount:,} Fest Coins (1:1 conversion).",
        "recharge": recharge_entry,
        "attendee": updated_attendee or attendee
    }

@app.get("/api/recharges", tags=["Recharges"])
async def list_recharges():
    recharges_list, summary = await db_manager.get_recharges_summary(limit=100)
    return {
        "recharges": recharges_list,
        "summary": summary
    }

# ─────────────────────── Live Social Sentiment & Triage ───────────────────────
@app.get("/api/social/feed", tags=["Social Listening"])
async def get_social_feed():
    feed_items = [
        {
            "id": p["id"],
            "text": p["text"],
            "sentiment": p["sentiment"],
            "author": p.get("author", "@attendee"),
            "timestamp": p["timestamp"]
        }
        for p in social_manager.recent_stream[:25]
    ]
    # Calculate breakdown
    pos = sum(1 for p in social_manager.recent_stream if p.get("sentiment") == "positive")
    neu = sum(1 for p in social_manager.recent_stream if p.get("sentiment") == "neutral")
    neg = sum(1 for p in social_manager.recent_stream if p.get("sentiment") == "negative")

    return {
        "feed": feed_items,
        "friction_alert": social_manager.friction_alert,
        "sentiment_summary": {
            "positive_count": pos,
            "neutral_count": neu,
            "negative_count": neg,
            "total_sampled": len(social_manager.recent_stream)
        }
    }

@app.post("/api/social/simulate_spike", tags=["Social Listening"])
async def simulate_negative_sentiment_spike(is_admin: bool = Depends(verify_admin)):
    """Admin utility to test Friction Alert immediately by injecting a cluster of 8 negative attendee reports."""
    social_manager.inject_negative_spike(count=8)
    return {
        "message": "Simulated negative sentiment spike injected successfully!",
        "friction_alert": social_manager.friction_alert
    }

@app.post("/api/social/clear_alert", tags=["Social Listening"])
async def clear_friction_alert(is_admin: bool = Depends(verify_admin)):
    alert_state = social_manager.resolve_alert(cooldown_seconds=120)
    return {
        "message": "Friction Alert cleared successfully. 120s operational resistance cooldown activated.",
        "friction_alert": alert_state
    }

# ─────────────────────── Emergency Incidents (God's Eye) ───────────────────────
@app.get("/api/incidents", tags=["Incidents"])
async def list_incidents():
    incidents = await db_manager.list_recent_incidents()
    return {"incidents": incidents}

@app.post("/api/incident", status_code=status.HTTP_201_CREATED, tags=["Incidents"])
async def report_incident(payload: IncidentCreate):
    valid_types = ["Medical", "Security", "Logistics"]
    if payload.type not in valid_types:
        raise HTTPException(status_code=400, detail="Invalid incident type. Must be Medical, Security, or Logistics.")

    incident = await db_manager.create_incident(
        incident_type=payload.type,
        zone_name=payload.zone_name or "Main Stage",
        notes=payload.notes or "",
        coordinator_name=payload.coordinator_name or "Field Coordinator"
    )
    return {
        "message": f"🚨 Emergency {payload.type} incident logged at {incident['zone_name']}!",
        "incident": incident
    }

@app.patch("/api/incident/{incident_id}/status", tags=["Incidents"])
@app.post("/api/incident/{incident_id}/status", tags=["Incidents"])
async def update_incident_status_endpoint(
    incident_id: str,
    payload: IncidentStatusUpdate,
    is_authorized: bool = Depends(verify_field_or_admin)
):
    updated = await db_manager.update_incident_status(
        incident_id,
        payload.status,
        payload.coordinator_name,
        payload.resolution_notes
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {
        "message": f"Incident marked as {updated['status']}",
        "incident": updated
    }

@app.patch("/api/incident/{incident_id}/resolve", tags=["Incidents"])
@app.post("/api/incident/{incident_id}/resolve", tags=["Incidents"])
async def resolve_incident_endpoint(
    incident_id: str,
    payload: Optional[IncidentStatusUpdate] = None,
    coordinator: Optional[str] = None,
    is_authorized: bool = Depends(verify_field_or_admin)
):
    coord_name = (payload and payload.coordinator_name) or coordinator or "Zone Coordinator"
    res_notes = (payload and payload.resolution_notes) or None
    resolved = await db_manager.resolve_incident(incident_id, coordinator_name=coord_name, resolution_notes=res_notes)
    if not resolved:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {
        "message": f"{resolved['type']} incident marked resolved by {resolved.get('resolved_by', 'Coordinator')}",
        "incident": resolved
    }

# ─────────────────────── Crowd Crush Prevention & Diversions ───────────────────────
@app.post("/api/admin/trigger_diversion", status_code=status.HTTP_201_CREATED, tags=["Crowd Control"])
async def trigger_diversion_endpoint(payload: DiversionCreate, is_admin: bool = Depends(verify_admin)):
    if payload.zone_name not in ZONES:
        raise HTTPException(status_code=400, detail="Invalid zone name for diversion")

    diversion, relieved_count = await db_manager.trigger_diversion(
        zone_name=payload.zone_name,
        target_zone=payload.target_zone or "Food Court",
        message=payload.message or ""
    )
    return {
        "message": f"Diversion activated for {payload.zone_name}! {relieved_count} attendees re-routed to {diversion['target_zone']}.",
        "diversion": diversion
    }

@app.post("/api/admin/clear_diversion/{diversion_id}", tags=["Crowd Control"])
async def clear_diversion_endpoint(diversion_id: str, is_admin: bool = Depends(verify_admin)):
    success = await db_manager.clear_diversion(diversion_id)
    if not success:
        raise HTTPException(status_code=404, detail="Diversion not found")
    return {"message": "Diversion marked inactive"}

@app.post("/api/admin/throttle_zone", tags=["Crowd Control"])
async def throttle_zone_endpoint(payload: ThrottleRequest, is_admin: bool = Depends(verify_admin)):
    if payload.zone_name not in ZONES:
        raise HTTPException(status_code=400, detail="Invalid zone")

    other_zones = [z for z in ZONES if z != payload.zone_name]
    target = payload.target_zone if payload.target_zone and payload.target_zone in ZONES else random.choice(other_zones)
    relieved = await db_manager.reroute_recent_scans(payload.zone_name, target, limit=payload.count or 150)

    if relieved == 0:
        return {"message": f"No active attendees found in {payload.zone_name}."}
    return {"message": f"SUCCESS! Diverted {relieved} attendees from {payload.zone_name} to {target}."}

# ─────────────────────── Simulation Endpoints ───────────────────────
@app.post("/api/admin/simulate_surge", tags=["Simulation"])
async def simulate_surge_endpoint(is_admin: bool = Depends(verify_admin)):
    count = await db_manager.simulate_surge()
    return {"message": f"Surge simulated! {count} attendees rushed the Cultural Pavilion."}

@app.post("/api/admin/revert_simulation", tags=["Simulation"])
async def revert_simulation_endpoint(is_admin: bool = Depends(verify_admin)):
    reverted = await db_manager.revert_simulation()
    return {"message": f"Simulation stopped. Reverted {reverted} simulated scans."}

# ─────────────────────── Admin Stats & Live Telemetry ───────────────────────
@app.get("/api/admin/stats", tags=["Admin Operations"])
async def get_admin_stats_endpoint(is_admin: bool = Depends(verify_admin)):
    stats = await db_manager.get_admin_stats()
    # Attach Live Social Listening Feed and Friction Alert for single 3-second polling roundtrip
    stats["social_feed"] = [
        {
            "id": p["id"],
            "text": p["text"],
            "sentiment": p["sentiment"],
            "author": p.get("author", "@attendee"),
            "timestamp": p["timestamp"]
        }
        for p in social_manager.recent_stream[:15]
    ]
    stats["friction_alert"] = social_manager.friction_alert

    # Ensure live_transactions is populated in financials
    if "financials" in stats:
        stats["financials"]["live_transactions"] = stats["financials"].get("transaction_volume", 0)

    return stats

@app.get("/api/admin/attendees", tags=["Admin Operations"])
async def list_admin_attendees(is_admin: bool = Depends(verify_admin)):
    attendees = await db_manager.list_attendees(limit=500)
    return {"attendees": attendees}

@app.delete("/api/admin/delete_attendee/{attendee_id}", tags=["Admin Operations"])
async def delete_attendee_endpoint(attendee_id: str, is_admin: bool = Depends(verify_admin)):
    existing = await db_manager.get_attendee_by_id(attendee_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Attendee not found")
    await db_manager.delete_attendee(attendee_id)
    return {"message": "Attendee deleted permanently."}

async def _terminate_qr_logic(attendee_id: str, reason: Optional[str]):
    attendee = await db_manager.get_attendee_by_id(attendee_id)
    if not attendee:
        raise HTTPException(status_code=404, detail="Attendee not found")

    revocation_reason = (reason and reason.strip()) or "Administrative revocation"
    updated = await db_manager.update_attendee(
        attendee_id,
        {
            "status": "terminated",
            "terminated_at": datetime.now(timezone.utc).isoformat(),
            "termination_reason": revocation_reason,
        }
    )
    return {
        "message": f"QR Pass for {attendee['name']} has been terminated.",
        "attendee": updated
    }

@app.post("/api/admin/attendee/{attendee_id}/terminate_qr", tags=["Pass Security"])
async def admin_terminate_qr(attendee_id: str, payload: Optional[TerminateQrRequest] = None, is_admin: bool = Depends(verify_admin)):
    return await _terminate_qr_logic(attendee_id, payload.reason if payload else None)

@app.post("/attendee/{attendee_id}/terminate_qr", tags=["Pass Security"])
async def public_terminate_qr(attendee_id: str, payload: Optional[TerminateQrRequest] = None):
    return await _terminate_qr_logic(attendee_id, payload.reason if payload else None)

async def _reinstate_qr_logic(attendee_id: str, regenerate: bool):
    attendee = await db_manager.get_attendee_by_id(attendee_id)
    if not attendee:
        raise HTTPException(status_code=404, detail="Attendee not found")

    updates: Dict[str, Any] = {
        "status": "active",
        "terminated_at": None,
        "termination_reason": None,
    }
    if regenerate:
        import secrets
        updates["qr_code_hash"] = secrets.token_hex(16)

    updated = await db_manager.update_attendee(attendee_id, updates)
    msg = f"New QR Pass generated and activated for {attendee['name']}." if regenerate else f"QR Pass for {attendee['name']} has been reinstated."
    return {
        "message": msg,
        "attendee": updated
    }

@app.post("/api/admin/attendee/{attendee_id}/reinstate_qr", tags=["Pass Security"])
async def admin_reinstate_qr(attendee_id: str, payload: Optional[ReinstateQrRequest] = None, is_admin: bool = Depends(verify_admin)):
    return await _reinstate_qr_logic(attendee_id, payload.regenerate if payload else False)

@app.post("/attendee/{attendee_id}/reinstate_qr", tags=["Pass Security"])
async def public_reinstate_qr(attendee_id: str, payload: Optional[ReinstateQrRequest] = None):
    return await _reinstate_qr_logic(attendee_id, payload.regenerate if payload else False)

# ─────────────────────── NLP Assistant & Intelligence ───────────────────────
@app.post("/api/admin/nlp_query", tags=["AI Command Assistant"])
async def nlp_query_endpoint(request_body: Dict[str, Any], is_admin: bool = Depends(verify_admin)):
    original_query = str(request_body.get("query", "")).strip()
    query = original_query.lower()

    stats = await db_manager.get_admin_stats()
    all_att = await db_manager.list_attendees(limit=500)

    zone_counts = {z["zone_name"]: z["total_scans"] for z in stats["zones"]}
    bottlenecks = [z["zone_name"] for z in stats["zones"] if z["recent_scans"] >= BOTTLENECK_THRESHOLD]

    total_attendees = len(all_att)
    active_count = sum(1 for a in all_att if a.get("status") == "active")
    terminated_count = total_attendees - active_count
    total_coins = sum(a.get("fest_coins", 0) for a in all_att)
    role_counts = {}
    for a in all_att:
        r = a.get("role", "General")
        role_counts[r] = role_counts.get(r, 0) + 1

    fin = stats.get("financials", {})
    total_inr_collected = fin.get("total_inr_recharged", 0)
    recharge_volume = fin.get("recharge_volume", 0)
    active_incidents = stats.get("active_incidents", [])
    # Live Social Sentiment Telemetry
    social_manager._evaluate_friction_alert(datetime.now(timezone.utc))
    total_sampled = len(social_manager.recent_stream)
    pos_count = sum(1 for p in social_manager.recent_stream if str(p.get("sentiment", "")).lower() == "positive")
    neu_count = sum(1 for p in social_manager.recent_stream if str(p.get("sentiment", "")).lower() == "neutral")
    neg_count = sum(1 for p in social_manager.recent_stream if str(p.get("sentiment", "")).lower() == "negative")
    denom = max(1, total_sampled)
    pos_pct = round((pos_count / denom) * 100, 1)
    neu_pct = round((neu_count / denom) * 100, 1)
    neg_pct = round((neg_count / denom) * 100, 1)

    f_alert_global = social_manager.friction_alert
    is_friction_active_global = bool(f_alert_global.get("active", False))
    if is_friction_active_global:
        mood_rating = "CRITICAL FRICTION"
    elif pos_pct >= 75:
        mood_rating = "OVERWHELMINGLY POSITIVE"
    elif pos_pct >= 50:
        mood_rating = "MOSTLY POSITIVE"
    elif neg_pct >= 30:
        mood_rating = "HIGH FRICTION"
    else:
        mood_rating = "BALANCED"

    is_sentiment = bool(re.search(r"sentiment|social|listening|mood|vibe|feedback|review|tweet|reaction|feeling|opinion|complaint|friction|\bposts?\b|(?:friction|sentiment|social|feedback)\s+triage", query)) and not bool(re.search(r"\bsos\b|emergency|medical|hazard", query))
    is_crowd = bool(re.search(r"crowd|bottleneck|congest|traffic|capacity|dens|packed|fullest|busy|divert", query))
    is_coin = bool(re.search(r"coin|recharge|inr|rupee|wallet|cash|money|top-up|balance|revenue|minted|fest coin", query))
    is_pass = bool(re.search(r"pass|attendee|guest|ticket|revok|terminat|status|register|vip", query)) and not is_crowd
    is_incident = bool(re.search(r"incident|\bsos\b|emergency|medical|security|logistics|hazard|\btriage\b", query))

    category = "general"
    answer = ""
    metrics: List[Dict[str, Any]] = []
    quick_actions: List[Dict[str, Any]] = []
    result_count = 0

    if is_sentiment:
        category = "sentiment"
        f_alert = social_manager.friction_alert
        is_friction_active = bool(f_alert.get("active", False))
        neg_60s = f_alert.get("negative_count_60s", 0)
        severity = f_alert.get("severity", "NORMAL")

        latest_neg = next((p for p in social_manager.recent_stream if str(p.get("sentiment", "")).lower() == "negative"), None)
        latest_pos = next((p for p in social_manager.recent_stream if str(p.get("sentiment", "")).lower() == "positive"), None)

        if is_friction_active:
            answer = f"🚨 Social Sentiment Alert ({severity}): Friction alert is ACTIVE with {neg_60s} negative attendee complaints in the last 60 seconds! Stream breakdown: {pos_pct}% Positive, {neu_pct}% Neutral, {neg_pct}% Negative across {total_sampled} live posts."
            if latest_neg:
                answer += f' Highlighted complaint: "{latest_neg.get("text")}" ({latest_neg.get("author")}). Immediate triage recommended.'
        else:
            answer = f"Live Sentiment Telemetry: Audience mood is {mood_rating} ({pos_pct}% positive, {neu_pct}% neutral, {neg_pct}% negative across {total_sampled} live posts). Friction alert is NOMINAL with {neg_60s} complaints in the 60s window."
            if latest_pos:
                answer += f' Recent guest buzz: "{latest_pos.get("text")}" ({latest_pos.get("author")}).'

        metrics = [
            {"label": "Audience Mood", "value": f"{pos_pct}% Positive", "alert": is_friction_active or neg_pct > 25},
            {"label": "Friction Alert", "value": "ACTIVE" if is_friction_active else "NOMINAL", "alert": is_friction_active},
            {"label": "Positive Posts", "value": f"{pos_count} ({pos_pct}%)"},
            {"label": "Negative Reports", "value": f"{neg_count} ({neg_pct}%)", "alert": is_friction_active or neg_count >= 8}
        ]
        quick_actions = [
            {"label": "Open Live Social Radar", "url": "/#social-ticker-container"}
        ]
        if is_friction_active:
            quick_actions.append({"label": "Resolve Friction Alert", "action": "clear_alert"})
        else:
            quick_actions.append({"label": "Test Friction Spike", "action": "simulate_spike"})

        result_count = total_sampled

    elif is_crowd:
        category = "crowd"
        if bottlenecks:
            b_str = ", ".join([f"{b} ({zone_counts.get(b, 0)} scans)" for b in bottlenecks])
            answer = f"⚠️ Alert: {len(bottlenecks)} festival zone(s) currently at or above bottleneck threshold (≥150 scans): {b_str}. Crowd diversion is recommended."
        else:
            top_zone = max(zone_counts.items(), key=lambda x: x[1]) if zone_counts else ("Main Stage", 0)
            answer = f"Flow is steady across all festival zones. No critical bottlenecks detected. The highest density is currently at {top_zone[0]} with {top_zone[1]} recorded scans."

        metrics = [
            {"label": z, "value": f"{zone_counts.get(z, 0)} scans", "alert": zone_counts.get(z, 0) >= BOTTLENECK_THRESHOLD}
            for z in ZONES
        ]
        quick_actions = [
            {"label": "View Live Telemetry", "url": "/"},
            {"label": "Trigger Crowd Diversion", "action": "diversion"}
        ]
        result_count = len(bottlenecks)

    elif is_coin:
        category = "financial"
        answer = f"Cashless Economy Status: Total Fest Coins in attendee circulation: 🪙 {total_coins:,}. Total INR collected via top-ups: ₹{total_inr_collected:,} across {recharge_volume} transactions (Standard rate: 1 INR = 1 Fest Coin)."
        metrics = [
            {"label": "Coins in Circulation", "value": f"🪙 {total_coins:,}"},
            {"label": "Total INR Collected", "value": f"₹{total_inr_collected:,}"},
            {"label": "Top-up Transactions", "value": str(recharge_volume)},
            {"label": "Rate", "value": "1 ₹ = 1 🪙"}
        ]
        quick_actions = [
            {"label": "Open Coin Top-up Hub", "url": "/recharge.html"},
            {"label": "View Guest Balances", "url": "/attendees.html"}
        ]
        result_count = total_coins

    elif is_incident:
        category = "security"
        f_alert = social_manager.friction_alert
        if f_alert.get("active"):
            answer = f"🚨 High Alert: Active Friction Alert in progress with {f_alert.get('negative_count_60s')} negative attendee complaints in the last 60 seconds. SOS triage required."
        elif active_incidents:
            answer = f"Active Security Alerts: {len(active_incidents)} pending incident(s) require dispatcher triage. Latest incident in {active_incidents[0].get('zone_name', 'Festival Grounds')}."
        else:
            answer = f"All clear. Zero unresolved SOS incidents. Audience mood is {mood_rating.lower()} ({pos_pct}% positive)."
        metrics = [
            {"label": "Active Incidents", "value": str(len(active_incidents)), "alert": len(active_incidents) > 0},
            {"label": "Friction Alert", "value": "ACTIVE" if f_alert.get("active") else "NOMINAL", "alert": f_alert.get("active")},
            {"label": "Diversions Active", "value": str(len(stats.get("active_diversions", [])))}
        ]
        quick_actions = [
            {"label": "Open Incident Radar", "url": "/"},
            {"label": "Scanner Station SOS", "url": "/scanner.html"}
        ]
        result_count = len(active_incidents)

    elif is_pass:
        category = "attendees"
        vip_count = role_counts.get("VIP", 0)
        answer = f"Attendee Directory: {total_attendees} total registered attendees. {active_count} passes active, {terminated_count} passes revoked/terminated. VIP passes: {vip_count}."
        metrics = [
            {"label": "Total Registered", "value": str(total_attendees)},
            {"label": "Active Passes", "value": str(active_count)},
            {"label": "Revoked Passes", "value": str(terminated_count)},
            {"label": "VIP Passholders", "value": str(vip_count)}
        ]
        quick_actions = [
            {"label": "Open Guest Directory", "url": "/attendees.html"},
            {"label": "Field Scanner Hub", "url": "/scanner.html"}
        ]
        result_count = total_attendees

    else:
        answer = f"Festival Telemetry Status: {total_attendees} registered attendees with {active_count} active passes. Current cashless balance: 🪙 {total_coins:,} Fest Coins."
        metrics = [
            {"label": "Registered Guests", "value": str(total_attendees)},
            {"label": "Active Zones", "value": str(len(ZONES))},
            {"label": "Cashless Balance", "value": f"🪙 {total_coins:,}"}
        ]
        quick_actions = [
            {"label": "Command Center", "url": "/"},
            {"label": "Live Analytics", "url": "/analytics.html"}
        ]
        result_count = total_attendees

    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key and len(gemini_key) > 10:
        try:
            from google import genai
            client = genai.Client()
            sentiment_summary = {
                "positive_pct": f"{pos_pct}%",
                "neutral_pct": f"{neu_pct}%",
                "negative_pct": f"{neg_pct}%",
                "total_posts_sampled": total_sampled,
                "audience_mood": mood_rating,
                "friction_alert": social_manager.friction_alert,
                "recent_sample": [
                    {"author": p.get("author"), "sentiment": p.get("sentiment"), "text": p.get("text")}
                    for p in social_manager.recent_stream[:3]
                ]
            }
            prompt = f"""You are the Cultura Festival AI Command Assistant.
The festival administrator asked: "{original_query}".
Telemetry Snapshot:
- Zones: {json.dumps(zone_counts)}
- Bottlenecks: {json.dumps(bottlenecks)}
- Total Attendees: {total_attendees} ({active_count} active passes, {terminated_count} revoked)
- Total Fest Coins in Circulation: {total_coins}
- Total INR Cashless Collected: ₹{total_inr_collected}
- Active SOS Incidents: {len(active_incidents)}
- Sentiment Analyzer & Social Telemetry: {json.dumps(sentiment_summary)}
- Friction Alert: {json.dumps(social_manager.friction_alert)}
Base calculated response: "{answer}"

Provide a concise, direct, professional response in 1-2 clear sentences. Include specific numbers where relevant."""
            
            ai_res = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            if ai_res and ai_res.text:
                answer = ai_res.text.strip()
        except Exception as ai_err:
            logger.debug(f"Gemini API enhancement fallback: {ai_err}")

    return {
        "query": original_query,
        "response": answer,
        "answer": answer,
        "category": category,
        "metrics": metrics,
        "quick_actions": quick_actions,
        "result_count": result_count,
    }

# Database test config
@app.post("/api/admin/config", tags=["Admin Operations"])
async def config_test_endpoint(is_admin: bool = Depends(verify_admin)):
    status_info = db_manager.get_status_summary()
    return {"message": f"Database operational. {status_info['connection_message']}"}

# ─────────────────────── Static Frontend Serving ───────────────────────
base_dir = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(os.path.dirname(base_dir), "frontend")
if not os.path.exists(static_dir):
    static_dir = os.path.join(base_dir, "static")
if not os.path.exists(static_dir):
    static_dir = os.path.join(os.path.dirname(base_dir), "static")

if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_index():
    return FileResponse(os.path.join(static_dir, "index.html"))

@app.api_route("/admin.html", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_admin():
    return FileResponse(os.path.join(static_dir, "index.html"))

@app.api_route("/scanner.html", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_scanner():
    return FileResponse(os.path.join(static_dir, "scanner.html"))

@app.api_route("/analytics.html", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_analytics():
    return FileResponse(os.path.join(static_dir, "analytics.html"))

@app.api_route("/attendees.html", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_attendees():
    return FileResponse(os.path.join(static_dir, "attendees.html"))

@app.api_route("/recharge.html", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_recharge():
    return FileResponse(os.path.join(static_dir, "recharge.html"))

@app.api_route("/payment.html", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_payment():
    return FileResponse(os.path.join(static_dir, "recharge.html"))

@app.api_route("/settings.html", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_settings():
    return FileResponse(os.path.join(static_dir, "settings.html"))

@app.api_route("/{file_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_static_fallback(file_path: str):
    full_path = os.path.join(static_dir, file_path)
    if os.path.isfile(full_path):
        return FileResponse(full_path)
    return FileResponse(os.path.join(static_dir, "index.html"))

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 3000))
    host = os.getenv("HOST", "127.0.0.1")
    reload_mode = os.getenv("RELOAD", "false").lower() in ("true", "1", "yes")
    uvicorn.run("main:app", host=host, port=port, reload=reload_mode)
