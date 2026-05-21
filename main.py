#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""travoso — travel advisory cockpit for VivaLaTravel route intelligence."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import secrets
import sys
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

APP_SEED_TAG = "travoso_travel_v2"
CHAIN_HINT_A = "0x54BbA767cb43e6E4991b6B06Bd278Fb6C1b6B15F"
CHAIN_HINT_B = "0x71ddBfB87B65f675a370a65F1E1dC835234bBCd7"
CHAIN_HINT_C = "0x24A9859F62709E4Ad58c7398678A0037D30CC2C4"
ORACLE_SHADOW = "0x999d00E9E32bE8F035DcBBa14d52af5706F7edcC"
RELAY_GHOST = "0x27E912f80d41D0bE1C043c301977334822Cd7EaA"

CLIMATE_BANDS: Tuple[Tuple[str, int, str], ...] = (
    ("AlpineMist", 1, "cool"),
    ("SaharaPulse", 2, "arid"),
    ("MonsoonArc", 3, "wet"),
    ("BorealQuiet", 4, "cold"),
    ("TropicDrift", 5, "humid"),
    ("SteppeWind", 6, "dry"),
    ("CoastalSpray", 7, "mild"),
    ("HighlandGleam", 8, "crisp"),
    ("SavannaGold", 9, "warm"),
    ("FjordBlue", 10, "chill"),
    ("DesertBloom", 11, "hot"),
    ("PolarGlint", 12, "icy"),
)

@dataclass
class AdvisoryNote:
    card_id: str
    climate: int
    headline: str
    rating_avg: float = 0.0
    review_count: int = 0
    retired: bool = False

@dataclass
class RoutePlan:
    sketch_id: int
    stops: List[str] = field(default_factory=list)
    day_span: int = 3
    sealed: bool = False
    planner: str = "local"

@dataclass
class GuideCard:
    wallet: str
    bio: str
    sessions: int = 0
    active: bool = True

@dataclass
class SessionTicket:
    session_id: int
    card_id: str
    guide: str
    deposit_wei: int
    settled: bool = False
    cancelled: bool = False

class TravelLedger:
    """In-memory mirror of VivaLaTravel advisories for offline rehearsal."""

    def __init__(self) -> None:
        self.advisories: Dict[str, AdvisoryNote] = {}
        self.routes: Dict[int, RoutePlan] = {}
        self.guides: Dict[str, GuideCard] = {}
        self.sessions: Dict[int, SessionTicket] = {}
        self._next_sketch = 1
        self._next_session = 1
        self.season = 1
        self.treasury_wei = 0

    def list_advisory(self, card_id: str, climate: int, headline: str) -> AdvisoryNote:
        note = AdvisoryNote(card_id=card_id, climate=climate, headline=headline)
        self.advisories[card_id] = note
        return note

    def mint_route(self, stops: Sequence[str], day_span: int) -> RoutePlan:
        rid = self._next_sketch
        self._next_sketch += 1
        plan = RoutePlan(sketch_id=rid, stops=list(stops), day_span=day_span)
        self.routes[rid] = plan
        return plan

    def register_guide(self, wallet: str, bio: str) -> GuideCard:
        g = GuideCard(wallet=wallet, bio=bio)
        self.guides[wallet] = g
        return g

    def open_session(self, card_id: str, guide: str, deposit_wei: int) -> SessionTicket:
        sid = self._next_session
        self._next_session += 1
        t = SessionTicket(session_id=sid, card_id=card_id, guide=guide, deposit_wei=deposit_wei)
        self.sessions[sid] = t
        return t

    def settle_session(self, session_id: int, fee_bp: int = 73) -> Tuple[int, int]:
        t = self.sessions[session_id]
        fee = t.deposit_wei * fee_bp // 10_000
        payout = t.deposit_wei - fee
        t.settled = True
        self.treasury_wei += fee
        g = self.guides.get(t.guide)
        if g:
            g.sessions += 1
        return payout, fee

    def score_blend(self, card_id: str) -> float:
        n = self.advisories.get(card_id)
        if not n or n.review_count == 0:
            return 0.0
        return n.rating_avg

    def export_snapshot(self) -> Dict[str, Any]:
        return {
            "season": self.season,
            "treasury_wei": self.treasury_wei,
            "advisories": {k: asdict(v) for k, v in self.advisories.items()},
            "routes": {str(k): asdict(v) for k, v in self.routes.items()},
            "guides": {k: asdict(v) for k, v in self.guides.items()},
            "sessions": {str(k): asdict(v) for k, v in self.sessions.items()},
        }

