import express from 'express';
import cors from 'cors';
import path from 'path';
import crypto from 'crypto';
import { fileURLToPath } from 'url';
import { GoogleGenAI } from '@google/genai';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = process.env.PORT || 3000;
const HOST = '0.0.0.0';

app.use((req, res, next) => {
  res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0');
  res.setHeader('Pragma', 'no-cache');
  res.setHeader('Expires', '0');
  next();
});

// ─────────────────────── Security & Auth ───────────────────────
const ADMIN_ACCESS_KEY = (process.env.ADMIN_KEY || '').trim();
const FIELD_CREW_KEY = (process.env.FIELD_CREW_KEY || 'FIELD-CREW-KEY').trim();

function verifyAdmin(req, res, next) {
  const adminKey = req.headers['x-admin-key'];
  if (!ADMIN_ACCESS_KEY || !adminKey || adminKey.trim() !== ADMIN_ACCESS_KEY) {
    return res.status(401).json({ detail: 'Unauthorized Access' });
  }
  next();
}

function verifyFieldOrAdmin(req, res, next) {
  const adminKey = req.headers['x-admin-key'];
  const fieldKey = req.headers['x-field-crew-key'];

  if (ADMIN_ACCESS_KEY && adminKey && adminKey.trim() === ADMIN_ACCESS_KEY) {
    return next();
  }

  if (FIELD_CREW_KEY) {
    if (fieldKey && fieldKey.trim() === FIELD_CREW_KEY) {
      return next();
    }
    if (adminKey && adminKey.trim() === FIELD_CREW_KEY) {
      return next();
    }
  }

  return res.status(401).json({ detail: 'Unauthorized: Valid field crew or admin credentials required' });
}

// ─────────────────────── Domain Constants ───────────────────────
const ZONES = ['Main Stage', 'Food Court', 'Artisan Market', 'Cultural Pavilion', 'VIP Lounge'];
const ROLES = ['General', 'Early Bird', 'VIP', 'Organizer', 'Sponsor'];
const SCAN_ENGAGEMENT_POINTS = 10;
const BOTTLENECK_THRESHOLD = 150;

// ─────────────────────── In-Memory Datastore ───────────────────────
const attendees = new Map();
const zoneLogs = [];
const transactions = [];
const incidents = [];
const diversions = [];
const recharges = [];

function generateId() {
  return crypto.randomBytes(12).toString('hex');
}

