"""FastAPI app: patient chat, booking API, admin API and WhatsApp webhook."""
import threading
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from . import booking, config, db
from .assistant import Assistant

STATIC = Path(__file__).resolve().parent.parent / "static"


class ChatIn(BaseModel):
    session_id: str = Field(min_length=4, max_length=64)
    message: str = Field(min_length=1, max_length=1000)


class BookIn(BaseModel):
    doctor_id: int
    date: date
    time: str = Field(pattern=r"^\d{2}:\d{2}$")
    patient_name: str
    patient_phone: str
    reason: str = ""


class CancelIn(BaseModel):
    phone: str


class StatusIn(BaseModel):
    status: str


class DoctorIn(BaseModel):
    name: str
    specialty: str
    work_days: str = "0,1,2,3,4,5"
    start_time: str = Field("10:00", pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field("17:00", pattern=r"^\d{2}:\d{2}$")
    slot_minutes: int = Field(20, ge=5, le=120)
    fee: int = Field(500, ge=0)


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title=f"{config.CLINIC_NAME} - AI Booking", version="1.0.0")
    conn = db.connect(db_path)
    db.init_db(conn)
    lock = threading.Lock()
    app.state.conn = conn

    def get_conn():
        with lock:  # one SQLite connection, one request at a time
            yield conn

    def require_admin(x_admin_token: str = Header(default="")):
        if x_admin_token != config.ADMIN_TOKEN:
            raise HTTPException(401, "Invalid admin token")

    def bad(e: booking.BookingError):
        return HTTPException(400, str(e))

    # ---------- pages
    @app.get("/", include_in_schema=False)
    def chat_page():
        return FileResponse(STATIC / "index.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page():
        return FileResponse(STATIC / "admin.html")

    @app.get("/health")
    def health():
        return {"ok": True, "llm": bool(config.LLM_API_KEY)}

    @app.get("/api/clinic")
    def clinic():
        return {"name": config.CLINIC_NAME, "phone": config.CLINIC_PHONE}

    # ---------- public API
    @app.post("/api/chat")
    def chat(body: ChatIn, c=Depends(get_conn)):
        return Assistant(c).reply(body.session_id, body.message, source="web")

    @app.get("/api/doctors")
    def doctors(c=Depends(get_conn)):
        return booking.list_doctors(c)

    @app.get("/api/doctors/{doctor_id}/slots")
    def slots(doctor_id: int, date: date, c=Depends(get_conn)):
        if not booking.get_doctor(c, doctor_id):
            raise HTTPException(404, "Doctor not found")
        return {"doctor_id": doctor_id, "date": date.isoformat(), "slots": booking.available_slots(c, doctor_id, date)}

    @app.post("/api/appointments", status_code=201)
    def create_appt(body: BookIn, c=Depends(get_conn)):
        try:
            return booking.book(c, body.doctor_id, body.date, body.time, body.patient_name,
                                body.patient_phone, body.reason, source="api")
        except booking.BookingError as e:
            raise bad(e)

    @app.get("/api/appointments/{code}")
    def get_appt(code: str, phone: str, c=Depends(get_conn)):
        appt = booking.get_appointment(c, code)
        try:
            ok = appt and booking.normalize_phone(phone) == appt["patient_phone"]
        except booking.BookingError:
            ok = False
        if not ok:
            raise HTTPException(404, "Booking not found")
        return appt

    @app.post("/api/appointments/{code}/cancel")
    def cancel_appt(code: str, body: CancelIn, c=Depends(get_conn)):
        try:
            return booking.cancel(c, code, body.phone)
        except booking.BookingError as e:
            raise bad(e)

    # ---------- admin API
    @app.get("/api/admin/stats", dependencies=[Depends(require_admin)])
    def admin_stats(c=Depends(get_conn)):
        return booking.stats(c)

    @app.get("/api/admin/appointments", dependencies=[Depends(require_admin)])
    def admin_list(date: str | None = None, status: str | None = None, doctor_id: int | None = None,
                   c=Depends(get_conn)):
        return booking.list_appointments(c, day=date, status=status, doctor_id=doctor_id)

    @app.post("/api/admin/appointments/{code}/status", dependencies=[Depends(require_admin)])
    def admin_status(code: str, body: StatusIn, c=Depends(get_conn)):
        try:
            return booking.set_status(c, code, body.status)
        except booking.BookingError as e:
            raise bad(e)

    @app.post("/api/admin/doctors", status_code=201, dependencies=[Depends(require_admin)])
    def admin_add_doctor(body: DoctorIn, c=Depends(get_conn)):
        return booking.add_doctor(c, body.model_dump())

    @app.get("/api/admin/reminders", dependencies=[Depends(require_admin)])
    def admin_reminders(date: date, c=Depends(get_conn)):
        return booking.reminders_for(c, date)

    # ---------- WhatsApp (Twilio sandbox / WhatsApp Business via Twilio)
    @app.post("/webhook/whatsapp")
    def whatsapp(From: str = Form(...), Body: str = Form(""), c=Depends(get_conn)):
        result = Assistant(c).reply("wa:" + From, Body or "hi", source="whatsapp")
        text = result["text"]
        if result.get("options"):
            text += "\n\n" + " | ".join(result["options"])
        twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escape(text)}</Message></Response>'
        return Response(twiml, media_type="application/xml")

    return app


app = create_app()