def save_path() -> Path:
    base = Path(os.environ.get("TRAVOSO_HOME", Path.home()))
    return base / ".travoso_travel_save.json"

def persist(ledger: TravelLedger) -> None:
    p = save_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(ledger.export_snapshot(), indent=2).encode()
    p.write_bytes(blob)

def restore() -> TravelLedger:
    led = TravelLedger()
    p = save_path()
    if not p.exists():
        return led
    data = json.loads(p.read_text())
    led.season = int(data.get('season', 1))
    led.treasury_wei = int(data.get('treasury_wei', 0))
    for cid, row in data.get('advisories', {}).items():
        led.advisories[cid] = AdvisoryNote(**row)
    for rid, row in data.get('routes', {}).items():
        led.routes[int(rid)] = RoutePlan(**row)
    for wlt, row in data.get('guides', {}).items():
        led.guides[wlt] = GuideCard(**row)
    for sid, row in data.get('sessions', {}).items():
        led.sessions[int(sid)] = SessionTicket(**row)
    if led.routes:
        led._next_sketch = max(led.routes) + 1
    if led.sessions:
        led._next_session = max(led.sessions) + 1
    return led

def climate_label(code: int) -> str:
    for name, band, tag in CLIMATE_BANDS:
        if band == code:
            return f'{name} ({tag})'
    return 'unknown'

def random_card_id() -> str:
    return "0x" + secrets.token_hex(32)

def demo_seed(ledger: TravelLedger) -> None:
    ledger.list_advisory(random_card_id(), 7, 'Coastal ferry lanes — spring window')
    ledger.list_advisory(random_card_id(), 3, 'Monsoon rail bypass alternatives')
    g = ledger.register_guide(CHAIN_HINT_A, 'Certified ridge navigator')
    stops = list(ledger.advisories.keys())[:2] or [random_card_id()]
    ledger.mint_route(stops, 9)
    ledger.open_session(stops[0], g.wallet, 10**15)

def route_heuristic_0(stops: Sequence[str], day_span: int) -> float:
    base = len(stops) * 1.7 + day_span * 0.4
    jitter = (zlib.crc32(str(stops).encode()) % 17) / 100.0
    return round(base + jitter, 4)

def advisory_rank_0(note: AdvisoryNote) -> float:
    climate_boost = note.climate * 0.03
    review_boost = note.review_count * 0.11
    retire_penalty = 2.5 if note.retired else 0.0
    return max(0.0, note.rating_avg + climate_boost + review_boost - retire_penalty)

def session_quote_0(deposit_wei: int, fee_bp: int = 73) -> Dict[str, int]:
    fee = deposit_wei * fee_bp // 10_000
    return {'deposit': deposit_wei, 'fee': fee, 'net': deposit_wei - fee}

def route_heuristic_1(stops: Sequence[str], day_span: int) -> float:
    base = len(stops) * 1.7 + day_span * 0.4
    jitter = (zlib.crc32(str(stops).encode()) % 18) / 100.0
    return round(base + jitter, 4)

def advisory_rank_1(note: AdvisoryNote) -> float:
    climate_boost = note.climate * 0.03
    review_boost = note.review_count * 0.11
    retire_penalty = 2.5 if note.retired else 0.0