// ─────────────────────── Seed Initial Data ───────────────────────
function seedData() {
  const firstNames = [
    'Aarav', 'Vivaan', 'Aditya', 'Arjun', 'Sai', 'Reyansh', 'Krishna', 'Ishaan', 'Rohan', 'Kabir',
    'Ananya', 'Diya', 'Saanvi', 'Aadhya', 'Priya', 'Isha', 'Meera', 'Riya', 'Sneha', 'Kavya',
    'Rahul', 'Neha', 'Vikram', 'Pooja', 'Karan', 'Tanvi', 'Dev', 'Nisha', 'Sameer', 'Aisha',
    'Liam', 'Olivia', 'Noah', 'Emma', 'Lucas', 'Mia', 'Ethan', 'Sophia', 'Mason', 'Isabella'
  ];
  const lastNames = [
    'Sharma', 'Verma', 'Gupta', 'Patel', 'Reddy', 'Iyer', 'Nair', 'Singh', 'Kumar', 'Das',
    'Mehta', 'Joshi', 'Rao', 'Chopra', 'Bose', 'Malhotra', 'Kapoor', 'Menon', 'Pillai', 'Agarwal',
    'Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis', 'Rodriguez', 'Martinez'
  ];

  const tickets = {
    General: ['Standard', 'Student', 'Last Minute'],
    'Early Bird': ['Early Bird Std', 'Early Bird Student'],
    VIP: ['Platinum', 'Investor', 'Speaker'],
    Organizer: ['Staff', 'Volunteer'],
    Sponsor: ['Corporate', 'Executive Sponsor'],
  };

  const zoneAffinities = {
    General: { 'Main Stage': 2, 'Food Court': 2, 'Artisan Market': 2, 'Cultural Pavilion': 5, 'VIP Lounge': 1 },
    'Early Bird': { 'Main Stage': 2, 'Food Court': 2, 'Artisan Market': 2, 'Cultural Pavilion': 5, 'VIP Lounge': 1 },
    VIP: { 'Main Stage': 4, 'Food Court': 3, 'Artisan Market': 1, 'Cultural Pavilion': 1, 'VIP Lounge': 5 },
    Organizer: { 'Main Stage': 1, 'Food Court': 1, 'Artisan Market': 1, 'Cultural Pavilion': 1, 'VIP Lounge': 1 },
    Sponsor: { 'Main Stage': 3, 'Food Court': 2, 'Artisan Market': 1, 'Cultural Pavilion': 1, 'VIP Lounge': 4 },
  };

  const now = Date.now();
  const attendeeList = [];

  // Generate 250 attendees with Fest Coins default (1,000)
  for (let i = 0; i < 250; i++) {
    const roleChoice = Math.random();
    let role = 'General';
    if (roleChoice < 0.60) role = 'General';
    else if (roleChoice < 0.85) role = 'Early Bird';
    else if (roleChoice < 0.95) role = 'Organizer';
    else role = 'VIP';

    const fn = firstNames[Math.floor(Math.random() * firstNames.length)];
    const ln = lastNames[Math.floor(Math.random() * lastNames.length)];
    const ticketList = tickets[role] || ['Standard'];
    const ticket = ticketList[Math.floor(Math.random() * ticketList.length)];

    const id = generateId();
    const qrHash = crypto.randomBytes(16).toString('hex');
    const registeredAt = new Date(now - Math.random() * 48 * 3600 * 1000);

    const attendee = {
      _id: id,
      name: `${fn} ${ln}`,
      role,
      ticket_type: ticket,
      engagement_score: 0,
      fest_coins: 1000,
      qr_code_hash: qrHash,
      status: 'active',
      terminated_at: null,
      termination_reason: null,
      registered_at: registeredAt.toISOString(),
    };

    attendees.set(id, attendee);
    attendeeList.push(attendee);
  }

  // Generate historical scans (past 4 hours)
  const fourHoursMs = 4 * 3600 * 1000;
  for (let i = 0; i < 1800; i++) {
    const att = attendeeList[Math.floor(Math.random() * attendeeList.length)];
    const affinities = zoneAffinities[att.role] || zoneAffinities.General;

    let totalWeight = 0;
    for (const z of ZONES) totalWeight += affinities[z] || 1;
    let r = Math.random() * totalWeight;
    let chosenZone = ZONES[0];
    for (const z of ZONES) {
      r -= affinities[z] || 1;
      if (r <= 0) {
        chosenZone = z;
        break;
      }
    }

    const scanTime = new Date(now - Math.random() * fourHoursMs);
    att.engagement_score += SCAN_ENGAGEMENT_POINTS;

    zoneLogs.push({
      _id: generateId(),
      attendee_id: att._id,
      qr_code_hash: att.qr_code_hash,
      role: att.role,
      zone_name: chosenZone,
      timestamp: scanTime,
      seeded: true,
    });
  }

  // Plant recent surge in Cultural Pavilion (180 scans in last 10 minutes)
  const tenMinsMs = 10 * 60 * 1000;
  for (let i = 0; i < 180; i++) {
    const att = attendeeList[Math.floor(Math.random() * attendeeList.length)];
    const scanTime = new Date(now - Math.random() * (tenMinsMs - 15000) - 5000);
    att.engagement_score += SCAN_ENGAGEMENT_POINTS;

    zoneLogs.push({
      _id: generateId(),
      attendee_id: att._id,
      qr_code_hash: att.qr_code_hash,
      role: att.role,
      zone_name: 'Cultural Pavilion',
      timestamp: scanTime,
      seeded: true,
    });
  }

  // Pre-seed 65 Fest Coin Cashless transactions
  const stalls = [
    { name: 'Food Court - Saffron Grill', zone: 'Food Court', price: 150 },
    { name: 'Artisan Market - Silk & Spice', zone: 'Artisan Market', price: 200 },
    { name: 'Main Stage - Beverage Bar', zone: 'Main Stage', price: 120 },
    { name: 'Cultural Pavilion - Craft Hub', zone: 'Cultural Pavilion', price: 80 },
    { name: 'VIP Lounge - Signature Cocktails', zone: 'VIP Lounge', price: 250 },
  ];

  for (let i = 0; i < 65; i++) {
    const att = attendeeList[Math.floor(Math.random() * attendeeList.length)];
    const stall = stalls[Math.floor(Math.random() * stalls.length)];
    const deduct = stall.price;

    if (att.fest_coins >= deduct) {
      att.fest_coins -= deduct;
      transactions.push({
        _id: generateId(),
        attendee_id: att._id,
        attendee_name: att.name,
        qr_code_hash: att.qr_code_hash,
        amount: deduct,
        stall_name: stall.name,
        zone_name: stall.zone,
        timestamp: new Date(now - Math.random() * 2 * 3600 * 1000),
      });
    }
  }

  // Pre-seed initial incidents
  incidents.push({
    _id: generateId(),
    type: 'Medical',
    zone_name: 'Cultural Pavilion',
    notes: 'Attendee experiencing heat exhaustion near northern gallery entrance.',
    status: 'Active',
    reported_at: new Date(now - 4 * 60 * 1000),
    resolved_at: null,
  });

  incidents.push({
    _id: generateId(),
    type: 'Security',
    zone_name: 'Main Stage',
    notes: 'Perimeter barrier loose near stage right sound console.',
    status: 'Active',
    reported_at: new Date(now - 8 * 60 * 1000),
    resolved_at: null,
  });

  incidents.push({
    _id: generateId(),
    type: 'Logistics',
    zone_name: 'Food Court',
    notes: 'Water refill station 3 valve replacement requested.',
    status: 'Resolved',
    reported_at: new Date(now - 35 * 60 * 1000),
    resolved_at: new Date(now - 12 * 60 * 1000),
  });

  // Pre-seed 40 Rupee-to-Fest-Coin recharges (1 INR = 1 Coin)
  const paymentMethods = ['UPI - GPay', 'UPI - PhonePe', 'UPI - Paytm', 'Cash Desk', 'Card (Visa/Mastercard)', 'NetBanking'];
  for (let i = 0; i < 40; i++) {
    const att = attendeeList[Math.floor(Math.random() * attendeeList.length)];
    const rupeeAmount = [100, 200, 250, 500, 1000, 1500][Math.floor(Math.random() * 6)];
    const method = paymentMethods[Math.floor(Math.random() * paymentMethods.length)];
    const isStaff = method === 'Cash Desk' || Math.random() < 0.3;
    recharges.push({
      _id: generateId(),
      payment_ref: `TXN-INR-${Date.now().toString(36).toUpperCase()}-${Math.floor(1000 + Math.random() * 9000)}`,
      attendee_id: att._id,
      attendee_name: att.name,
      attendee_role: att.role,
      qr_code_hash: att.qr_code_hash,
      inr_amount: rupeeAmount,
      coins_added: rupeeAmount,
      conversion_ratio: '1 INR = 1 Coin',
      payment_method: method,
      operator_mode: isStaff ? 'staff_desk' : 'self_service',
      operator_name: isStaff ? 'Counter 02 - Cash Desk' : 'Attendee Mobile Portal',
      notes: isStaff ? 'Counter Cashier Verified' : 'Instant UPI Confirmation',
      timestamp: new Date(now - Math.random() * 6 * 3600 * 1000),
    });
  }

  // Sort logs chronologically
  zoneLogs.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
  transactions.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
  recharges.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
  console.log(`[Seed] Seeded ${attendees.size} attendees, ${zoneLogs.length} logs, ${transactions.length} transactions, ${recharges.length} recharges, and ${incidents.length} incidents.`);
}

seedData();

// ─────────────────────── Middlewares ───────────────────────
app.use(cors());
app.use(express.json());

// ─────────────────────── API Endpoints ───────────────────────

// Canonical zones list
app.get('/zones', (req, res) => {
  res.json({ zones: ZONES });
});

// Register attendee (defaults fest_coins to 0, attendees top up via payment page)
app.post('/register_attendee', (req, res) => {
  const { name, role, ticket_type, initial_coins } = req.body;
  if (!name || !role || !ticket_type) {
    return res.status(400).json({ detail: 'Missing required attendee fields' });
  }

  const id = generateId();
  const qrHash = crypto.randomBytes(16).toString('hex');
  const attendee = {
    _id: id,
    name: String(name).trim(),
    role: String(role),
    ticket_type: String(ticket_type).trim(),
    engagement_score: 0,
    fest_coins: initial_coins !== undefined ? Math.max(0, parseInt(initial_coins, 10) || 0) : 0,
    qr_code_hash: qrHash,
    status: 'active',
    terminated_at: null,
    termination_reason: null,
    registered_at: new Date().toISOString(),
  };

  attendees.set(id, attendee);

  res.status(201).json({
    message: 'Attendee registered successfully',
    qr_code_hash: qrHash,
    attendee,
  });
});

// Get attendee by QR code hash
app.get('/attendee/:qr_code_hash', (req, res) => {
  const { qr_code_hash } = req.params;
  const attendee = Array.from(attendees.values()).find((a) => a.qr_code_hash === qr_code_hash);
  if (!attendee) {
    return res.status(404).json({ detail: 'Attendee not found' });
  }
  res.json(attendee);
});

