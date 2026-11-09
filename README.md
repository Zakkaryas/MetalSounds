# Metal Discos

Extrae discografías de grupos de metal desde MusicBrainz y las acumula en `data/discografias.json`.
`index.html` es un visor web que lee ese JSON (publicable con GitHub Pages).

## Añadir un grupo
```bash
pip install requests
python metal_discografia.py "Judas Priest"
git add data && git commit -m "Añadir Judas Priest" && git push
```

Opciones: `--refresh` (actualizar un grupo existente), `--all-releases`, `--first`.
