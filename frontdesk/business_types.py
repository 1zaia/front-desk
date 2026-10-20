"""Kinds of appointment-based businesses Front Desk can run, with sensible starting setups.

A new business picks a type at sign-up; the type gives it starter services, opening hours and the
words the receptionist uses ("barber", "stylist", "therapist"...). Everything can be edited afterwards.
"""
from __future__ import annotations

WEEKDAYS_9_TO_6 = [(d, "09:00", "18:00") for d in range(5)] + [(5, "09:00", "14:00")]

TYPES: dict[str, dict] = {
    "barbershop": {
        "label": "Barbershop", "staff": "barber", "staff_plural": "barbers", "customer": "customer",
        "services": [("Haircut", 30, 3500), ("Beard trim", 20, 2000), ("Haircut and beard", 50, 5000),
                     ("Hot towel shave", 40, 4000)],
        "team": ["Marcus", "Diego"],
        "hours": [(0, "09:00", "19:00"), (1, "09:00", "19:00"), (2, "09:00", "19:00"), (3, "09:00", "19:00"),
                  (4, "09:00", "20:00"), (5, "08:00", "14:00")],
    },
    "hair_salon": {
        "label": "Hair salon", "staff": "stylist", "staff_plural": "stylists", "customer": "client",
        "services": [("Women's haircut", 60, 6500), ("Men's haircut", 30, 3500), ("Blowout", 45, 4500),
                     ("Color", 120, 12000), ("Highlights", 150, 16000)],
        "team": ["Ava", "Mia"],
        "hours": [(1, "10:00", "19:00"), (2, "10:00", "19:00"), (3, "10:00", "20:00"), (4, "10:00", "20:00"),
                  (5, "09:00", "17:00")],
    },
    "nail_studio": {
        "label": "Nail studio", "staff": "nail tech", "staff_plural": "nail techs", "customer": "client",
        "services": [("Manicure", 30, 3000), ("Gel manicure", 45, 4500), ("Pedicure", 45, 4500),
                     ("Acrylic full set", 75, 6500)],
        "team": ["Lily", "Grace"],
        "hours": [(d, "10:00", "19:00") for d in range(6)],
    },
    "spa": {
        "label": "Spa and massage", "staff": "therapist", "staff_plural": "therapists", "customer": "guest",
        "services": [("Swedish massage", 60, 9000), ("Deep tissue massage", 60, 11000), ("Facial", 50, 8500),
                     ("Hot stone massage", 90, 14000)],
        "team": ["Nora", "Ethan"],
        "hours": [(d, "10:00", "20:00") for d in range(7)],
    },
    "physio": {
        "label": "Physical therapy", "staff": "physical therapist", "staff_plural": "physical therapists",
        "customer": "patient",
        "services": [("Initial assessment", 60, 15000), ("Follow-up session", 45, 11000), ("Sports massage", 30, 7000)],
        "team": ["Dr. Patel", "Dr. Kim"],
        "hours": [(d, "08:00", "18:00") for d in range(5)],
    },
    "clinic": {
        "label": "Clinic", "staff": "practitioner", "staff_plural": "practitioners", "customer": "patient",
        "services": [("Consultation", 30, 12000), ("Follow-up visit", 20, 8000), ("Annual checkup", 45, 18000)],
        "team": ["Dr. Rivera", "Dr. Chen"],
        "hours": [(d, "08:00", "17:00") for d in range(5)],
    },
    "pet_grooming": {
        "label": "Pet grooming", "staff": "groomer", "staff_plural": "groomers", "customer": "pet parent",
        "services": [("Bath and brush", 45, 4500), ("Full groom", 90, 8500), ("Nail trim", 15, 1500)],
        "team": ["Rosa", "Ben"],
        "hours": WEEKDAYS_9_TO_6,
    },
    "tattoo": {
        "label": "Tattoo studio", "staff": "artist", "staff_plural": "artists", "customer": "client",
        "services": [("Consultation", 30, 0), ("Small tattoo", 60, 12000), ("Half-day session", 240, 50000)],
        "team": ["Jay", "Rina"],
        "hours": [(d, "12:00", "21:00") for d in range(1, 6)],
    },
    "fitness": {
        "label": "Personal training", "staff": "trainer", "staff_plural": "trainers", "customer": "client",
        "services": [("Intro session", 30, 0), ("Personal training", 60, 8000), ("Partner training", 60, 11000)],
        "team": ["Alex", "Sam"],
        "hours": [(d, "06:00", "20:00") for d in range(6)],
    },
    "tutoring": {
        "label": "Tutoring", "staff": "tutor", "staff_plural": "tutors", "customer": "student",
        "services": [("One-hour lesson", 60, 6000), ("Exam prep", 90, 9000), ("Trial lesson", 30, 0)],
        "team": ["Ms. Lopez", "Mr. Grant"],
        "hours": [(d, "14:00", "20:00") for d in range(5)] + [(5, "09:00", "13:00")],
    },
    "other": {
        "label": "Other", "staff": "team member", "staff_plural": "team members", "customer": "customer",
        "services": [("Appointment", 30, 0)],
        "team": ["Front desk"],
        "hours": WEEKDAYS_9_TO_6,
    },
}

DEFAULT = "barbershop"


def get(kind: str | None) -> dict:
    return TYPES.get(kind or DEFAULT, TYPES["other"])


def catalog() -> list[dict]:
    """For the sign-up form."""
    return [{"id": k, "label": v["label"], "staff": v["staff"], "services": [s[0] for s in v["services"]]}
            for k, v in TYPES.items()]
