"""Conversational booking assistant (English + Telugu).

Works fully offline with a rule-based parser. If LLM_API_KEY is set, an
OpenAI-compatible model is also used to understand free-form messages
(e.g. "my son has fever, can we come day after tomorrow evening?").
"""
import json
import re
from datetime import date, datetime, timedelta

import httpx

from . import booking, config
from .db import get_session, save_session

# ---------------------------------------------------------------- language

TEXT = {
    "en": {
        "welcome": "Hi! I'm the booking assistant for {clinic}. I can book, cancel or check appointments. What do you need?",
        "ask_doctor": "Which doctor would you like to see? Reply with a number or describe the problem (e.g. fever, tooth pain, skin).",
        "ask_date": "When would you like to come to {doctor}? Next open days: {days}. You can also say 'today' or 'tomorrow'.",
        "no_slots": "{doctor} has no free slots on {date}. Next open days: {days}.",
        "ask_time": "Free slots with {doctor} on {date}: {slots}. Which time suits you?",
        "bad_time": "That time isn't free. Closest free slots: {slots}.",
        "ask_name": "Great, {time} on {date}. What is the patient's name?",
        "ask_phone": "Thanks {name}. Your 10-digit mobile number?",
        "confirm": "Please confirm: {name} with {doctor} ({specialty}) on {date} at {time}, fee Rs {fee}. Shall I book it? (yes/no)",
        "booked": "Booked! Your code is {code}. {doctor}, {date} at {time}. Please arrive 10 minutes early. To cancel, send: cancel {code}",
        "aborted": "Okay, I didn't book anything. Say 'book' whenever you want to start again.",
        "ask_code": "Please send your booking code (it looks like CB1A2B3C).",
        "cancelled": "Booking {code} with {doctor} on {date} at {time} is cancelled.",
        "my_none": "I found no upcoming bookings for {phone}.",
        "my_list": "Your bookings: {items}",
        "ask_phone_lookup": "Please send the mobile number you booked with.",
        "help": "You can say things like: 'book a dentist tomorrow', 'cancel CB1A2B3C', 'my bookings', or 'doctors'.",
        "doctors": "Our doctors: {items}",
    },
    "te": {
        "welcome": "నమస్తే! నేను {clinic} బుకింగ్ అసిస్టెంట్‌ని. అపాయింట్‌మెంట్ బుక్, క్యాన్సిల్ లేదా చెక్ చేయగలను. మీకు ఏం కావాలి?",
        "ask_doctor": "ఏ డాక్టర్‌ను కలవాలి? నంబర్ పంపండి లేదా సమస్య చెప్పండి (జ్వరం, పంటి నొప్పి, చర్మం).",
        "ask_date": "{doctor} దగ్గరకు ఎప్పుడు రావాలి? ఖాళీ రోజులు: {days}. 'ఈరోజు' లేదా 'రేపు' అని కూడా చెప్పొచ్చు.",
        "no_slots": "{date}న {doctor} దగ్గర ఖాళీ లేదు. ఖాళీ రోజులు: {days}.",
        "ask_time": "{date}న {doctor} ఖాళీ సమయాలు: {slots}. ఏ సమయం కావాలి?",
        "bad_time": "ఆ సమయం ఖాళీ లేదు. దగ్గరి సమయాలు: {slots}.",
        "ask_name": "సరే, {date} {time}. పేషెంట్ పేరు ఏమిటి?",
        "ask_phone": "ధన్యవాదాలు {name}. మీ 10 అంకెల మొబైల్ నంబర్?",
        "confirm": "నిర్ధారించండి: {name}, {doctor} ({specialty}), {date} {time}, ఫీజు Rs {fee}. బుక్ చేయమంటారా? (అవును/వద్దు)",
        "booked": "బుక్ అయింది! మీ కోడ్ {code}. {doctor}, {date} {time}. 10 నిమిషాలు ముందుగా రండి. క్యాన్సిల్ చేయాలంటే: cancel {code}",
        "aborted": "సరే, ఏదీ బుక్ చేయలేదు. మళ్ళీ కావాలంటే 'బుక్' అని పంపండి.",
        "ask_code": "మీ బుకింగ్ కోడ్ పంపండి (ఉదా: CB1A2B3C).",
        "cancelled": "{date} {time} {doctor} బుకింగ్ {code} క్యాన్సిల్ అయింది.",
        "my_none": "{phone} పై రాబోయే బుకింగ్‌లు లేవు.",
        "my_list": "మీ బుకింగ్‌లు: {items}",
        "ask_phone_lookup": "బుక్ చేసిన మొబైల్ నంబర్ పంపండి.",
        "help": "ఇలా అడగొచ్చు: 'రేపు డెంటిస్ట్ బుక్', 'cancel CB1A2B3C', 'నా బుకింగ్స్', 'డాక్టర్లు'.",
        "doctors": "మా డాక్టర్లు: {items}",
    },
}