// Scan attendee checkpoint (Check-in movement)
app.post('/scan', (req, res) => {
  const { qr_code_hash, zone_name } = req.body;
  if (!qr_code_hash || !zone_name || !ZONES.includes(zone_name)) {
    return res.status(400).json({ detail: 'Invalid scan payload or zone' });
  }

  const attendee = Array.from(attendees.values()).find((a) => a.qr_code_hash === qr_code_hash);
  if (!attendee) {
    return res.status(404).json({ detail: 'Attendee not found' });
  }

  // Security enforcement: Reject terminated passes
  if (attendee.status === 'terminated') {
    return res.status(403).json({
      detail: `SECURITY ALERT: QR Pass is REVOKED/TERMINATED (${attendee.termination_reason || 'Access Revoked by Administration'})`,
      terminated: true,
      attendee: {
        id: attendee._id,
        name: attendee.name,
        role: attendee.role,
        ticket_type: attendee.ticket_type,
        status: attendee.status,
        terminated_at: attendee.terminated_at,
        termination_reason: attendee.termination_reason,
      },
    });
  }

  attendee.engagement_score += SCAN_ENGAGEMENT_POINTS;

  const log = {
    _id: generateId(),
    attendee_id: attendee._id,
    qr_code_hash: attendee.qr_code_hash,
    role: attendee.role,
    zone_name,
    timestamp: new Date(),
  };

  zoneLogs.push(log);

  res.status(201).json({
    message: `Scan logged at ${zone_name}!`,
    attendee,
    zone_log: {
      ...log,
      timestamp: log.timestamp.toISOString(),
    },
  });
});

// ─────────────────────── FEATURE 1: Fest Coin Cashless Economy ───────────────────────

// Process a payment / transaction from field scanner
app.post('/transaction', (req, res) => {
  const { qr_code_hash, amount, stall_name, zone_name } = req.body;
  const coinAmount = parseInt(amount, 10);

  if (!qr_code_hash || isNaN(coinAmount) || coinAmount <= 0) {
    return res.status(400).json({ detail: 'Invalid transaction parameters' });
  }

  const attendee = Array.from(attendees.values()).find((a) => a.qr_code_hash === qr_code_hash);
  if (!attendee) {
    return res.status(404).json({ detail: 'Attendee QR code not registered' });
  }

  // Security enforcement: Reject terminated passes
  if (attendee.status === 'terminated') {
    return res.status(403).json({
      detail: `SECURITY ALERT: Payment Denied! QR Pass is REVOKED/TERMINATED (${attendee.termination_reason || 'Pass Revoked'})`,
      terminated: true,
      attendee: {
        id: attendee._id,
        name: attendee.name,
        role: attendee.role,
        status: attendee.status,
        terminated_at: attendee.terminated_at,
        termination_reason: attendee.termination_reason,
      },
    });
  }

  if (attendee.fest_coins < coinAmount) {
    return res.status(400).json({
      detail: `Insufficient Fest Coins. Current balance: ${attendee.fest_coins} coins, Required: ${coinAmount} coins`,
      current_balance: attendee.fest_coins,
    });
  }

  attendee.fest_coins -= coinAmount;

  const tx = {
    _id: generateId(),
    attendee_id: attendee._id,
    attendee_name: attendee.name,
    qr_code_hash: attendee.qr_code_hash,
    amount: coinAmount,
    stall_name: stall_name || 'Vendor Stall',
    zone_name: zone_name && ZONES.includes(zone_name) ? zone_name : 'Food Court',
    timestamp: new Date(),
  };

  transactions.unshift(tx);

  res.status(201).json({
    message: `Successfully deducted ${coinAmount} Fest Coins for ${stall_name || 'Vendor Stall'}`,
    transaction: {
      ...tx,
      timestamp: tx.timestamp.toISOString(),
    },
    attendee: {
      name: attendee.name,
      fest_coins: attendee.fest_coins,
    },
  });
});

// ─────────────────────── FEATURE: Rupee-to-Coin Cashless Top-up (1 INR = 1 Coin) ───────────────────────

// Process a payment recharge (dual mode: self-service or staff desk)
app.post('/api/recharge', (req, res) => {
  const {
    attendee_id,
    qr_code_hash,
    inr_amount,
    payment_method = 'UPI',
    operator_mode = 'self_service',
    operator_name,
    notes,
  } = req.body;

  const rupeeAmount = parseInt(inr_amount, 10);
  if (isNaN(rupeeAmount) || rupeeAmount <= 0) {
    return res.status(400).json({ detail: 'Invalid recharge amount. Must be greater than 0.' });
  }

  // Find attendee by ID or QR hash
  let attendee = null;
  if (attendee_id) {
    attendee = attendees.get(attendee_id);
  } else if (qr_code_hash) {
    attendee = Array.from(attendees.values()).find((a) => a.qr_code_hash === qr_code_hash);
  }

  if (!attendee) {
    return res.status(404).json({ detail: 'Attendee not found. Please verify attendee name or scan QR pass.' });
  }

  // Security enforcement: Reject recharge for terminated passes
  if (attendee.status === 'terminated') {
    return res.status(403).json({
      detail: `SECURITY ALERT: Recharge Rejected! QR Pass is REVOKED/TERMINATED (${attendee.termination_reason || 'Administrative Hold'}). Please reinstate pass first.`,
      terminated: true,
      attendee: {
        id: attendee._id,
        name: attendee.name,
        role: attendee.role,
        status: attendee.status,
        terminated_at: attendee.terminated_at,
        termination_reason: attendee.termination_reason,
      },
    });
  }

  // 1 Rupee = 1 Fest Coin
  const coinsAdded = rupeeAmount;
  attendee.fest_coins = (attendee.fest_coins || 0) + coinsAdded;

  const paymentRef = `TXN-INR-${Date.now().toString(36).toUpperCase()}-${Math.floor(1000 + Math.random() * 9000)}`;

  const recharge = {
    _id: generateId(),
    payment_ref: paymentRef,
    attendee_id: attendee._id,
    attendee_name: attendee.name,
    attendee_role: attendee.role,
    qr_code_hash: attendee.qr_code_hash,
    inr_amount: rupeeAmount,
    coins_added: coinsAdded,
    conversion_ratio: '1 INR = 1 Coin',
    payment_method: String(payment_method),
    operator_mode: operator_mode === 'staff_desk' ? 'staff_desk' : 'self_service',
    operator_name: operator_name || (operator_mode === 'staff_desk' ? 'Desk Cashier' : 'Self-Service Portal'),
    notes: notes || '',
    timestamp: new Date(),
  };

  recharges.unshift(recharge);

  res.status(201).json({
    message: `Payment of ₹${rupeeAmount.toLocaleString()} successful! Added ${coinsAdded.toLocaleString()} Fest Coins (1:1 conversion).`,
    recharge: {
      ...recharge,
      timestamp: recharge.timestamp.toISOString(),
    },
    attendee: {
      _id: attendee._id,
      name: attendee.name,
      role: attendee.role,
      ticket_type: attendee.ticket_type,
      fest_coins: attendee.fest_coins,
      status: attendee.status,
      qr_code_hash: attendee.qr_code_hash,
    },
  });
});

