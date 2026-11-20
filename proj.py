import time as clock

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Clinic Appointment API")

# Valid slot start times: 10:00, 10:30, ... 16:30 (the last slot ends at 17:00)
SLOTS = [f"{h:02d}:{m:02d}" for h in range(10, 17) for m in (0, 30)]
SLOT_MESSAGE = (
    "time must be HH:MM, a multiple of 30 minutes, between 10:00 and 16:30 (e.g. 10:30)"
)
DATE_MESSAGE = "date must be a valid date in YYYY-MM-DD format (e.g. 2030-01-10)"
GENDERS = ("male", "female", "other")

# India Standard Time = UTC+5:30 (no daylight saving). Change the offset for another timezone.
IST_OFFSET_SECONDS = 5 * 3600 + 30 * 60


# ---------------------------------------------------------------- time helpers
# Dates are plain "YYYY-MM-DD" strings and times are "HH:MM" strings.
# In this exact format they sort and compare correctly as text.
def now_local() -> str:
    """Current clinic time as "YYYY-MM-DD HH:MM"."""
    return clock.strftime("%Y-%m-%d %H:%M", clock.gmtime(clock.time() + IST_OFFSET_SECONDS))


def today_str() -> str:
    return now_local()[:10]


def is_valid_date(d: str) -> bool:
    """True only for a real calendar date written exactly as YYYY-MM-DD."""
    if len(d) != 10 or d[4] != "-" or d[7] != "-":
        return False
    year, month, day = d[:4], d[5:7], d[8:]
    for part in (year, month, day):
        if not (part.isascii() and part.isdigit()):
            return False
    year, month, day = int(year), int(month), int(day)
    if not 1 <= month <= 12:
        return False
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days_in_month = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return 1 <= day <= days_in_month[month - 1]


def is_past(d: str, t: str) -> bool:
    """True if the slot starting at date d ("YYYY-MM-DD") / time t ("HH:MM") has started."""
    return f"{d} {t}" <= now_local()


def available_slots(booked: set[str], on_date: str) -> list[str]:
    """Slots that are neither booked nor in the past."""
    return [s for s in SLOTS if s not in booked and not is_past(on_date, s)]


def is_valid_phone(phone: str) -> bool:
    return phone.isascii() and phone.isdigit() and len(phone) == 10


# ---------------------------------------------------------------- models
class Doctor(BaseModel):
    id: int
    name: str
    specialisation: str


class AppointmentIn(BaseModel):
    patient_name: str
    phone: str
    age: int
    gender: str
    date: str  # "YYYY-MM-DD"
    time: str  # "HH:MM"
    doctor_id: int


class Appointment(AppointmentIn):
    id: int
    doctor_name: str


# ---------------------------------------------------------------- storage
doctors_db: list[Doctor] = [
    Doctor(id=1, name="Dr. Arjun Rao", specialisation="General Physician"),
    Doctor(id=2, name="Dr. Meera Nair", specialisation="Dermatologist"),
    Doctor(id=3, name="Dr. Karthik Iyer", specialisation="Orthopedic"),
]

appointments_db: dict[int, Appointment] = {}
next_id = 1


# ---------------------------------------------------------------- helpers
def validate_appointment(data: AppointmentIn) -> None:
    """Input checks for booking/modifying. Raises 422 listing every problem."""
    errors = []
    today = today_str()
    date_ok = is_valid_date(data.date)

    if not data.patient_name.strip():
        errors.append("patient_name cannot be empty")
    if not is_valid_phone(data.phone):
        errors.append("phone must be exactly 10 digits")
    if not 0 <= data.age <= 120:
        errors.append("age must be between 0 and 120")
    if data.gender.lower() not in GENDERS:
        errors.append("gender must be male, female or other")
    if not date_ok:
        errors.append(DATE_MESSAGE)
    elif data.date < today:
        errors.append("date cannot be in the past")
    if data.time not in SLOTS:
        errors.append(SLOT_MESSAGE)
    elif date_ok and data.date >= today and is_past(data.date, data.time):
        errors.append("that date/time has already passed; choose a future slot")

    if errors:
        raise HTTPException(422, detail=errors)


def find_doctor(doctor_id: int) -> Doctor:
    for d in doctors_db:
        if d.id == doctor_id:
            return d
    raise HTTPException(404, f"Doctor with id {doctor_id} not found")


def booked_times(doctor_id: int, on_date: str, ignore_id: int | None = None) -> set[str]:
    return {
        a.time
        for a in appointments_db.values()
        if a.doctor_id == doctor_id and a.date == on_date and a.id != ignore_id
    }


