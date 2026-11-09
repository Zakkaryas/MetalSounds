#!/usr/bin/env python3
"""
Extrae la discografía completa de un grupo desde MusicBrainz y la guarda en JSON.

Uso:
    pip install requests
    python metal_discografia.py              # te pregunta el grupo
    python metal_discografia.py "Iron Maiden"

Todo se acumula en data/discografias.json (una entrada por grupo).

Opciones:
    --all-releases   guarda TODAS las ediciones de cada disco (más lento)
    --db ruta        base de datos JSON (por defecto data/discografias.json)
    --first          elige el primer resultado sin preguntar (útil en automatizaciones)
    --refresh        vuelve a descargar un grupo que ya está en la base de datos

Notas:
- MusicBrainz limita a 1 petición/segundo; el script respeta ese límite.
- Cambia CONTACTO por tu email o web (lo exige MusicBrainz en el User-Agent).
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

CONTACTO = "tu_email@ejemplo.com"
BASE = "https://musicbrainz.org/ws/2"
HEADERS = {"User-Agent": f"MetalDiscografiaScraper/0.1 ( {CONTACTO} )", "Accept": "application/json"}
_last_call = 0.0


def mb_get(path, **params):
    """GET con rate-limit (1 req/s) y reintentos ante 503."""
    global _last_call
    params["fmt"] = "json"
    for intento in range(5):
        espera = 1.1 - (time.time() - _last_call)
        if espera > 0:
            time.sleep(espera)
        _last_call = time.time()
        r = requests.get(f"{BASE}/{path}", params=params, headers=HEADERS, timeout=30)
        if r.status_code in (429, 503):
            time.sleep(2 * (intento + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Demasiados reintentos en {path}")


def browse_all(path, key, **params):
    """Recorre todas las páginas de una consulta 'browse'."""
    items, offset = [], 0
    while True:
        data = mb_get(path, limit=100, offset=offset, **params)
        items.extend(data[key])
        offset += 100
        if offset >= data.get(f"{key[:-1]}-count", data.get("release-group-count", len(items))):
            break
    return items


def tags(obj, campo="tags"):
    return sorted(
        ({"name": t["name"], "count": t.get("count", 0)} for t in obj.get(campo, [])),
        key=lambda t: -t["count"],
    )


# ---------------------------------------------------------------- artista
def buscar_artista(nombre, primero=False):
    data = mb_get("artist", query=f'artist:"{nombre}"', limit=10)
    candidatos = data.get("artists", [])
    if not candidatos:
        sys.exit("No se encontró ningún artista con ese nombre.")
    if primero:
        return candidatos[0]["id"]
    print("\nResultados:")
    for i, a in enumerate(candidatos, 1):
        t = ", ".join(x["name"] for x in tags(a)[:3]) or "-"
        print(f"  {i}. {a['name']}  [{a.get('country', '?')}]  "
              f"{a.get('disambiguation', '')}  (tags: {t})  score={a['score']}")
    sel = input("\nElige número [1]: ").strip() or "1"
    return candidatos[int(sel) - 1]["id"]


def info_artista(mbid):
    a = mb_get("artist/" + mbid,
               inc="aliases+tags+genres+ratings+url-rels+artist-rels")
    miembros, enlaces = [], {}
    for rel in a.get("relations", []):
        if rel["type"] == "member of band" and rel.get("artist"):
            miembros.append({
                "name": rel["artist"]["name"],
                "mbid": rel["artist"]["id"],
                "roles": rel.get("attributes", []),
                "begin": rel.get("begin"),
                "end": rel.get("end"),
                "current": not rel.get("ended", False),
            })
        elif "url" in rel:
            enlaces.setdefault(rel["type"], []).append(rel["url"]["resource"])
    return {
        "mbid": a["id"],
        "name": a["name"],
        "sort_name": a.get("sort-name"),
        "type": a.get("type"),
        "country": a.get("country"),
        "area": (a.get("area") or {}).get("name"),
        "begin_area": (a.get("begin-area") or {}).get("name"),
        "formed": (a.get("life-span") or {}).get("begin"),
        "disbanded": (a.get("life-span") or {}).get("end"),
        "ended": (a.get("life-span") or {}).get("ended"),
        "disambiguation": a.get("disambiguation"),
        "aliases": [x["name"] for x in a.get("aliases", [])],
        "genres": tags(a, "genres"),
        "tags": tags(a),
        "rating": a.get("rating"),
        "members": miembros,
        "links": enlaces,
    }


# ------------------------------------------------------------- discografía
def procesar_release(rel_id):
    r = mb_get("release/" + rel_id,
               inc="recordings+labels+artist-credits+release-groups+genres+url-rels+media")
    medios = []
    for m in r.get("media", []):
        pistas = []
        for p in m.get("tracks", []):
            rec = p.get("recording", {})
            pistas.append({
                "position": p.get("position"),
                "number": p.get("number"),
                "title": p.get("title"),
                "length_ms": p.get("length") or rec.get("length"),
                "recording_mbid": rec.get("id"),
                "isrcs": rec.get("isrcs", []),
            })
        medios.append({
            "position": m.get("position"),
            "format": m.get("format"),
            "title": m.get("title"),
            "track_count": m.get("track-count"),
            "tracks": pistas,
        })
    return {
        "mbid": r["id"],
        "title": r["title"],
        "status": r.get("status"),
        "date": r.get("date"),
        "country": r.get("country"),
        "barcode": r.get("barcode"),
        "packaging": r.get("packaging"),
        "language": (r.get("text-representation") or {}).get("language"),
        "script": (r.get("text-representation") or {}).get("script"),
        "labels": [
            {"name": (l.get("label") or {}).get("name"), "catalog_number": l.get("catalog-number")}
            for l in r.get("label-info", [])
        ],
        "genres": tags(r, "genres"),
        "media": medios,
        "links": [x["url"]["resource"] for x in r.get("relations", []) if "url" in x],
    }


def discografia(mbid, todas):
    grupos = browse_all("release-group", "release-groups",
                        artist=mbid, inc="tags+genres+ratings")
    grupos.sort(key=lambda g: g.get("first-release-date") or "9999")
    resultado = []
    for i, g in enumerate(grupos, 1):
        print(f"  [{i}/{len(grupos)}] {g.get('first-release-date', '?')}  {g['title']}")
        releases = browse_all("release", "releases",
                              **{"release-group": g["id"], "inc": "labels+media"})
        oficiales = [r for r in releases if r.get("status") == "Official"] or releases
        oficiales.sort(key=lambda r: r.get("date") or "9999")
        elegidas = oficiales if todas else oficiales[:1]
        resultado.append({
            "mbid": g["id"],
            "title": g["title"],
            "primary_type": g.get("primary-type"),
            "secondary_types": g.get("secondary-types", []),
            "first_release_date": g.get("first-release-date"),
            "genres": tags(g, "genres"),
            "tags": tags(g),
            "rating": g.get("rating"),
            "total_editions": len(releases),
            "releases": [procesar_release(r["id"]) for r in elegidas],
        })
    return resultado


# -------------------------------------------------------------------- main
def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def cargar_db(ruta):
    if ruta.exists():
        return json.loads(ruta.read_text(encoding="utf-8"))
    return {"source": "MusicBrainz", "updated_at": None, "bands": []}


def guardar_db(ruta, db):
    db["bands"].sort(key=lambda b: b["artist"]["name"].lower())
    db["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("grupo", nargs="?")
    ap.add_argument("--all-releases", action="store_true")
    ap.add_argument("--db", default="data/discografias.json")
    ap.add_argument("--first", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    ruta = Path(args.db)
    db = cargar_db(ruta)

    nombre = args.grupo or input("¿Qué grupo quieres? ").strip()
    mbid = buscar_artista(nombre, args.first)

    existente = next((b for b in db["bands"] if b["artist"]["mbid"] == mbid), None)
    if existente and not args.refresh:
        print(f"\n{existente['artist']['name']} ya está en la base de datos "
              f"(descargado el {existente['fetched_at'][:10]}). Usa --refresh para actualizarlo.")
        return

    print("\nObteniendo ficha del artista...")
    artista = info_artista(mbid)
    print(f"Descargando discografía de {artista['name']}...")
    discos = discografia(mbid, args.all_releases)

    entrada = {
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "artist": artista,
        "discography": discos,
    }
    db["bands"] = [b for b in db["bands"] if b["artist"]["mbid"] != mbid] + [entrada]
    guardar_db(ruta, db)

    n_pistas = sum(len(m["tracks"]) for d in discos for r in d["releases"] for m in r["media"])
    print(f"\nListo: {len(discos)} lanzamientos, {n_pistas} pistas. "
          f"Base de datos: {len(db['bands'])} grupos -> {ruta}")


if __name__ == "__main__":
    main()