// List recharges with financial summary
app.get('/api/recharges', (req, res) => {
  let totalInr = 0;
  let totalCoins = 0;
  const methodCounts = {};
  const modeCounts = { self_service: 0, staff_desk: 0 };

  for (const r of recharges) {
    totalInr += r.inr_amount || 0;
    totalCoins += r.coins_added || 0;
    methodCounts[r.payment_method] = (methodCounts[r.payment_method] || 0) + 1;
    if (r.operator_mode in modeCounts) {
      modeCounts[r.operator_mode]++;
    }
  }

  res.json({
    recharges: recharges.slice(0, 100).map((r) => ({
      ...r,
      timestamp: new Date(r.timestamp).toISOString(),
    })),
    summary: {
      total_inr_collected: totalInr,
      total_coins_issued: totalCoins,
      conversion_ratio: '1 Rupee = 1 Fest Coin',
      total_transactions: recharges.length,
      method_counts: methodCounts,
      mode_counts: modeCounts,
    },
  });
});

// Get attendee full profile & unified history (pass status, recharges, spending, scans)
app.get('/api/attendee/:attendee_id/history', (req, res) => {
  const { attendee_id } = req.params;
  const attendee = attendees.get(attendee_id);
  if (!attendee) {
    return res.status(404).json({ detail: 'Attendee not found' });
  }

  const attendeeRecharges = recharges
    .filter((r) => r.attendee_id === attendee_id)
    .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
    .map((r) => ({ ...r, timestamp: new Date(r.timestamp).toISOString() }));

  const attendeeTransactions = transactions
    .filter((t) => t.attendee_id === attendee_id)
    .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
    .map((t) => ({ ...t, timestamp: new Date(t.timestamp).toISOString() }));

  const attendeeScans = zoneLogs
    .filter((l) => l.attendee_id === attendee_id)
    .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
    .map((l) => ({ ...l, timestamp: new Date(l.timestamp).toISOString() }));

  const totalRechargedInr = attendeeRecharges.reduce((sum, r) => sum + (r.inr_amount || 0), 0);
  const totalSpentCoins = attendeeTransactions.reduce((sum, t) => sum + (t.amount || 0), 0);

  res.json({
    attendee,
    recharges: attendeeRecharges,
    transactions: attendeeTransactions,
    scans: attendeeScans,
    summary: {
      current_balance_coins: attendee.fest_coins || 0,
      current_balance_inr: attendee.fest_coins || 0,
      total_recharged_inr: totalRechargedInr,
      total_spent_coins: totalSpentCoins,
      total_scans: attendeeScans.length,
    },
  });
});

// Search attendees (public for self-service & desk top-ups)
app.get('/api/attendees/search', (req, res) => {
  const q = String(req.query.q || '').trim().toLowerCase();
  const list = Array.from(attendees.values());
  if (!q) {
    return res.json({ attendees: list.slice(0, 30) });
  }

  const results = list.filter((a) => {
    return (
      a.name.toLowerCase().includes(q) ||
      (a.role && a.role.toLowerCase().includes(q)) ||
      (a.ticket_type && a.ticket_type.toLowerCase().includes(q)) ||
      (a.qr_code_hash && a.qr_code_hash.toLowerCase().includes(q)) ||
      (a._id && a._id.toLowerCase().includes(q))
    );
  }).slice(0, 50);

  res.json({ attendees: results });
});

// ─────────────────────── FEATURE 2: Crowd Crush Prevention (Active Diversion) ───────────────────────

// Broadcast active diversion offer/message
app.post('/api/admin/trigger_diversion', verifyAdmin, (req, res) => {
  const { zone_name, message, target_zone } = req.body;

  if (!zone_name || !ZONES.includes(zone_name)) {
    return res.status(400).json({ detail: 'Invalid zone name for diversion' });
  }

  const diversion = {
    _id: generateId(),
    zone_name,
    target_zone: target_zone && ZONES.includes(target_zone) ? target_zone : 'Food Court',
    message: message || `Active Crowd Diversion: Special 50% discount at ${target_zone || 'Food Court'}!`,
    created_at: new Date(),
    active: true,
  };

  // Mark previous diversions for this zone inactive
  diversions.forEach((d) => {
    if (d.zone_name === zone_name) d.active = false;
  });

  diversions.unshift(diversion);

  // Immediately relieve 65% of the recent scans in the overloaded zone
  const tenMinsAgo = Date.now() - 10 * 60 * 1000;
  const targetReliefCount = Math.floor(BOTTLENECK_THRESHOLD * 0.65);
  let relieved = 0;

  for (let i = zoneLogs.length - 1; i >= 0 && relieved < targetReliefCount; i--) {
    const l = zoneLogs[i];
    if (l.zone_name === zone_name && new Date(l.timestamp).getTime() >= tenMinsAgo) {
      l.zone_name = diversion.target_zone;
      relieved++;
    }
  }

  res.status(201).json({
    message: `Diversion activated for ${zone_name}! ${relieved} attendees re-routed to ${diversion.target_zone}.`,
    diversion: {
      ...diversion,
      created_at: diversion.created_at.toISOString(),
    },
  });
});

// Clear an active diversion
app.post('/api/admin/clear_diversion/:id', verifyAdmin, (req, res) => {
  const { id } = req.params;
  const d = diversions.find((div) => div._id === id);
  if (d) {
    d.active = false;
    return res.json({ message: 'Diversion marked inactive' });
  }
  res.status(404).json({ detail: 'Diversion not found' });
});

// Throttle and divert zone crowd scans
app.post('/api/admin/throttle_zone', verifyAdmin, (req, res) => {
  const { zone_name, target_zone, count } = req.body || {};
  if (!zone_name || !ZONES.includes(zone_name)) {
    return res.status(400).json({ detail: 'Invalid zone' });
  }

  const otherZones = ZONES.filter((z) => z !== zone_name);
  const target = target_zone && ZONES.includes(target_zone) ? target_zone : otherZones[Math.floor(Math.random() * otherZones.length)];
  const limit = count || 150;

  const tenMinsAgo = Date.now() - 10 * 60 * 1000;
  let relieved = 0;

  for (let i = zoneLogs.length - 1; i >= 0 && relieved < limit; i--) {
    const l = zoneLogs[i];
    if (l.zone_name === zone_name && new Date(l.timestamp).getTime() >= tenMinsAgo) {
      l.zone_name = target;
      relieved++;
    }
  }

  if (relieved === 0) {
    return res.json({ message: `No active attendees found in ${zone_name}.` });
  }
  return res.json({ message: `SUCCESS! Diverted ${relieved} attendees from ${zone_name} to ${target}.` });
});