TELUGU_RE = re.compile(r"[ఀ-౿]")

# ---------------------------------------------------------------- parsing

SPECIALTY_WORDS = {
    "General Physician": ["general", "physician", "fever", "cold", "cough", "bp", "sugar", "diabetes",
                          "headache", "body pain", "జ్వరం", "జలుబు", "దగ్గు", "తలనొప్పి"],
    "Pediatrician": ["child", "children", "kid", "baby", "son", "daughter", "pediatric", "paediatric",
                     "పిల్ల", "బాబు", "పాప"],
    "Dermatologist": ["skin", "acne", "pimple", "hair", "rash", "derma", "చర్మ", "జుట్టు", "మొటిమ"],
    "Dentist": ["tooth", "teeth", "dental", "dentist", "gum", "cavity", "root canal", "పన్ను", "పంటి", "పళ్ళ"],
    "Gynecologist": ["pregnan", "gyn", "period", "women", "lady doctor", "గర్భ", "పీరియడ్"],
}

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
YES = {"yes", "y", "ok", "okay", "confirm", "sure", "haan", "ha", "avunu", "sare", "సరే", "అవును", "హా", "book it"}
NO = {"no", "n", "nope", "nahi", "vaddu", "వద్దు", "stop"}
CODE_RE = re.compile(r"\bCB[0-9A-F]{6}\b", re.I)
PHONE_RE = re.compile(r"(\+?\d[\d\s-]{8,14}\d)")


def parse_date(text: str, today: date) -> date | None:
    t = text.lower()
    if "day after tomorrow" in t or "ఎల్లుండి" in t or "parso" in t:
        return today + timedelta(days=2)
    if "tomorrow" in t or "రేపు" in t or re.search(r"\bkal\b", t):
        return today + timedelta(days=1)
    if "today" in t or "ఈరోజు" in t or "ఈ రోజు" in t or re.search(r"\baaj\b", t):
        return today
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b", t)
    if m:
        year = int(m[3]) if m[3] else today.year
        year += 2000 if year < 100 else 0
        d = _safe_date(year, int(m[2]), int(m[1]))
        if d and not m[3] and d < today:
            d = _safe_date(year + 1, int(m[2]), int(m[1]))
        return d
    mon = "|".join(MONTHS)
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({mon})[a-z]*\b", t) or \
        re.search(rf"\b({mon})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", t)
    if m:
        a, b = m.groups()
        day_n, mon_s = (int(a), b) if a.isdigit() else (int(b), a)
        d = _safe_date(today.year, MONTHS.index(mon_s[:3]) + 1, day_n)
        if d and d < today:
            d = _safe_date(today.year + 1, MONTHS.index(mon_s[:3]) + 1, day_n)
        return d
    for i, wd in enumerate(WEEKDAYS):
        if re.search(rf"\b{wd}\b|\b{wd[:3]}\b", t):
            ahead = (i - today.weekday()) % 7 or 7
            if "this" in t and i == today.weekday():
                ahead = 0
            return today + timedelta(days=ahead)
    return None