def check_available(data: AppointmentIn, ignore_id: int | None = None) -> None:
    booked = booked_times(data.doctor_id, data.date, ignore_id)
    if data.time in booked:
        raise HTTPException(
            409,
            detail={
                "message": f"Doctor is already booked at {data.time} on {data.date}",
                "free_slots": available_slots(booked, data.date),
            },
        )


# ---------------------------------------------------------------- endpoints
@app.get("/doctors", response_model=list[Doctor])
def list_doctors():
    return doctors_db


@app.post("/appointments", response_model=Appointment, status_code=201)
def book_appointment(data: AppointmentIn):
    global next_id
    validate_appointment(data)
    doctor = find_doctor(data.doctor_id)
    check_available(data)
    appt = Appointment(id=next_id, doctor_name=doctor.name, **data.model_dump())
    appointments_db[next_id] = appt
    next_id += 1
    return appt


@app.put("/appointments/{appointment_id}", response_model=Appointment)
def modify_appointment(appointment_id: int, data: AppointmentIn):
    validate_appointment(data)
    if appointment_id not in appointments_db:
        raise HTTPException(404, "Appointment not found")
    doctor = find_doctor(data.doctor_id)
    check_available(data, ignore_id=appointment_id)
    appt = Appointment(id=appointment_id, doctor_name=doctor.name, **data.model_dump())
    appointments_db[appointment_id] = appt
    return appt


@app.delete("/appointments/{appointment_id}")
def delete_appointment(appointment_id: int):
    if appointment_id not in appointments_db:
        raise HTTPException(404, "Appointment not found")
    del appointments_db[appointment_id]
    return {"message": f"Appointment {appointment_id} deleted"}


@app.get("/appointments/schedule/{doctor_id}")
def doctor_availability(doctor_id: int, on_date: str, time: str | None = None):
    """For patients: which slots are booked/free for a doctor on a date (YYYY-MM-DD).
    Pass `time` (HH:MM) to check one specific slot. No patient details are exposed."""
    if not is_valid_date(on_date):
        raise HTTPException(422, DATE_MESSAGE)
    doctor = find_doctor(doctor_id)
    booked = booked_times(doctor_id, on_date)
    result = {
        "doctor_id": doctor.id,
        "doctor_name": doctor.name,
        "date": on_date,
        "booked_slots": sorted(booked),
        "free_slots": available_slots(booked, on_date),
    }
    if time is not None:
        if time not in SLOTS:
            raise HTTPException(422, SLOT_MESSAGE)
        result["time"] = time
        if is_past(on_date, time):
            result["is_free"] = False
            result["reason"] = "this time has already passed"
        elif time in booked:
            result["is_free"] = False
            result["reason"] = "already booked"
        else:
            result["is_free"] = True
    return result


@app.get("/patient/appointment", response_model=list[Appointment])
def patient_appointments(patient_name: str, phone: str):
    """A patient sees only their own appointments (matched by name + phone)."""
    if not is_valid_phone(phone):
        raise HTTPException(422, "phone must be exactly 10 digits")
    return [
        a
        for a in appointments_db.values()
        if a.patient_name.lower() == patient_name.lower() and a.phone == phone
    ]


@app.get("/doctors/appointment", response_model=list[Appointment])
def doctor_schedule(doctor_id: int, on_date: str | None = None):
    """A doctor's full schedule (with patient details), optionally for one date."""
    if on_date is not None and not is_valid_date(on_date):
        raise HTTPException(422, DATE_MESSAGE)
    find_doctor(doctor_id)
    result = [
        a
        for a in appointments_db.values()
        if a.doctor_id == doctor_id and (on_date is None or a.date == on_date)
    ]
    return sorted(result, key=lambda a: (a.date, a.time))


@app.get("/receptionist/records", response_model=list[Appointment])
def receptionist_records(on_date: str, time: str | None = None):
    """Receptionist: full details of every appointment on a day (YYYY-MM-DD), past or
    upcoming. Pass `time` (HH:MM) to see only one slot."""
    if not is_valid_date(on_date):
        raise HTTPException(422, DATE_MESSAGE)
    if time is not None and time not in SLOTS:
        raise HTTPException(422, SLOT_MESSAGE)
    result = [
        a
        for a in appointments_db.values()
        if a.date == on_date and (time is None or a.time == time)
    ]
    return sorted(result, key=lambda a: (a.time, a.doctor_name))