// ─────────────────────── FEATURE 3: SOS & Incident Triage (God's Eye) ───────────────────────

// Get all recent incidents
app.get('/api/incidents', (req, res) => {
  const formatted = incidents.slice(0, 50).map(inc => ({
    ...inc,
    status: (String(inc.status).toLowerCase() === 'resolved') ? 'Resolved' : 'Under Processing',
    reported_at: inc.reported_at ? new Date(inc.reported_at).toISOString() : new Date().toISOString(),
    resolved_at: inc.resolved_at ? new Date(inc.resolved_at).toISOString() : null,
  }));
  res.json({ incidents: formatted });
});

// Report emergency incident from scanner
app.post('/api/incident', (req, res) => {
  const { type, zone_name, notes, coordinator_name } = req.body;
  const validTypes = ['Medical', 'Security', 'Logistics'];

  if (!type || !validTypes.includes(type)) {
    return res.status(400).json({ detail: 'Invalid incident type. Must be Medical, Security, or Logistics.' });
  }

  const incident = {
    _id: generateId(),
    type,
    zone_name: zone_name && ZONES.includes(zone_name) ? zone_name : 'Main Stage',
    notes: notes || `Urgent ${type} assistance requested at ${zone_name || 'Main Stage'}.`,
    status: 'Under Processing',
    coordinator_name: coordinator_name || 'Field Coordinator',
    reported_at: new Date(),
    resolved_at: null,
    resolved_by: null,
    resolution_notes: null,
  };

  incidents.unshift(incident);

  res.status(201).json({
    message: `🚨 Emergency ${type} incident logged at ${incident.zone_name}!`,
    incident: {
      ...incident,
      reported_at: incident.reported_at.toISOString(),
    },
  });
});

// Update incident status
app.all(['/api/incident/:id/status'], verifyFieldOrAdmin, (req, res) => {
  const { id } = req.params;
  const { status, coordinator_name, resolution_notes } = req.body || {};
  const inc = incidents.find((i) => i._id === id);

  if (!inc) {
    return res.status(404).json({ detail: 'Incident not found' });
  }

  const isResolved = String(status).toLowerCase() === 'resolved';
  inc.status = isResolved ? 'Resolved' : 'Under Processing';
  if (isResolved) {
    inc.resolved_at = new Date();
    inc.resolved_by = coordinator_name || inc.coordinator_name || 'Coordinator';
    if (resolution_notes) inc.resolution_notes = resolution_notes;
  } else {
    inc.resolved_at = null;
    inc.resolved_by = null;
  }

  res.json({
    message: `Incident marked as ${inc.status}`,
    incident: {
      ...inc,
      reported_at: inc.reported_at.toISOString(),
      resolved_at: inc.resolved_at ? inc.resolved_at.toISOString() : null,
    },
  });
});

// Resolve an incident from Admin Dashboard or Field Terminal
app.all(['/api/incident/:id/resolve'], verifyFieldOrAdmin, (req, res) => {
  const { id } = req.params;
  const { coordinator_name, resolution_notes } = req.body || {};
  const inc = incidents.find((i) => i._id === id);

  if (!inc) {
    return res.status(404).json({ detail: 'Incident not found' });
  }

  inc.status = 'Resolved';
  inc.resolved_at = new Date();
  inc.resolved_by = coordinator_name || inc.coordinator_name || 'Coordinator';
  if (resolution_notes) inc.resolution_notes = resolution_notes;

  res.json({
    message: `${inc.type} incident resolved successfully by ${inc.resolved_by}`,
    incident: {
      ...inc,
      reported_at: inc.reported_at.toISOString(),
      resolved_at: inc.resolved_at.toISOString(),
    },
  });
});

// ─────────────────────── Admin Protected Endpoints ───────────────────────

// Live aggregated dashboard stats (including Fest Coin financials, active diversions, and active incidents)
app.get('/api/admin/stats', verifyAdmin, (req, res) => {
  const totalAttendees = attendees.size;
  let totalEngagement = 0;
  for (const a of attendees.values()) {
    totalEngagement += a.engagement_score || 0;
  }

  const tenMinsAgo = Date.now() - 10 * 60 * 1000;
  const recentCounts = {};
  const totalCounts = {};

  for (const z of ZONES) {
    recentCounts[z] = 0;
    totalCounts[z] = 0;
  }

  for (const log of zoneLogs) {
    const z = log.zone_name;
    if (totalCounts[z] !== undefined) {
      totalCounts[z]++;
    }
    const logTime = new Date(log.timestamp).getTime();
    if (logTime >= tenMinsAgo) {
      if (recentCounts[z] !== undefined) {
        recentCounts[z]++;
      }
    }
  }

  const activeDiversionMap = {};
  diversions.filter((d) => d.active).forEach((d) => {
    activeDiversionMap[d.zone_name] = d;
  });

  const zonesStats = ZONES.map((zone) => {
    const recent = recentCounts[zone] || 0;
    const total = totalCounts[zone] || 0;
    const hasActiveDiversion = Boolean(activeDiversionMap[zone]);
    return {
      zone_name: zone,
      total_scans: total,
      recent_scans: recent,
      capacity_warning: recent > BOTTLENECK_THRESHOLD,
      active_diversion: hasActiveDiversion ? activeDiversionMap[zone] : null,
    };
  });

  // Latest 10 logs (shortened by 10 names)
  const sortedLogs = [...zoneLogs].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
  const recent10 = sortedLogs.slice(0, 10).map((log) => {
    const att = attendees.get(log.attendee_id);
    return {
      id: log._id,
      zone_name: log.zone_name,
      timestamp: new Date(log.timestamp).toISOString(),
      attendee_name: att ? att.name : 'Unknown',
      role: log.role,
    };
  });

  // Financial calculations
  let totalRevenue = 0;
  for (const tx of transactions) {
    totalRevenue += tx.amount || 0;
  }

  // Active and recent incidents with coordinator status
  const formattedIncidents = incidents
    .slice(0, 50)
    .sort((a, b) => new Date(b.reported_at) - new Date(a.reported_at))
    .map((i) => {
      const isResolved = String(i.status).toLowerCase() === 'resolved';
      return {
        id: i._id,
        type: i.type,
        zone_name: i.zone_name,
        notes: i.notes,
        status: isResolved ? 'Resolved' : 'Under Processing',
        coordinator_name: i.coordinator_name || 'Field Coordinator',
        reported_at: new Date(i.reported_at).toISOString(),
        resolved_at: i.resolved_at ? new Date(i.resolved_at).toISOString() : null,
        resolved_by: i.resolved_by || null,
        resolution_notes: i.resolution_notes || null,
      };
    });

  const underProcessingCount = formattedIncidents.filter((i) => i.status === 'Under Processing').length;
  const resolvedCount = formattedIncidents.filter((i) => i.status === 'Resolved').length;

  const activeDiversionsList = diversions
    .filter((d) => d.active)
    .map((d) => ({
      id: d._id,
      zone_name: d.zone_name,
      target_zone: d.target_zone,
      message: d.message,
      created_at: new Date(d.created_at).toISOString(),
    }));

  res.json({
    zones: zonesStats,
    recent_logs: recent10,
    total_attendees: totalAttendees,
    total_engagement: totalEngagement,
    financials: {
      total_revenue: totalRevenue,
      transaction_volume: transactions.length,
      total_inr_recharged: recharges.reduce((s, r) => s + (r.inr_amount || 0), 0),
      recharge_volume: recharges.length,
      conversion_ratio: '1 INR = 1 Coin',
      recent_transactions: transactions.slice(0, 10).map((tx) => ({
        id: tx._id,
        attendee_name: tx.attendee_name,
        amount: tx.amount,
        stall_name: tx.stall_name,
        zone_name: tx.zone_name,
        timestamp: new Date(tx.timestamp).toISOString(),
      })),
      recent_recharges: recharges.slice(0, 10).map((r) => ({
        id: r._id,
        payment_ref: r.payment_ref,
        attendee_name: r.attendee_name,
        inr_amount: r.inr_amount,
        coins_added: r.coins_added,
        payment_method: r.payment_method,
        operator_mode: r.operator_mode,
        timestamp: new Date(r.timestamp).toISOString(),
      })),
    },
    incidents: formattedIncidents,
    active_incidents: formattedIncidents,
    incidents_summary: {
      under_processing: underProcessingCount,
      resolved: resolvedCount,
      total: formattedIncidents.length,
    },
    active_diversions: activeDiversionsList,
  });
});

