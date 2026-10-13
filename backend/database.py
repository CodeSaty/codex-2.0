import os
import secrets
import asyncio
import time
import copy
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional, Tuple
import logging

try:
    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
except ImportError:
    AsyncIOMotorClient = None
    ConnectionFailure = Exception
    ServerSelectionTimeoutError = Exception

logger = logging.getLogger("cultura.database")

# ─────────────────────── Domain Constants ───────────────────────
ZONES = ['Main Stage', 'Food Court', 'Artisan Market', 'Cultural Pavilion', 'VIP Lounge']
ROLES = ['General', 'Early Bird', 'VIP', 'Organizer', 'Sponsor']
SCAN_ENGAGEMENT_POINTS = 10
BOTTLENECK_THRESHOLD = 150

def generate_id() -> str:
    return secrets.token_hex(12)

def generate_qr_hash() -> str:
    return secrets.token_hex(16)

class DatabaseManager:
    def __init__(self):
        self.mongodb_uri = os.getenv("MONGODB_URI", "").strip()
        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        self.is_connected_to_atlas = False
        self.connection_message = "Initializing..."

        # In-Memory reactive datastore (fallback & high-performance cache)
        self.mem_attendees: Dict[str, Dict[str, Any]] = {}
        self.mem_zone_logs: List[Dict[str, Any]] = []
        self.mem_transactions: List[Dict[str, Any]] = []
        self.mem_incidents: List[Dict[str, Any]] = []
        self.mem_diversions: List[Dict[str, Any]] = []
        self.mem_recharges: List[Dict[str, Any]] = []

        # High-performance telemetry caching for instant 3s polling roundtrips
        self._stats_cache: Optional[Dict[str, Any]] = None
        self._stats_cache_time: float = 0.0
        self._stats_cache_ttl: float = 1.5

    def invalidate_admin_stats_cache(self):
        self._stats_cache = None
        self._stats_cache_time = 0.0

    async def connect(self):
        """Attempts to connect to MongoDB Atlas if URI is provided, else activates in-memory fallback."""
        if self.mongodb_uri and AsyncIOMotorClient is not None:
            try:
                logger.info(f"Connecting to MongoDB Atlas at {self.mongodb_uri[:15]}...")
                self.client = AsyncIOMotorClient(
                    self.mongodb_uri,
                    serverSelectionTimeoutMS=2500,
                    connectTimeoutMS=2500
                )
                # Verify cluster ping
                await self.client.admin.command('ping')
                self.db = self.client.get_default_database()
                if self.db is None or self.db.name == 'test':
                    self.db = self.client["cultura_festival_db"]
                self.is_connected_to_atlas = True
                self.connection_message = f"Connected to MongoDB Atlas ({self.db.name})"
                logger.info("Successfully connected to MongoDB Atlas!")
                
                # Create indexes for high-throughput queries
                await self.db.attendees.create_index("qr_code_hash", unique=True)
                await self.db.attendees.create_index("status")
                await self.db.zone_logs.create_index([("timestamp", -1)])
                await self.db.zone_logs.create_index("zone_name")
                await self.db.recharges.create_index([("timestamp", -1)])
                await self.db.transactions.create_index([("timestamp", -1)])
            except Exception as e:
                logger.warning(f"MongoDB Atlas connection failed ({e}). Falling back to reactive in-memory datastore.")
                self.is_connected_to_atlas = False
                self.connection_message = f"In-Memory Store (Atlas unreachable: {str(e)[:60]})"
        else:
            self.is_connected_to_atlas = False
            self.connection_message = "In-Memory Store (MONGODB_URI not configured - set in environment to activate Atlas)"
            logger.info("Running on high-throughput In-Memory datastore.")

        # Seed initial dataset if database is empty
        await self.seed_if_needed()

    async def seed_if_needed(self):
        """Seeds initial festival data if collections are empty."""
        has_data = False
        if self.is_connected_to_atlas and self.db is not None:
            count = await self.db.attendees.count_documents({})
            if count > 0:
                has_data = True
        elif len(self.mem_attendees) > 0:
            has_data = True

        if not has_data:
            await self._run_seeder()

    async def _run_seeder(self):
        logger.info("Seeding realistic festival data (attendees, scans, transactions, incidents, recharges)...")
        first_names = [
            'Aarav', 'Vivaan', 'Aditya', 'Arjun', 'Sai', 'Reyansh', 'Krishna', 'Ishaan', 'Rohan', 'Kabir',
            'Ananya', 'Diya', 'Saanvi', 'Aadhya', 'Priya', 'Isha', 'Meera', 'Riya', 'Sneha', 'Kavya',
            'Rahul', 'Neha', 'Vikram', 'Pooja', 'Karan', 'Tanvi', 'Dev', 'Nisha', 'Sameer', 'Aisha',
            'Liam', 'Olivia', 'Noah', 'Emma', 'Lucas', 'Mia', 'Ethan', 'Sophia', 'Mason', 'Isabella'
        ]
        last_names = [
            'Sharma', 'Verma', 'Gupta', 'Patel', 'Reddy', 'Iyer', 'Nair', 'Singh', 'Kumar', 'Das',
            'Mehta', 'Joshi', 'Rao', 'Chopra', 'Bose', 'Malhotra', 'Kapoor', 'Menon', 'Pillai', 'Agarwal',
            'Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis', 'Rodriguez', 'Martinez'
        ]
        tickets = {
            'General': ['Standard', 'Student', 'Last Minute'],
            'Early Bird': ['Early Bird Std', 'Early Bird Student'],
            'VIP': ['Platinum', 'Investor', 'Speaker'],
            'Organizer': ['Staff', 'Volunteer'],
            'Sponsor': ['Corporate', 'Executive Sponsor'],
        }
        zone_affinities = {
            'General': {'Main Stage': 2, 'Food Court': 2, 'Artisan Market': 2, 'Cultural Pavilion': 5, 'VIP Lounge': 1},
            'Early Bird': {'Main Stage': 2, 'Food Court': 2, 'Artisan Market': 2, 'Cultural Pavilion': 5, 'VIP Lounge': 1},
            'VIP': {'Main Stage': 4, 'Food Court': 3, 'Artisan Market': 1, 'Cultural Pavilion': 1, 'VIP Lounge': 5},
            'Organizer': {'Main Stage': 1, 'Food Court': 1, 'Artisan Market': 1, 'Cultural Pavilion': 1, 'VIP Lounge': 1},
            'Sponsor': {'Main Stage': 3, 'Food Court': 2, 'Artisan Market': 1, 'Cultural Pavilion': 1, 'VIP Lounge': 4},
        }

        now = datetime.now(timezone.utc)
        attendees_to_insert = []

        import random
        for i in range(250):
            r = random.random()
            if r < 0.60: role = 'General'
            elif r < 0.85: role = 'Early Bird'
            elif r < 0.95: role = 'Organizer'
            else: role = 'VIP'

            fn = random.choice(first_names)
            ln = random.choice(last_names)
            t_list = tickets.get(role, ['Standard'])
            ticket = random.choice(t_list)

            att_id = generate_id()
            qr_hash = generate_qr_hash()
            reg_time = now - timedelta(hours=random.uniform(1, 48))

            att = {
                '_id': att_id,
                'name': f"{fn} {ln}",
                'role': role,
                'ticket_type': ticket,
                'engagement_score': 0,
                'fest_coins': 1000,
                'qr_code_hash': qr_hash,
                'status': 'active',
                'terminated_at': None,
                'termination_reason': None,
                'registered_at': reg_time.isoformat(),
            }
            attendees_to_insert.append(att)
            self.mem_attendees[att_id] = att

        # Historical scans (past 4 hours)
        logs_to_insert = []
        for i in range(1800):
            att = random.choice(attendees_to_insert)
            affs = zone_affinities.get(att['role'], zone_affinities['General'])
            zones_weighted = []
            for z, w in affs.items():
                zones_weighted.extend([z] * w)
            chosen_zone = random.choice(zones_weighted)
            scan_time = now - timedelta(hours=random.uniform(0.1, 4.0))

            att['engagement_score'] += SCAN_ENGAGEMENT_POINTS

            log_entry = {
                '_id': generate_id(),
                'attendee_id': att['_id'],
                'qr_code_hash': att['qr_code_hash'],
                'role': att['role'],
                'zone_name': chosen_zone,
                'timestamp': scan_time.isoformat(),
                'seeded': True,
                'simulated': False
            }
            logs_to_insert.append(log_entry)
            self.mem_zone_logs.append(log_entry)

        # Bottleneck Surge in Cultural Pavilion (180 recent scans in last 10 mins)
        for i in range(180):
            att = random.choice(attendees_to_insert)
            scan_time = now - timedelta(minutes=random.uniform(0.5, 9.5))
            att['engagement_score'] += SCAN_ENGAGEMENT_POINTS

            surge_log = {
                '_id': generate_id(),
                'attendee_id': att['_id'],
                'qr_code_hash': att['qr_code_hash'],
                'role': att['role'],
                'zone_name': 'Cultural Pavilion',
                'timestamp': scan_time.isoformat(),
                'seeded': True,
                'simulated': False
            }
            logs_to_insert.append(surge_log)
            self.mem_zone_logs.append(surge_log)

        # Cashless transactions (65 transactions)
        stalls = [
            {'name': 'Food Court - Saffron Grill', 'zone': 'Food Court', 'price': 150},
            {'name': 'Artisan Market - Silk & Spice', 'zone': 'Artisan Market', 'price': 200},
            {'name': 'Main Stage - Beverage Bar', 'zone': 'Main Stage', 'price': 120},
            {'name': 'Cultural Pavilion - Craft Hub', 'zone': 'Cultural Pavilion', 'price': 80},
            {'name': 'VIP Lounge - Signature Cocktails', 'zone': 'VIP Lounge', 'price': 250},
        ]
        txs_to_insert = []
        for i in range(65):
            att = random.choice(attendees_to_insert)
            stall = random.choice(stalls)
            deduct = stall['price']
            if att['fest_coins'] >= deduct:
                att['fest_coins'] -= deduct
                tx_time = now - timedelta(hours=random.uniform(0.2, 2.5))
                tx_entry = {
                    '_id': generate_id(),
                    'attendee_id': att['_id'],
                    'attendee_name': att['name'],
                    'qr_code_hash': att['qr_code_hash'],
                    'amount': deduct,
                    'stall_name': stall['name'],
                    'zone_name': stall['zone'],
                    'timestamp': tx_time.isoformat()
                }
                txs_to_insert.append(tx_entry)
                self.mem_transactions.append(tx_entry)

        # Pre-seed initial incidents
        initial_incidents = [
            {
                '_id': generate_id(),
                'type': 'Medical',
                'zone_name': 'Cultural Pavilion',
                'notes': 'Attendee experiencing heat exhaustion near northern gallery entrance.',
                'status': 'Active',
                'reported_at': (now - timedelta(minutes=4)).isoformat(),
                'resolved_at': None,
            },
            {
                '_id': generate_id(),
                'type': 'Security',
                'zone_name': 'Main Stage',
                'notes': 'Perimeter barrier loose near stage right sound console.',
                'status': 'Active',
                'reported_at': (now - timedelta(minutes=8)).isoformat(),
                'resolved_at': None,
            },
            {
                '_id': generate_id(),
                'type': 'Logistics',
                'zone_name': 'Food Court',
                'notes': 'Water refill station 3 valve replacement requested.',
                'status': 'Resolved',
                'reported_at': (now - timedelta(minutes=35)).isoformat(),
                'resolved_at': (now - timedelta(minutes=12)).isoformat(),
            }
        ]
        self.mem_incidents = initial_incidents.copy()

        # Rupee to Fest Coin Recharges (40 top-ups)
        payment_methods = ['UPI - GPay', 'UPI - PhonePe', 'UPI - Paytm', 'Cash Desk', 'Card (Visa/Mastercard)', 'NetBanking']
        recharges_to_insert = []
        for i in range(40):
            att = random.choice(attendees_to_insert)
            rupee_amount = random.choice([100, 200, 250, 500, 1000, 1500])
            method = random.choice(payment_methods)
            is_staff = method == 'Cash Desk' or random.random() < 0.3
            r_time = now - timedelta(hours=random.uniform(0.1, 6.0))
            recharge_entry = {
                '_id': generate_id(),
                'payment_ref': f"TXN-INR-{int(r_time.timestamp())}-{random.randint(1000, 9999)}",
                'attendee_id': att['_id'],
                'attendee_name': att['name'],
                'attendee_role': att['role'],
                'qr_code_hash': att['qr_code_hash'],
                'inr_amount': rupee_amount,
                'coins_added': rupee_amount,
                'conversion_ratio': '1 INR = 1 Coin',
                'payment_method': method,
                'operator_mode': 'staff_desk' if is_staff else 'self_service',
                'operator_name': 'Counter 02 - Cash Desk' if is_staff else 'Attendee Mobile Portal',
                'notes': 'Counter Cashier Verified' if is_staff else 'Instant UPI Confirmation',
                'timestamp': r_time.isoformat()
            }
            recharges_to_insert.append(recharge_entry)
            self.mem_recharges.append(recharge_entry)

        # Sort in-memory logs
        self.mem_zone_logs.sort(key=lambda x: x['timestamp'])
        self.mem_transactions.sort(key=lambda x: x['timestamp'], reverse=True)
        self.mem_recharges.sort(key=lambda x: x['timestamp'], reverse=True)

        # If connected to MongoDB Atlas, bulk insert
        if self.is_connected_to_atlas and self.db is not None:
            try:
                await self.db.attendees.insert_many(attendees_to_insert)
                await self.db.zone_logs.insert_many(logs_to_insert)
                await self.db.transactions.insert_many(txs_to_insert)
                await self.db.incidents.insert_many(initial_incidents)
                await self.db.recharges.insert_many(recharges_to_insert)
                logger.info("Successfully seeded MongoDB Atlas collections!")
            except Exception as e:
                logger.error(f"Error seeding MongoDB Atlas: {e}")

    # ─────────────────────── Attendees Methods ───────────────────────
    async def get_attendee_by_id(self, attendee_id: str) -> Optional[Dict[str, Any]]:
        if self.is_connected_to_atlas and self.db is not None:
            return await self.db.attendees.find_one({"_id": attendee_id})
        return self.mem_attendees.get(attendee_id)

    async def get_attendee_by_qr(self, qr_code_hash: str) -> Optional[Dict[str, Any]]:
        if self.is_connected_to_atlas and self.db is not None:
            return await self.db.attendees.find_one({"qr_code_hash": qr_code_hash})
        for att in self.mem_attendees.values():
            if att.get('qr_code_hash') == qr_code_hash:
                return att
        return None

    async def create_attendee(self, name: str, role: str, ticket_type: str, initial_coins: int = 0) -> Dict[str, Any]:
        att_id = generate_id()
        qr_hash = generate_qr_hash()
        attendee = {
            '_id': att_id,
            'name': name.strip(),
            'role': role.strip(),
            'ticket_type': ticket_type.strip(),
            'engagement_score': 0,
            'fest_coins': max(0, initial_coins),
            'qr_code_hash': qr_hash,
            'status': 'active',
            'terminated_at': None,
            'termination_reason': None,
            'registered_at': datetime.now(timezone.utc).isoformat(),
        }
        self.mem_attendees[att_id] = attendee
        if self.is_connected_to_atlas and self.db is not None:
            await self.db.attendees.insert_one(attendee)
        return attendee

    async def update_attendee(self, attendee_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if attendee_id in self.mem_attendees:
            self.mem_attendees[attendee_id].update(updates)
            att = self.mem_attendees[attendee_id]
        else:
            att = None

        if self.is_connected_to_atlas and self.db is not None:
            await self.db.attendees.update_one({"_id": attendee_id}, {"$set": updates})
            att = await self.db.attendees.find_one({"_id": attendee_id})
        return att

    async def delete_attendee(self, attendee_id: str) -> bool:
        if attendee_id in self.mem_attendees:
            del self.mem_attendees[attendee_id]
            self.mem_zone_logs = [l for l in self.mem_zone_logs if l.get('attendee_id') != attendee_id]
            self.mem_transactions = [t for t in self.mem_transactions if t.get('attendee_id') != attendee_id]

        if self.is_connected_to_atlas and self.db is not None:
            await self.db.attendees.delete_one({"_id": attendee_id})
            await self.db.zone_logs.delete_many({"attendee_id": attendee_id})
            await self.db.transactions.delete_many({"attendee_id": attendee_id})
            await self.db.recharges.delete_many({"attendee_id": attendee_id})
        return True

    async def list_attendees(self, query: str = "", limit: int = 250) -> List[Dict[str, Any]]:
        q = query.strip().lower()
        if self.is_connected_to_atlas and self.db is not None:
            mongo_filter = {}
            if q:
                mongo_filter = {
                    "$or": [
                        {"name": {"$regex": q, "$options": "i"}},
                        {"role": {"$regex": q, "$options": "i"}},
                        {"ticket_type": {"$regex": q, "$options": "i"}},
                        {"qr_code_hash": {"$regex": q, "$options": "i"}},
                        {"_id": {"$regex": q, "$options": "i"}},
                    ]
                }
            cursor = self.db.attendees.find(mongo_filter).sort("registered_at", -1).limit(limit)
            return await cursor.to_list(length=limit)

        all_att = list(self.mem_attendees.values())
        if q:
            all_att = [
                a for a in all_att if (
                    q in a.get('name', '').lower() or
                    q in a.get('role', '').lower() or
                    q in a.get('ticket_type', '').lower() or
                    q in a.get('qr_code_hash', '').lower() or
                    q in a.get('_id', '').lower()
                )
            ]
        all_att.sort(key=lambda x: x.get('registered_at', ''), reverse=True)
        return all_att[:limit]

    # ─────────────────────── Zone Scans & Telemetry ───────────────────────
    async def log_zone_scan(self, attendee: Dict[str, Any], zone_name: str) -> Dict[str, Any]:
        # Increment engagement score
        att_id = attendee['_id']
        new_score = attendee.get('engagement_score', 0) + SCAN_ENGAGEMENT_POINTS
        await self.update_attendee(att_id, {'engagement_score': new_score})

        log_entry = {
            '_id': generate_id(),
            'attendee_id': att_id,
            'qr_code_hash': attendee['qr_code_hash'],
            'role': attendee['role'],
            'zone_name': zone_name,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'seeded': False,
            'simulated': False,
        }
        self.mem_zone_logs.append(log_entry)
        if self.is_connected_to_atlas and self.db is not None:
            await self.db.zone_logs.insert_one(log_entry)
        return log_entry

    async def simulate_surge(self) -> int:
        att_list = await self.list_attendees(limit=200)
        now_iso = datetime.now(timezone.utc).isoformat()
        new_logs = []
        for att in att_list:
            new_score = att.get('engagement_score', 0) + SCAN_ENGAGEMENT_POINTS
            await self.update_attendee(att['_id'], {'engagement_score': new_score})
            entry = {
                '_id': generate_id(),
                'attendee_id': att['_id'],
                'qr_code_hash': att['qr_code_hash'],
                'role': att['role'],
                'zone_name': 'Cultural Pavilion',
                'timestamp': now_iso,
                'seeded': True,
                'simulated': True,
            }
            new_logs.append(entry)
            self.mem_zone_logs.append(entry)

        if self.is_connected_to_atlas and self.db is not None and new_logs:
            await self.db.zone_logs.insert_many(new_logs)
        return len(new_logs)

    async def revert_simulation(self) -> int:
        count = 0
        self.mem_zone_logs = [l for l in self.mem_zone_logs if not l.get('simulated', False)]
        if self.is_connected_to_atlas and self.db is not None:
            res = await self.db.zone_logs.delete_many({"simulated": True})
            count = res.deleted_count
        return count

    # ─────────────────────── Transactions & Recharges ───────────────────────
    async def create_transaction(self, attendee: Dict[str, Any], amount: int, stall_name: str, zone_name: str) -> Dict[str, Any]:
        att_id = attendee['_id']
        new_balance = attendee.get('fest_coins', 0) - amount
        await self.update_attendee(att_id, {'fest_coins': new_balance})

        tx = {
            '_id': generate_id(),
            'attendee_id': att_id,
            'attendee_name': attendee['name'],
            'qr_code_hash': attendee['qr_code_hash'],
            'amount': amount,
            'stall_name': stall_name or 'Vendor Stall',
            'zone_name': zone_name if zone_name in ZONES else 'Food Court',
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        self.mem_transactions.insert(0, tx)
        if self.is_connected_to_atlas and self.db is not None:
            await self.db.transactions.insert_one(tx)
        return tx

    async def create_recharge(self, attendee: Dict[str, Any], inr_amount: int, payment_method: str = "UPI",
                              operator_mode: str = "self_service", operator_name: Optional[str] = None, notes: str = "") -> Dict[str, Any]:
        att_id = attendee['_id']
        coins_added = inr_amount
        new_balance = attendee.get('fest_coins', 0) + coins_added
        await self.update_attendee(att_id, {'fest_coins': new_balance})

        r_time = datetime.now(timezone.utc)
        payment_ref = f"TXN-INR-{int(r_time.timestamp())}-{secrets.randbelow(9000) + 1000}"

        recharge = {
            '_id': generate_id(),
            'payment_ref': payment_ref,
            'attendee_id': att_id,
            'attendee_name': attendee['name'],
            'attendee_role': attendee.get('role', 'General'),
            'qr_code_hash': attendee['qr_code_hash'],
            'inr_amount': inr_amount,
            'coins_added': coins_added,
            'conversion_ratio': '1 INR = 1 Coin',
            'payment_method': payment_method or 'UPI',
            'operator_mode': 'staff_desk' if operator_mode == 'staff_desk' else 'self_service',
            'operator_name': operator_name or ('Counter Desk Cashier' if operator_mode == 'staff_desk' else 'Self-Service Mobile Portal'),
            'notes': notes or '',
            'timestamp': r_time.isoformat(),
        }
        self.mem_recharges.insert(0, recharge)
        if self.is_connected_to_atlas and self.db is not None:
            await self.db.recharges.insert_one(recharge)
        return recharge

    async def get_recharges_summary(self, limit: int = 100) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if self.is_connected_to_atlas and self.db is not None:
            recharges_cursor = self.db.recharges.find().sort("timestamp", -1).limit(limit)
            recharges_list = await recharges_cursor.to_list(length=limit)
            all_recharges_cursor = self.db.recharges.find()
            all_recharges = await all_recharges_cursor.to_list(length=2000)
        else:
            recharges_list = self.mem_recharges[:limit]
            all_recharges = self.mem_recharges

        total_inr = sum(r.get('inr_amount', 0) for r in all_recharges)
        total_coins = sum(r.get('coins_added', 0) for r in all_recharges)
        method_counts: Dict[str, int] = {}
        mode_counts = {'self_service': 0, 'staff_desk': 0}

        for r in all_recharges:
            m = r.get('payment_method', 'UPI')
            method_counts[m] = method_counts.get(m, 0) + 1
            op = r.get('operator_mode', 'self_service')
            if op in mode_counts:
                mode_counts[op] += 1

        summary = {
            'total_inr_collected': total_inr,
            'total_coins_issued': total_coins,
            'conversion_ratio': '1 Rupee = 1 Fest Coin',
            'total_transactions': len(all_recharges),
            'method_counts': method_counts,
            'mode_counts': mode_counts,
        }
        return recharges_list, summary

    # ─────────────────────── Incidents ───────────────────────
    async def create_incident(self, incident_type: str, zone_name: str, notes: str, coordinator_name: str = "Field Coordinator") -> Dict[str, Any]:
        incident = {
            '_id': generate_id(),
            'type': incident_type,
            'zone_name': zone_name if zone_name in ZONES else 'Main Stage',
            'notes': notes or f"Urgent {incident_type} assistance reported at {zone_name}.",
            'status': 'Under Processing',
            'coordinator_name': coordinator_name or "Field Coordinator",
            'reported_at': datetime.now(timezone.utc).isoformat(),
            'resolved_at': None,
            'resolved_by': None,
            'resolution_notes': None,
        }
        self.mem_incidents.insert(0, incident)
        if self.is_connected_to_atlas and self.db is not None:
            await self.db.incidents.insert_one(incident)
        self.invalidate_admin_stats_cache()
        return incident

    async def resolve_incident(self, incident_id: str, coordinator_name: Optional[str] = None, resolution_notes: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return await self.update_incident_status(incident_id, "Resolved", coordinator_name, resolution_notes)

    async def update_incident_status(self, incident_id: str, new_status: str, coordinator_name: Optional[str] = None, resolution_notes: Optional[str] = None) -> Optional[Dict[str, Any]]:
        now_iso = datetime.now(timezone.utc).isoformat()
        is_resolved = (str(new_status).lower() == 'resolved')
        status_str = 'Resolved' if is_resolved else 'Under Processing'

        found = None
        for inc in self.mem_incidents:
            if inc.get('_id') == incident_id:
                inc['status'] = status_str
                if is_resolved:
                    inc['resolved_at'] = now_iso
                    inc['resolved_by'] = coordinator_name or inc.get('coordinator_name') or 'Coordinator'
                    if resolution_notes:
                        inc['resolution_notes'] = resolution_notes
                else:
                    inc['resolved_at'] = None
                    inc['resolved_by'] = None
                found = inc
                break

        if self.is_connected_to_atlas and self.db is not None:
            update_data: Dict[str, Any] = {
                "status": status_str,
                "resolved_at": now_iso if is_resolved else None,
                "resolved_by": (coordinator_name or "Coordinator") if is_resolved else None,
            }
            if resolution_notes:
                update_data["resolution_notes"] = resolution_notes
            await self.db.incidents.update_one(
                {"_id": incident_id},
                {"$set": update_data}
            )
            found = await self.db.incidents.find_one({"_id": incident_id})

        self.invalidate_admin_stats_cache()
        return found

    async def list_recent_incidents(self, limit: int = 50) -> List[Dict[str, Any]]:
        if self.is_connected_to_atlas and self.db is not None:
            raw = await self.db.incidents.find().sort("reported_at", -1).to_list(length=limit)
        else:
            raw = self.mem_incidents[:limit]

        results = []
        for i in sorted(raw, key=lambda x: str(x.get('reported_at', '')), reverse=True):
            status = 'Resolved' if str(i.get('status', '')).lower() == 'resolved' else 'Under Processing'
            results.append({
                'id': str(i.get('_id', '')),
                'type': i.get('type', 'Medical'),
                'zone_name': i.get('zone_name', 'Main Stage'),
                'notes': i.get('notes', ''),
                'status': status,
                'coordinator_name': i.get('coordinator_name', 'Field Coordinator'),
                'reported_at': i.get('reported_at'),
                'resolved_at': i.get('resolved_at'),
                'resolved_by': i.get('resolved_by'),
                'resolution_notes': i.get('resolution_notes'),
            })
        return results

    # ─────────────────────── Diversions ───────────────────────
    async def trigger_diversion(self, zone_name: str, target_zone: str, message: str) -> Tuple[Dict[str, Any], int]:
        target = target_zone if target_zone in ZONES else 'Food Court'
        diversion = {
            '_id': generate_id(),
            'zone_name': zone_name,
            'target_zone': target,
            'message': message or f"Active Crowd Diversion: Special 50% discount at {target}!",
            'created_at': datetime.now(timezone.utc).isoformat(),
            'active': True,
        }
        # Mark existing active diversions for this zone inactive
        for d in self.mem_diversions:
            if d.get('zone_name') == zone_name:
                d['active'] = False

        self.mem_diversions.insert(0, diversion)

        if self.is_connected_to_atlas and self.db is not None:
            await self.db.diversions.update_many({"zone_name": zone_name}, {"$set": {"active": False}})
            await self.db.diversions.insert_one(diversion)

        # Relieve 65% of recent scans in congested zone
        relieved = await self.reroute_recent_scans(zone_name, target)
        return diversion, relieved

    async def clear_diversion(self, diversion_id: str) -> bool:
        found = False
        for d in self.mem_diversions:
            if d.get('_id') == diversion_id:
                d['active'] = False
                found = True

        if self.is_connected_to_atlas and self.db is not None:
            res = await self.db.diversions.update_one({"_id": diversion_id}, {"$set": {"active": False}})
            if res.modified_count > 0:
                found = True
        return found

    async def reroute_recent_scans(self, from_zone: str, to_zone: str, limit: int = 100) -> int:
        ten_mins_ago = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        relieved = 0

        # In-memory update
        for log in reversed(self.mem_zone_logs):
            if log.get('zone_name') == from_zone and log.get('timestamp', '') >= ten_mins_ago:
                log['zone_name'] = to_zone
                relieved += 1
                if relieved >= limit:
                    break

        if self.is_connected_to_atlas and self.db is not None:
            cursor = self.db.zone_logs.find({
                "zone_name": from_zone,
                "timestamp": {"$gte": ten_mins_ago}
            }).sort("timestamp", -1).limit(limit)
            ids_to_update = [doc["_id"] async for doc in cursor]
            if ids_to_update:
                await self.db.zone_logs.update_many(
                    {"_id": {"$in": ids_to_update}},
                    {"$set": {"zone_name": to_zone}}
                )
        self.invalidate_admin_stats_cache()
        return relieved

    # ─────────────────────── Aggregations & Admin Stats ───────────────────────
    async def get_admin_stats(self) -> Dict[str, Any]:
        now_ts = time.time()
        if self._stats_cache is not None and (now_ts - self._stats_cache_time) < self._stats_cache_ttl:
            return copy.deepcopy(self._stats_cache)

        ten_mins_ago = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

        if self.is_connected_to_atlas and self.db is not None:
            # Parallel query execution across WAN to minimize Atlas latency by 10x
            all_attendees, all_logs, all_txs, all_recharges, all_incidents, active_divs = await asyncio.gather(
                self.db.attendees.find({}, {"_id": 1, "name": 1, "engagement_score": 1}).to_list(length=1000),
                self.db.zone_logs.find({}, {"zone_name": 1, "timestamp": 1, "attendee_id": 1, "role": 1}).to_list(length=5000),
                self.db.transactions.find({}, {"_id": 1, "amount": 1, "attendee_name": 1, "stall_name": 1, "zone_name": 1, "timestamp": 1}).to_list(length=1000),
                self.db.recharges.find({}, {"_id": 1, "inr_amount": 1, "coins_added": 1, "attendee_name": 1, "payment_method": 1, "operator_mode": 1, "timestamp": 1, "payment_ref": 1}).to_list(length=1000),
                self.db.incidents.find().sort("reported_at", -1).to_list(length=50),
                self.db.diversions.find({"active": True}).to_list(length=20)
            )
        else:
            all_attendees = list(self.mem_attendees.values())
            all_logs = self.mem_zone_logs
            all_txs = self.mem_transactions
            all_recharges = self.mem_recharges
            all_incidents = self.mem_incidents
            active_divs = [d for d in self.mem_diversions if d.get('active')]

        total_attendees = len(all_attendees)
        total_engagement = sum(a.get('engagement_score', 0) for a in all_attendees)

        recent_counts = {z: 0 for z in ZONES}
        total_counts = {z: 0 for z in ZONES}

        for log in all_logs:
            z = log.get('zone_name')
            if z in total_counts:
                total_counts[z] += 1
            if log.get('timestamp', '') >= ten_mins_ago:
                if z in recent_counts:
                    recent_counts[z] += 1

        active_div_map = {d['zone_name']: d for d in active_divs if 'zone_name' in d}

        zones_stats = []
        for z in ZONES:
            recent = recent_counts.get(z, 0)
            total = total_counts.get(z, 0)
            zones_stats.append({
                'zone_name': z,
                'total_scans': total,
                'recent_scans': recent,
                'capacity_warning': recent > BOTTLENECK_THRESHOLD,
                'active_diversion': active_div_map.get(z),
            })

        # Recent 10 logs with attendee lookup (reduced by 10 names for compact live guest feed)
        att_map = {a['_id']: a.get('name', 'Unknown') for a in all_attendees}
        sorted_logs = sorted(all_logs, key=lambda x: x.get('timestamp', ''), reverse=True)
        recent_10 = []
        for l in sorted_logs[:10]:
            recent_10.append({
                'id': l.get('_id'),
                'zone_name': l.get('zone_name'),
                'timestamp': l.get('timestamp'),
                'attendee_name': att_map.get(l.get('attendee_id'), 'Unknown'),
                'role': l.get('role', 'General'),
            })

        total_revenue = sum(tx.get('amount', 0) for tx in all_txs)
        total_inr_recharged = sum(r.get('inr_amount', 0) for r in all_recharges)

        recent_txs = [
            {
                'id': tx.get('_id'),
                'attendee_name': tx.get('attendee_name', 'Attendee'),
                'amount': tx.get('amount', 0),
                'stall_name': tx.get('stall_name', 'Stall'),
                'zone_name': tx.get('zone_name', 'Food Court'),
                'timestamp': tx.get('timestamp'),
            }
            for tx in sorted(all_txs, key=lambda x: x.get('timestamp', ''), reverse=True)[:10]
        ]

        recent_recharges = [
            {
                'id': r.get('_id'),
                'payment_ref': r.get('payment_ref', ''),
                'attendee_name': r.get('attendee_name', ''),
                'inr_amount': r.get('inr_amount', 0),
                'coins_added': r.get('coins_added', 0),
                'payment_method': r.get('payment_method', 'UPI'),
                'operator_mode': r.get('operator_mode', 'self_service'),
                'timestamp': r.get('timestamp'),
            }
            for r in sorted(all_recharges, key=lambda x: x.get('timestamp', ''), reverse=True)[:10]
        ]

        formatted_incidents = []
        for i in sorted(all_incidents, key=lambda x: str(x.get('reported_at', '')), reverse=True):
            status = 'Resolved' if str(i.get('status', '')).lower() == 'resolved' else 'Under Processing'
            formatted_incidents.append({
                'id': str(i.get('_id', '')),
                'type': i.get('type', 'Medical'),
                'zone_name': i.get('zone_name', 'Main Stage'),
                'notes': i.get('notes', ''),
                'status': status,
                'coordinator_name': i.get('coordinator_name', 'Field Coordinator'),
                'reported_at': i.get('reported_at'),
                'resolved_at': i.get('resolved_at'),
                'resolved_by': i.get('resolved_by'),
                'resolution_notes': i.get('resolution_notes'),
            })

        under_processing_count = sum(1 for inc in formatted_incidents if inc['status'] == 'Under Processing')
        resolved_count = sum(1 for inc in formatted_incidents if inc['status'] == 'Resolved')

        formatted_diversions = [
            {
                'id': d.get('_id'),
                'zone_name': d.get('zone_name'),
                'target_zone': d.get('target_zone'),
                'message': d.get('message'),
                'created_at': d.get('created_at'),
            }
            for d in active_divs
        ]

        result = {
            'zones': zones_stats,
            'recent_logs': recent_10,
            'total_attendees': total_attendees,
            'total_engagement': total_engagement,
            'financials': {
                'total_revenue': total_revenue,
                'transaction_volume': len(all_txs),
                'live_transactions': len(all_txs),
                'total_inr_recharged': total_inr_recharged,
                'recharge_volume': len(all_recharges),
                'conversion_ratio': '1 INR = 1 Coin',
                'recent_transactions': recent_txs,
                'recent_recharges': recent_recharges,
            },
            'incidents': formatted_incidents,
            'active_incidents': formatted_incidents,
            'incidents_summary': {
                'under_processing': under_processing_count,
                'resolved': resolved_count,
                'total': len(formatted_incidents)
            },
            'active_diversions': formatted_diversions,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }

        self._stats_cache = copy.deepcopy(result)
        self._stats_cache_time = time.time()
        return result

    # ─────────────────────── Attendee Unified History ───────────────────────
    async def get_attendee_history(self, attendee_id: str) -> Optional[Dict[str, Any]]:
        attendee = await self.get_attendee_by_id(attendee_id)
        if not attendee:
            return None

        if self.is_connected_to_atlas and self.db is not None:
            recharges = await self.db.recharges.find({"attendee_id": attendee_id}).sort("timestamp", -1).to_list(length=100)
            transactions = await self.db.transactions.find({"attendee_id": attendee_id}).sort("timestamp", -1).to_list(length=100)
            scans = await self.db.zone_logs.find({"attendee_id": attendee_id}).sort("timestamp", -1).to_list(length=100)
        else:
            recharges = [r for r in self.mem_recharges if r.get('attendee_id') == attendee_id]
            recharges.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            transactions = [t for t in self.mem_transactions if t.get('attendee_id') == attendee_id]
            transactions.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            scans = [s for s in self.mem_zone_logs if s.get('attendee_id') == attendee_id]
            scans.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

        total_recharged_inr = sum(r.get('inr_amount', 0) for r in recharges)
        total_spent_coins = sum(t.get('amount', 0) for t in transactions)

        return {
            'attendee': attendee,
            'recharges': recharges,
            'transactions': transactions,
            'scans': scans,
            'summary': {
                'current_balance_coins': attendee.get('fest_coins', 0),
                'current_balance_inr': attendee.get('fest_coins', 0),
                'total_recharged_inr': total_recharged_inr,
                'total_spent_coins': total_spent_coins,
                'total_scans': len(scans),
            }
        }

    def get_status_summary(self) -> Dict[str, Any]:
        return {
            'atlas_connected': self.is_connected_to_atlas,
            'database_uri_configured': bool(self.mongodb_uri),
            'connection_message': self.connection_message,
            'attendee_count': len(self.mem_attendees) if not self.is_connected_to_atlas else 'synced in Atlas',
            'log_count': len(self.mem_zone_logs) if not self.is_connected_to_atlas else 'synced in Atlas',
        }

db_manager = DatabaseManager()
