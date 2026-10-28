#!/usr/bin/env python3
"""Récupère les événements publics OpenAgenda pour la Métropole Européenne de Lille."""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

API = "https://api.openagenda.com/v2"
KEY = os.environ.get("OPENAGENDA_API_KEY", "").strip()
OUT = Path("events.json")

SEARCH_TERMS = [
    "Marcq-en-Baroeul",
    "Lille Métropole",
    "Métropole Européenne de Lille",
    "Roubaix",
    "Tourcoing",
    "Villeneuve-d'Ascq",
    "petite enfance Lille",
    "jeune public Lille",
]
AREA_TERMS = [
    "lille", "marcq", "roubaix", "tourcoing", "villeneuve-d'ascq",
    "lambersart", "la madeleine", "croix", "wasquehal", "mons-en-baroeul",
    "hem", "wattrelos", "métropole européenne de lille", "metropole europeenne de lille",
]
BABY_TERMS = [
    "bébé", "bebe", "0-3 ans", "0 à 3 ans", "petite enfance", "éveil", "eveil",
    "comptine", "comptines", "éveil musical", "eveil musical", "heure du conte",
    "tout-petits", "tout petits", "sensoriel", "parent-enfant", "parents-enfants",
]
SPORT_TERMS = [
    "sport", "football", "rugby", "basket", "handball", "course", "marathon",
    "natation", "tennis", "cyclisme", "équitation", "equitation", "yoga",
]

def api_get(path, params=None):
    if not KEY:
        raise RuntimeError("Le secret OPENAGENDA_API_KEY est absent.")
    url = API + path
    if params:
        url += "?" + urlencode(params, doseq=True)
    req = Request(url, headers={"key": KEY, "Accept": "application/json", "User-Agent": "agenda-familial-lillois/1.0"})
    with urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))

def text_value(value):
    if isinstance(value, dict):
        return value.get("fr") or next(iter(value.values()), "")
    if isinstance(value, list):
        return " ".join(str(x) for x in value)
    return str(value or "")

def first_timing(event):
    timings = event.get("timings") or []
    valid = []
    now = datetime.now(timezone.utc)
    for timing in timings:
        raw = timing.get("begin") or ""
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= now:
                valid.append((dt, timing))
        except ValueError:
            continue
    return min(valid, key=lambda pair: pair[0]) if valid else (None, None)

def format_date(dt):
    if not dt:
        return "Date à consulter"
    # display in local timezone from source timing string, e.g. 2026-11-01T10:00:00+0100
    months = ["janvier","février","mars","avril","mai","juin","juillet","août","septembre","octobre","novembre","décembre"]
    weekdays = ["lun.","mar.","mer.","jeu.","ven.","sam.","dim."]
    return f"{weekdays[dt.weekday()]} {dt.day} {months[dt.month-1]} {dt.year} · {dt.strftime('%H h %M')}"

def classify(event, agenda_title, place, description):
    combined = " ".join([text_value(event.get("title")), text_value(event.get("description")),
                         text_value(event.get("keywords")), agenda_title, place, description]).lower()
    age = event.get("age") or {}
    age_min = age.get("min") if isinstance(age, dict) else None
    age_max = age.get("max") if isinstance(age, dict) else None
    if (age_min is not None and age_min <= 3) or any(term in combined for term in BABY_TERMS):
        return "baby"
    if any(term in combined for term in SPORT_TERMS):
        return "sport"
    return "culture"

def main():
    agendas = {}
    for term in SEARCH_TERMS:
        try:
            data = api_get("/agendas", {"search": term, "size": 12, "includeFields[]": ["uid", "title", "slug", "description"]})
            for agenda in data.get("agendas", []):
                uid = agenda.get("uid")
                if uid:
                    agendas[str(uid)] = agenda
        except Exception as exc:
            print(f"Recherche d’agendas ignorée ({term}): {exc}", file=sys.stderr)
        time.sleep(0.15)

    # Limit to agendas with relevant geographic/subject clues to avoid pulling unrelated results.
    selected = []
    for agenda in agendas.values():
        title = text_value(agenda.get("title"))
        desc = text_value(agenda.get("description"))
        haystack = f"{title} {desc} {agenda.get('slug','')}".lower()
        if any(term in haystack for term in AREA_TERMS) or any(term in haystack for term in ["petite enfance", "jeunesse", "culture", "sport", "médiathèque", "mediatheque"]):
            selected.append(agenda)
    selected = selected[:35]

    events = {}
    for agenda in selected:
        uid = agenda["uid"]
        agenda_title = text_value(agenda.get("title")) or "OpenAgenda"
        try:
            data = api_get(f"/agendas/{uid}/events", [
                ("relative[]", "upcoming"), ("size", "100"), ("detailed", "1")
            ])
            raw_events = data.get("events", [])
            for item in raw_events:
                event = item.get("event", item)
                title = text_value(event.get("title")).strip()
                if not title:
                    continue
                timing_dt, timing = first_timing(event)
                if not timing_dt:
                    continue
                location = event.get("location") or {}
                place = text_value(location.get("name") or location.get("title"))
                city = text_value(location.get("city") or location.get("addressLocality") or location.get("locality"))
                address = text_value(location.get("address") or location.get("addressLine"))
                location_text = f"{place} {city} {address}".strip()
                haystack = f"{agenda_title} {location_text}".lower()
                # Accept events tied to the Lille metro area; if city is absent, rely on relevant agenda.
                if city and not any(term in city.lower() for term in AREA_TERMS):
                    continue
                if not city and not any(term in haystack for term in AREA_TERMS):
                    continue
                description = text_value(event.get("description"))
                canonical = event.get("canonicalUrl") or f"https://openagenda.com/{agenda.get('slug','')}/events/{event.get('uid','')}"
                age = event.get("age") or {}
                if isinstance(age, dict) and (age.get("min") is not None or age.get("max") is not None):
                    age_label = f"Âge indiqué : {age.get('min','?')}–{age.get('max','?')} ans"
                else:
                    age_label = "Âge conseillé à vérifier"
                record = {
                    "title": title,
                    "date": format_date(timing_dt),
                    "place": place or city or "Métropole lilloise",
                    "town": city or "Métropole lilloise",
                    "kind": classify(event, agenda_title, location_text, description),
                    "url": canonical,
                    "source": agenda_title,
                    "age": age_label,
                    "description": description[:400],
                    "_sort": timing_dt.isoformat(),
                }
                key = str(event.get("uid") or (title + record["date"] + record["place"]))
                events[key] = record
        except Exception as exc:
            print(f"Agenda {uid} ({agenda_title}) ignoré: {exc}", file=sys.stderr)
        time.sleep(0.15)

    output_events = sorted(events.values(), key=lambda e: e["_sort"])[:250]
    for event in output_events:
        event.pop("_sort", None)

    # Keep a valid, visible JSON even when no matching public events were found; don't silently erase old data.
    if not output_events:
        if OUT.exists():
            print("Aucun événement correspondant trouvé : ancien events.json conservé.")
            return
        OUT.write_text(json.dumps({"updatedAt": datetime.now(timezone.utc).isoformat(), "events": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Aucun événement correspondant trouvé. Fichier vide créé pour le premier lancement.")
        return

    payload = {"updatedAt": datetime.now(timezone.utc).isoformat(), "events": output_events}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(output_events)} événements enregistrés dans events.json depuis {len(selected)} agendas.")

if __name__ == "__main__":
    main()