// Throttle / divert zone crowd flow
app.post('/api/admin/throttle_zone', verifyAdmin, (req, res) => {
  const { zone_name, target_zone, count = 150 } = req.body;
  if (!ZONES.includes(zone_name)) {
    return res.status(400).json({ detail: 'Invalid zone' });
  }

  const tenMinsAgo = Date.now() - 10 * 60 * 1000;
  const logsInZone = zoneLogs
    .filter((l) => l.zone_name === zone_name && new Date(l.timestamp).getTime() >= tenMinsAgo)
    .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
    .slice(0, count);

  if (logsInZone.length === 0) {
    return res.json({ message: `No active attendees found in ${zone_name}.` });
  }

  const otherZones = ZONES.filter((z) => z !== zone_name);
  let divertedCount = 0;

  for (const log of logsInZone) {
    const destination = (target_zone && ZONES.includes(target_zone) && target_zone !== zone_name)
      ? target_zone
      : otherZones[Math.floor(Math.random() * otherZones.length)];
    log.zone_name = destination;
    divertedCount++;
  }

  const destStr = target_zone && ZONES.includes(target_zone) ? target_zone : 'other zones';
  res.json({
    message: `SUCCESS! Diverted ${divertedCount} attendees from ${zone_name} to ${destStr}.`,
  });
});

// Simulate bottleneck surge
app.post('/api/admin/simulate_surge', verifyAdmin, (req, res) => {
  const attList = Array.from(attendees.values()).slice(0, 200);
  if (attList.length === 0) {
    return res.json({ message: 'No attendees found to simulate a surge.' });
  }

  const now = new Date();
  for (const att of attList) {
    att.engagement_score += SCAN_ENGAGEMENT_POINTS;
    zoneLogs.push({
      _id: generateId(),
      attendee_id: att._id,
      qr_code_hash: att.qr_code_hash,
      role: att.role,
      zone_name: 'Cultural Pavilion',
      timestamp: now,
      seeded: true,
      simulated: true,
    });
  }

  res.json({ message: 'Surge simulated! 200 attendees rushed the Cultural Pavilion.' });
});

// Revert surge simulation
app.post('/api/admin/revert_simulation', verifyAdmin, (req, res) => {
  let count = 0;
  for (let i = zoneLogs.length - 1; i >= 0; i--) {
    if (zoneLogs[i].simulated) {
      zoneLogs.splice(i, 1);
      count++;
    }
  }
  res.json({ message: `Simulation stopped. Reverted ${count} simulated scans.` });
});

// Delete attendee
app.delete('/api/admin/delete_attendee/:attendee_id', verifyAdmin, (req, res) => {
  const { attendee_id } = req.params;
  if (!attendees.has(attendee_id)) {
    return res.status(404).json({ detail: 'Attendee not found' });
  }
  attendees.delete(attendee_id);
  // Clean up logs and transactions
  for (let i = zoneLogs.length - 1; i >= 0; i--) {
    if (zoneLogs[i].attendee_id === attendee_id) {
      zoneLogs.splice(i, 1);
    }
  }
  for (let i = transactions.length - 1; i >= 0; i--) {
    if (transactions[i].attendee_id === attendee_id) {
      transactions.splice(i, 1);
    }
  }
  res.json({ message: 'Attendee deleted permanently.' });
});

// Terminate attendee QR pass
const handleTerminateQr = (req, res) => {
  const { attendee_id } = req.params;
  const { reason } = req.body || {};
  const attendee = attendees.get(attendee_id);
  if (!attendee) {
    return res.status(404).json({ detail: 'Attendee not found' });
  }

  attendee.status = 'terminated';
  attendee.terminated_at = new Date().toISOString();
  attendee.termination_reason = (reason && String(reason).trim()) || 'Administrative revocation';

  res.json({
    message: `QR Pass for ${attendee.name} has been terminated.`,
    attendee,
  });
};

app.post('/api/admin/attendee/:attendee_id/terminate_qr', verifyAdmin, handleTerminateQr);
app.post('/attendee/:attendee_id/terminate_qr', handleTerminateQr);

// Reinstate or regenerate attendee QR pass
const handleReinstateQr = (req, res) => {
  const { attendee_id } = req.params;
  const { regenerate } = req.body || {};
  const attendee = attendees.get(attendee_id);
  if (!attendee) {
    return res.status(404).json({ detail: 'Attendee not found' });
  }

  attendee.status = 'active';
  attendee.terminated_at = null;
  attendee.termination_reason = null;

  if (regenerate) {
    attendee.qr_code_hash = crypto.randomBytes(16).toString('hex');
  }

  res.json({
    message: regenerate
      ? `New QR Pass generated and activated for ${attendee.name}.`
      : `QR Pass for ${attendee.name} has been reinstated.`,
    attendee,
  });
};

