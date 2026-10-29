"""Patient-facing reply templates, one table per language."""

from __future__ import annotations

from typing import Literal

Lang = Literal["en", "te"]

TEMPLATES: dict[Lang, dict[str, str]] = {
    "en": {
        "welcome": "Hi! I'm the booking assistant for {clinic}. I can book, reschedule, cancel or check appointments. What do you need?",
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
        "help": "You can say things like: 'book a dentist tomorrow', 'reschedule CB1A2B3C', 'cancel CB1A2B3C', 'my bookings', or 'doctors'.",
        "doctors": "Our doctors: {items}",
        "on_leave": "{doctor} is on leave on {date}{reason}. Next open days: {days}.",
        "ask_reschedule_code": "Which booking would you like to move? Please send its code (it looks like CB1A2B3C).",
        "reschedule_ask_date": "Let's move your booking {code} with {doctor} (now {old}). Which day works? Next open days: {days}.",
        "reschedule_confirm": "Move booking {code} with {doctor} from {old} to {date} at {time}? (yes/no)",
        "rescheduled": "Done! Booking {code} is now on {date} at {time} with {doctor}. Your code stays the same.",
        "reschedule_kept": "Okay, nothing changed. Your booking stays at {old}.",
        "emergency": "⚠️ This sounds like a medical emergency. Please call 108 for an ambulance now, or go to the nearest emergency room. Don't wait for an appointment. You can also call the clinic on {phone}.",
        "self_harm": "I'm really sorry you're going through this. You're not alone, and help is available right now: call Tele-MANAS on 14416 (free, 24x7), or 108 if you are in immediate danger. If you'd like to see one of our doctors too, just say 'book'.",
    },
    "te": {
        "welcome": "నమస్తే! నేను {clinic} బుకింగ్ అసిస్టెంట్‌ని. అపాయింట్‌మెంట్ బుక్, మార్చడం, క్యాన్సిల్ లేదా చెక్ చేయగలను. మీకు ఏం కావాలి?",
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
        "help": "ఇలా అడగొచ్చు: 'రేపు డెంటిస్ట్ బుక్', 'reschedule CB1A2B3C', 'cancel CB1A2B3C', 'నా బుకింగ్స్', 'డాక్టర్లు'.",
        "doctors": "మా డాక్టర్లు: {items}",
        "on_leave": "{date}న {doctor} సెలవులో ఉన్నారు{reason}. ఖాళీ రోజులు: {days}.",
        "ask_reschedule_code": "ఏ బుకింగ్‌ను మార్చాలి? దాని కోడ్ పంపండి (ఉదా: CB1A2B3C).",
        "reschedule_ask_date": "{doctor} తో మీ బుకింగ్ {code} (ప్రస్తుతం {old}) మారుద్దాం. ఏ రోజు కావాలి? ఖాళీ రోజులు: {days}.",
        "reschedule_confirm": "బుకింగ్ {code} ను {old} నుండి {date} {time} కి మార్చమంటారా? (అవును/వద్దు)",
        "rescheduled": "అయిపోయింది! బుకింగ్ {code} ఇప్పుడు {date} {time}, {doctor}. మీ కోడ్ మారదు.",
        "reschedule_kept": "సరే, ఏమీ మార్చలేదు. మీ బుకింగ్ {old} కే ఉంటుంది.",
        "emergency": "⚠️ ఇది అత్యవసర పరిస్థితిలా ఉంది. వెంటనే 108 (అంబులెన్స్)కి కాల్ చేయండి, లేదా దగ్గరలోని ఎమర్జెన్సీకి వెళ్ళండి. అపాయింట్‌మెంట్ కోసం ఆగకండి. క్లినిక్ నంబర్: {phone}.",
        "self_harm": "మీరు ఇంత బాధలో ఉన్నందుకు చాలా విచారంగా ఉంది. మీరు ఒంటరి కాదు, సహాయం ఇప్పుడే అందుబాటులో ఉంది: Tele-MANAS 14416 (ఉచితం, 24x7)కి కాల్ చేయండి, లేదా తక్షణ ప్రమాదంలో ఉంటే 108కి కాల్ చేయండి. మా డాక్టర్‌ను కూడా కలవాలంటే 'బుక్' అని పంపండి.",
    },
}


def render(lang: Lang, key: str, **values: object) -> str:
    return TEMPLATES[lang][key].format(**values)