def _safe_date(y, m, d) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_time(text: str, loose: bool) -> str | None:
    """Return HH:MM. `loose` allows a bare number ("11") when we just asked for a time."""
    t = text.lower().replace(".", ":")
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a m|p m)\b", t)
    if m:
        h, mi = int(m[1]) % 12, int(m[2] or 0)
        if m[3].replace(" ", "") == "pm":
            h += 12
        return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", t)
    if m and int(m[1]) < 24 and int(m[2]) < 60:
        return f"{int(m[1]):02d}:{m[2]}"
    m = re.search(r"\b(?:at|@)\s*(\d{1,2})\b", t) or (re.fullmatch(r"\s*(\d{1,2})\s*", t) if loose else None)
    if m:
        h = int(m[1])
        if 1 <= h <= 7:  # clinic hours: "at 4" means 4 pm
            h += 12
        return f"{h:02d}:00" if h < 24 else None
    for word, hh in (("morning", "10:00"), ("afternoon", "14:00"), ("evening", "16:30")):
        if word in t:
            return "~" + hh  # approximate preference
    return None


def match_doctor(text: str, doctors: list[dict], allow_number: bool) -> dict | None:
    t = text.lower().strip()
    if allow_number and re.fullmatch(r"\d{1,2}", t):
        i = int(t) - 1
        return doctors[i] if 0 <= i < len(doctors) else None
    for d in doctors:
        parts = [p for p in d["name"].lower().replace("dr.", "").split() if len(p) > 2]
        if any(re.search(rf"\b{re.escape(p)}\b", t) for p in parts):
            return d
    for d in doctors:
        if d["specialty"].lower() in t:
            return d
    for spec, words in SPECIALTY_WORDS.items():
        if any(w in t for w in words):
            for d in doctors:
                if d["specialty"] == spec:
                    return d
    return None


def detect_intent(text: str) -> str | None:
    t = text.lower()
    if CODE_RE.search(text) or re.search(r"\bcancel\b|క్యాన్సిల్|రద్దు", t):
        return "cancel"
    if re.search(r"my (booking|appointment)s?|\bstatus\b|నా బుకింగ్", t):
        return "my_bookings"
    if re.search(r"\b(doctors?|list)\b|డాక్టర్లు", t) and not re.search(r"\bbook\b", t):
        return "doctors"
    if re.search(r"\bhelp\b|సహాయం", t):
        return "help"
    if re.search(r"\b(book|appointment|consult|visit|see a|slot)\b|బుక్|అపాయింట్", t):
        return "book"
    if re.fullmatch(r"\s*(hi|hello|hey|namaste|నమస్తే|హాయ్)\W*", t):
        return "greet"
    return None