app.post('/api/admin/attendee/:attendee_id/reinstate_qr', verifyAdmin, handleReinstateQr);
app.post('/attendee/:attendee_id/reinstate_qr', handleReinstateQr);

// List all attendees
app.get('/api/admin/attendees', verifyAdmin, (req, res) => {
  const list = Array.from(attendees.values()).sort(
    (a, b) => new Date(b.registered_at) - new Date(a.registered_at)
  );
  res.json({ attendees: list });
});

// NLP Command Query & Intelligence Assistant
app.post('/api/admin/nlp_query', verifyAdmin, async (req, res) => {
  const originalQuery = (req.body.query || '').trim();
  const query = originalQuery.toLowerCase();

  // Extract roles & zones
  const matchedRoles = [];
  for (const r of ROLES) {
    const rx = new RegExp(`\\b${r.toLowerCase()}s?\\b`, 'i');
    if (rx.test(query)) matchedRoles.push(r);
  }

  const matchedZones = [];
  for (const z of ZONES) {
    if (query.includes(z.toLowerCase())) matchedZones.push(z);
  }

  // Live festival snapshot
  const zoneCounts = {};
  const zoneRoleCounts = {};
  for (const z of ZONES) {
    zoneCounts[z] = 0;
    zoneRoleCounts[z] = {};
  }
  for (const log of zoneLogs) {
    zoneCounts[log.zone_name] = (zoneCounts[log.zone_name] || 0) + 1;
    if (!zoneRoleCounts[log.zone_name]) zoneRoleCounts[log.zone_name] = {};
    zoneRoleCounts[log.zone_name][log.role] = (zoneRoleCounts[log.zone_name][log.role] || 0) + 1;
  }

  const bottleneckZones = ZONES.filter(z => (zoneCounts[z] || 0) >= BOTTLENECK_THRESHOLD);
  const totalAttendees = attendees.size;
  let activeCount = 0;
  let terminatedCount = 0;
  let totalCoins = 0;
  const roleCounts = {};
  for (const att of attendees.values()) {
    if (att.status === 'active') activeCount++;
    else terminatedCount++;
    totalCoins += att.fest_coins || 0;
    roleCounts[att.role] = (roleCounts[att.role] || 0) + 1;
  }

  let totalInrCollected = 0;
  let totalRechargeCoins = 0;
  for (const r of recharges) {
    totalInrCollected += r.inr_amount || 0;
    totalRechargeCoins += r.fest_coins || 0;
  }

  const pendingIncidents = incidents.filter(i => i.status === 'open' || i.status === 'pending');
  let category = 'general';
  let answer = '';
  let details = [];
  let metrics = [];
  let quick_actions = [];
  let resultCount = 0;

  // 1. Crowd / Bottleneck / Congestion
  const isCrowdQuery = /crowd|bottleneck|congest|traffic|capacity|dens|packed|fullest|busy|divert/i.test(query);
  const isCoinQuery = /coin|recharge|inr|rupee|wallet|cash|money|top-up|balance|revenue|minted/i.test(query);
  const isPassQuery = /pass|attendee|guest|ticket|revok|terminat|status|register|vip/i.test(query) && !isCrowdQuery;
  const isIncidentQuery = /incident|sos|alert|emergency|medical|security|logistics|hazard/i.test(query);

  if (isCrowdQuery) {
    category = 'crowd';
    if (bottleneckZones.length > 0) {
      const bList = bottleneckZones.map(z => `${z} (${zoneCounts[z]} scans)`).join(', ');
      answer = `⚠️ Alert: ${bottleneckZones.length} festival zone(s) currently at or above bottleneck threshold (≥150 scans): ${bList}. Crowd diversion is recommended.`;
    } else {
      const topZone = Object.entries(zoneCounts).sort((a,b) => b[1] - a[1])[0];
      answer = `Flow is steady across all festival zones. No critical bottlenecks detected. The highest density is currently at ${topZone ? topZone[0] : 'Main Stage'} with ${topZone ? topZone[1] : 0} recorded scans.`;
    }

    metrics = ZONES.map(z => ({
      label: z,
      value: `${zoneCounts[z] || 0} scans`,
      alert: (zoneCounts[z] || 0) >= BOTTLENECK_THRESHOLD
    }));

    quick_actions = [
      { label: 'View Live Telemetry', url: '/' },
      { label: 'Trigger Crowd Diversion', action: 'diversion' }
    ];
    resultCount = bottleneckZones.length;
  }
  // 2. Fest Coins / Cashless Economy
  else if (isCoinQuery) {
    category = 'financial';
    answer = `Cashless Economy Status: Total Fest Coins in attendee circulation: 🪙 ${totalCoins.toLocaleString()}. Total INR collected via top-ups: ₹${totalInrCollected.toLocaleString()} across ${recharges.length} transactions (Standard rate: 1 INR = 1 Fest Coin).`;
    metrics = [
      { label: 'Coins in Circulation', value: `🪙 ${totalCoins.toLocaleString()}` },
      { label: 'Total INR Collected', value: `₹${totalInrCollected.toLocaleString()}` },
      { label: 'Top-up Transactions', value: `${recharges.length}` },
      { label: 'Rate', value: '1 ₹ = 1 🪙' }
    ];
    quick_actions = [
      { label: 'Open Coin Top-up Hub', url: '/recharge.html' },
      { label: 'View Guest Balances', url: '/attendees.html' }
    ];
    resultCount = totalCoins;
  }
  // 3. Security / Incidents
  else if (isIncidentQuery) {
    category = 'security';
    if (pendingIncidents.length > 0) {
      answer = `Active Security Alerts: ${pendingIncidents.length} pending incident(s) require dispatcher triage. Latest incident in ${pendingIncidents[0].zone_name || 'Festival Grounds'}.`;
    } else {
      answer = `All clear. Zero unresolved SOS incidents. Emergency responders and security dispatchers are standing by across all 5 zones.`;
    }
    metrics = [
      { label: 'Active Incidents', value: `${pendingIncidents.length}`, alert: pendingIncidents.length > 0 },
      { label: 'Total Logged', value: `${incidents.length}` },
      { label: 'Diversions Active', value: `${diversions.length}` }
    ];
    quick_actions = [
      { label: 'Open Incident Radar', url: '/' },
      { label: 'Scanner Station SOS', url: '/scanner.html' }
    ];
    resultCount = pendingIncidents.length;
  }
  // 4. Pass / Attendee Status
  else if (isPassQuery && matchedZones.length === 0) {
    category = 'attendees';
    const vipCount = roleCounts['VIP'] || 0;
    answer = `Attendee Directory: ${totalAttendees} total registered attendees. ${activeCount} passes active, ${terminatedCount} passes revoked/terminated. VIP passes: ${vipCount}.`;
    metrics = [
      { label: 'Total Registered', value: `${totalAttendees}` },
      { label: 'Active Passes', value: `${activeCount}` },
      { label: 'Revoked Passes', value: `${terminatedCount}` },
      { label: 'VIP Passholders', value: `${vipCount}` }
    ];
    quick_actions = [
      { label: 'Open Guest Directory', url: '/attendees.html' },
      { label: 'Field Scanner Hub', url: '/scanner.html' }
    ];
    resultCount = totalAttendees;
  }
  // 5. Zone & Role Specific Scans (Original core function + enhancements)
  else {
    const isUnique = /unique|attendee|person|people/i.test(query);
    let target = 'scans';

    if (isUnique) {
      target = 'unique attendees';
      const uniqueIds = new Set();
      for (const log of zoneLogs) {
        if (matchedRoles.length > 0 && !matchedRoles.includes(log.role)) continue;
        if (matchedZones.length > 0 && !matchedZones.includes(log.zone_name)) continue;
        uniqueIds.add(log.attendee_id);
      }
      resultCount = uniqueIds.size;
    } else {
      for (const log of zoneLogs) {
        if (matchedRoles.length > 0 && !matchedRoles.includes(log.role)) continue;
        if (matchedZones.length > 0 && !matchedZones.includes(log.zone_name)) continue;
        resultCount++;
      }
    }

    const roleStr = matchedRoles.length > 0 ? matchedRoles.join(' or ') : 'All roles';
    const zoneStr = matchedZones.length > 0 ? matchedZones.join(' or ') : 'All zones';
    answer = `Found ${resultCount.toLocaleString()} ${target} matching (${roleStr} in ${zoneStr}).`;

    metrics = [
      { label: 'Target', value: target },
      { label: 'Count', value: resultCount.toLocaleString() },
      { label: 'Filter Roles', value: roleStr },
      { label: 'Filter Zones', value: zoneStr }
    ];
    quick_actions = [
      { label: 'View Insights', url: '/analytics.html' },
      { label: 'Guest Directory', url: '/attendees.html' }
    ];
  }

  // Optional: Gemini API enhancement if GEMINI_API_KEY is configured
  if (process.env.GEMINI_API_KEY && process.env.GEMINI_API_KEY.length > 10) {
    try {
      const ai = new GoogleGenAI({});
      const prompt = `You are Cultura Festival AI Assistant. A festival admin asked: "${originalQuery}".
Telemetry summary:
- Zones: ${JSON.stringify(zoneCounts)}
- Bottlenecks: ${JSON.stringify(bottleneckZones)}
- Attendees: ${totalAttendees} total (${activeCount} active, ${terminatedCount} revoked, ${totalCoins} Fest Coins in circulation)
- Cashless Revenue: ₹${totalInrCollected} collected (1 INR = 1 Coin)
- Security Incidents: ${pendingIncidents.length} open
Statistical baseline: "${answer}"

Provide a concise, direct, helpful answer in 1 to 2 clear sentences. Mention exact relevant numbers. Do not include markdown headers or bullet dumps.`;

      const aiResponse = await ai.models.generateContent({
        model: 'gemini-2.5-flash',
        contents: prompt,
      });

      if (aiResponse && aiResponse.text) {
        answer = aiResponse.text.trim();
      }
    } catch (genAiErr) {
      console.warn('Gemini NLP enhancement fallback to rule engine:', genAiErr.message);
    }
  }

  res.json({
    query: originalQuery,
    response: answer,
    answer: answer,
    category: category,
    metrics: metrics,
    quick_actions: quick_actions,
    result_count: resultCount,
  });
});

// Database config mock
app.post('/api/admin/config', verifyAdmin, (req, res) => {
  res.json({ message: 'Connected to MongoDB successfully.' });
});

// ─────────────────────── Static Files Serving ───────────────────────
import fs from 'fs';
let staticPath = path.join(__dirname, '..', 'frontend');
if (!fs.existsSync(staticPath)) {
  staticPath = path.join(__dirname, 'static');
}
app.use(express.static(staticPath));
app.use('/static', express.static(staticPath));


// Direct file matching & admin aliases
app.get('/admin.html', (req, res) => res.sendFile(path.join(staticPath, 'index.html')));
app.get('/scanner.html', (req, res) => res.sendFile(path.join(staticPath, 'scanner.html')));
app.get('/analytics.html', (req, res) => res.sendFile(path.join(staticPath, 'analytics.html')));
app.get('/attendees.html', (req, res) => res.sendFile(path.join(staticPath, 'attendees.html')));
app.get('/recharge.html', (req, res) => res.sendFile(path.join(staticPath, 'recharge.html')));
app.get('/payment.html', (req, res) => res.sendFile(path.join(staticPath, 'recharge.html')));
app.get('/settings.html', (req, res) => res.sendFile(path.join(staticPath, 'settings.html')));
app.get('/', (req, res) => res.sendFile(path.join(staticPath, 'index.html')));

// Catch-all route to index.html for SPA/Static experience
app.get('*', (req, res) => {
  const filePath = path.join(staticPath, req.path);
  res.sendFile(filePath, (err) => {
    if (err) {
      res.sendFile(path.join(staticPath, 'index.html'));
    }
  });
});

// ─────────────────────── Dynamic 65% Crowd Diversion Simulator ───────────────────────
setInterval(() => {
  const attList = Array.from(attendees.values());
  if (attList.length > 0) {
    const numScans = Math.floor(Math.random() * 2) + 1;
    for (let i = 0; i < numScans; i++) {
      const att = attList[Math.floor(Math.random() * attList.length)];
      let zone = ZONES[Math.floor(Math.random() * ZONES.length)];

      // Check if this zone has an active diversion
      const activeDiv = diversions.find((d) => d.active && d.zone_name === zone);
      if (activeDiv) {
        // 65% chance of being diverted away from the overloaded zone
        if (Math.random() < 0.65) {
          zone = activeDiv.target_zone || 'Food Court';
        }
      }

      att.engagement_score += SCAN_ENGAGEMENT_POINTS;
      zoneLogs.push({
        _id: generateId(),
        attendee_id: att._id,
        qr_code_hash: att.qr_code_hash,
        role: att.role,
        zone_name: zone,
        timestamp: new Date(),
      });
    }
  }
}, 4000);

// Start server
app.listen(PORT, HOST, () => {
  console.log(`[Cultura Festival Hub] Dev server listening on http://${HOST}:${PORT}`);
});