def llm_extract(text: str, doctors: list[dict], today: date) -> dict:
    """Ask an LLM to pull out booking fields. Returns {} on any problem."""
    if not config.LLM_API_KEY:
        return {}
    doc_list = "; ".join(f"{d['id']}: {d['name']} ({d['specialty']})" for d in doctors)
    prompt = (
        f"Today is {today.isoformat()} ({today.strftime('%A')}). Doctors: {doc_list}.\n"
        "Extract from the patient's message a JSON object with keys: intent (book|cancel|my_bookings|doctors|help|null), "
        "doctor_id (int|null, choose by name or by matching the symptom to a specialty), date (YYYY-MM-DD|null), "
        "time (HH:MM 24h|null), name (string|null, only if the patient states it), phone (string|null), "
        "reason (short symptom string|null). Reply with JSON only.\n"
        f"Message: {text}"
    )
    try:
        r = httpx.post(
            f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json={"model": config.LLM_MODEL, "temperature": 0,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=8,
        )
        r.raise_for_status()
        data = json.loads(r.json()["choices"][0]["message"]["content"])
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------- dialogue

def _fmt_day(d: date) -> str:
    return d.strftime("%a %d %b")


class Assistant:
    def __init__(self, conn):
        self.conn = conn

    def reply(self, session_id: str, message: str, source: str = "web",
              now: datetime | None = None) -> dict:
        now = now or booking.now_local()
        state = get_session(self.conn, session_id)
        if TELUGU_RE.search(message):
            state["lang"] = "te"
        state.setdefault("lang", "en")
        try:
            out = self._handle(state, message.strip(), source, now)
        except booking.BookingError as e:
            out = {"text": str(e), "options": []}
        save_session(self.conn, session_id, state)
        out["state"] = {k: v for k, v in state.items() if k != "lang"}
        return out

    # helpers -------------------------------------------------------------
    def _t(self, state, key, **kw):
        kw.setdefault("clinic", config.CLINIC_NAME)
        return TEXT[state["lang"]][key].format(**kw)

    def _doctor(self, state):
        return booking.get_doctor(self.conn, state["doctor_id"]) if state.get("doctor_id") else None

    # main ----------------------------------------------------------------
    def _handle(self, state: dict, msg: str, source: str, now: datetime) -> dict:
        doctors = booking.list_doctors(self.conn)
        today = now.date()
        step = state.get("step")
        low = msg.lower().strip(" .!")

        # -- intents that can interrupt any step
        intent = detect_intent(msg)
        llm = llm_extract(msg, doctors, today) if (intent is None or step is None) else {}
        intent = intent or llm.get("intent")

        if step == "confirm":
            if low in YES:
                return self._finish_booking(state, source, now)
            if low in NO or intent == "cancel":
                self._reset(state)
                return {"text": self._t(state, "aborted"), "options": ["Book appointment"]}

        if step == "cancel_code" or intent == "cancel":
            code = CODE_RE.search(msg)
            if not code:
                state["step"] = "cancel_code"
                return {"text": self._t(state, "ask_code"), "options": []}
            appt = booking.cancel(self.conn, code[0])
            self._reset(state)
            return {"text": self._t(state, "cancelled", code=appt["code"], doctor=appt["doctor_name"],
                                    date=appt["date"], time=appt["time"]), "options": ["Book appointment"]}

        if step == "lookup_phone" or intent == "my_bookings":
            phone = PHONE_RE.search(msg)
            if not phone:
                state["step"] = "lookup_phone"
                return {"text": self._t(state, "ask_phone_lookup"), "options": []}
            p = booking.normalize_phone(phone[0])
            items = [a for a in booking.list_appointments(self.conn, phone=p, status="booked")
                     if a["date"] >= today.isoformat()]
            self._reset(state)
            if not items:
                return {"text": self._t(state, "my_none", phone=p), "options": ["Book appointment"]}
            txt = "; ".join(f"{a['code']} - {a['doctor_name']} {a['date']} {a['time']}" for a in items)
            return {"text": self._t(state, "my_list", items=txt), "options": ["Book appointment"]}

        if intent == "doctors" and step is None:
            items = "; ".join(f"{i+1}. {d['name']} ({d['specialty']}, Rs {d['fee']})" for i, d in enumerate(doctors))
            return {"text": self._t(state, "doctors", items=items), "options": ["Book appointment"]}
        if intent == "help":
            return {"text": self._t(state, "help"), "options": ["Book appointment", "My bookings", "Cancel"]}
        if intent == "greet" and step is None:
            return {"text": self._t(state, "welcome"), "options": ["Book appointment", "My bookings", "Cancel", "Doctors"]}
        if step is None and intent != "book" and not (llm or match_doctor(msg, doctors, False) or parse_date(msg, today)):
            return {"text": self._t(state, "welcome"), "options": ["Book appointment", "My bookings", "Cancel", "Doctors"]}

        # -- booking flow: absorb anything useful from the message
        state.setdefault("step", "doctor")
        self._absorb(state, msg, llm, doctors, today, now)
        return self._next_question(state, doctors, now)

    def _absorb(self, state, msg, llm, doctors, today, now):
        step = state["step"]
        choosing = step in ("doctor", "date", "time")
        doc = match_doctor(msg, doctors, allow_number=(step == "doctor")) if choosing else None
        if not doc and llm.get("doctor_id"):
            doc = next((d for d in doctors if d["id"] == llm["doctor_id"]), None)
        if doc and doc["id"] != state.get("doctor_id"):
            state["doctor_id"] = doc["id"]
            state.pop("time", None)

        d = parse_date(msg, today) if choosing else None
        if not d and llm.get("date"):
            try:
                d = date.fromisoformat(llm["date"])
            except ValueError:
                d = None
        if d and d >= today:
            if d.isoformat() != state.get("date"):
                state.pop("time", None)
            state["date"] = d.isoformat()

        if step in ("time", "doctor", "date"):
            t = parse_time(msg, loose=(step == "time")) or llm.get("time")
            if t:
                state["want_time"] = t

        if llm.get("reason") and not state.get("reason"):
            state["reason"] = str(llm["reason"])[:200]
        elif (step == "doctor" and doc and not state.get("reason")
              and not re.match(r"\s*\d{1,2}\b", msg) and detect_intent(msg) is None):
            state["reason"] = msg[:200]

        if step == "name":
            name = PHONE_RE.sub("", msg)
            name = re.sub(r"^(my name is|i am|i'm|name is|naa peru|నా పేరు)\s*", "", name, flags=re.I).strip(" .,")
            if len(name) >= 2:
                state["name"] = name[:60].title() if name.isascii() else name[:60]
        elif llm.get("name"):
            state["name"] = str(llm["name"])[:60]

        phone = PHONE_RE.search(msg) or (llm.get("phone") and PHONE_RE.search(str(llm["phone"])))
        if phone and step in ("phone", "name", "doctor", "date", "time"):
            try:
                state["phone"] = booking.normalize_phone(phone[0])
            except booking.BookingError:
                if step == "phone":
                    raise

    def _next_question(self, state, doctors, now) -> dict:
        doc = self._doctor(state)
        if not doc:
            state["step"] = "doctor"
            listing = "; ".join(f"{i+1}. {d['name']} ({d['specialty']})" for i, d in enumerate(doctors))
            return {"text": self._t(state, "ask_doctor") + "\n" + listing,
                    "options": [f"{i+1}. {d['specialty']}" for i, d in enumerate(doctors)]}

        open_days = booking.next_open_days(self.conn, doc["id"], 3, now)
        days_txt = ", ".join(_fmt_day(d) for d in open_days) or "-"
        if not state.get("date"):
            state["step"] = "date"
            return {"text": self._t(state, "ask_date", doctor=doc["name"], days=days_txt),
                    "options": [d.isoformat() for d in open_days]}

        day = date.fromisoformat(state["date"])
        slots = booking.available_slots(self.conn, doc["id"], day, now)
        if not slots:
            state.pop("date", None)
            state["step"] = "date"
            return {"text": self._t(state, "no_slots", doctor=doc["name"], date=_fmt_day(day), days=days_txt),
                    "options": [d.isoformat() for d in open_days]}

        if not state.get("time"):
            want = state.pop("want_time", None)
            if want:
                target = want.lstrip("~")
                if want in slots:
                    state["time"] = want
                elif want.startswith("~"):
                    state["time"] = min(slots, key=lambda s: abs(_mins(s) - _mins(target)))
                else:
                    near = sorted(slots, key=lambda s: abs(_mins(s) - _mins(target)))[:4]
                    state["step"] = "time"
                    return {"text": self._t(state, "bad_time", slots=", ".join(sorted(near))), "options": sorted(near)}
        if not state.get("time"):
            state["step"] = "time"
            show = slots[:8]
            return {"text": self._t(state, "ask_time", doctor=doc["name"], date=_fmt_day(day), slots=", ".join(show)),
                    "options": show}

        if not state.get("name"):
            state["step"] = "name"
            return {"text": self._t(state, "ask_name", time=state["time"], date=_fmt_day(day)), "options": []}
        if not state.get("phone"):
            state["step"] = "phone"
            return {"text": self._t(state, "ask_phone", name=state["name"]), "options": []}

        state["step"] = "confirm"
        return {"text": self._t(state, "confirm", name=state["name"], doctor=doc["name"], specialty=doc["specialty"],
                                date=_fmt_day(day), time=state["time"], fee=doc["fee"]),
                "options": ["Yes", "No"]}

    def _finish_booking(self, state, source, now) -> dict:
        try:
            appt = booking.book(self.conn, state["doctor_id"], date.fromisoformat(state["date"]), state["time"],
                                state["name"], state["phone"], state.get("reason", ""), source, now)
        except booking.BookingError as e:
            state.pop("time", None)
            state["step"] = "time"
            return {"text": str(e), "options": []}
        lang = state["lang"]
        self._reset(state)
        state["lang"] = lang
        return {"text": self._t(state, "booked", code=appt["code"], doctor=appt["doctor_name"],
                                date=_fmt_day(date.fromisoformat(appt["date"])), time=appt["time"]),
                "options": ["Book another", "My bookings"], "appointment": appt}

    @staticmethod
    def _reset(state):
        lang = state.get("lang", "en")
        state.clear()
        state["lang"] = lang


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)
