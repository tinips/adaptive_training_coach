# Guia d'actualitzacio local

Executa aquestes comandes des de l'arrel del repositori:

```powershell
cd C:\Users\arbol\Documents\adaptive_training_coach
```

## Canvi de codi del bot

Utilitza aquesta opcio per a canvis en handlers, serveis, missatges, teclats,
workflows o configuracio Python del bot:

```powershell
docker compose up -d --build --no-deps bot
```

Comprova que ha arrencat:

```powershell
docker compose ps
docker compose logs --tail 100 bot
```

Per seguir els logs en directe:

```powershell
docker compose logs -f bot
```

## Canvi de configuracio del bot

Despres de canviar `.env`, no cal reconstruir la imatge. Recrea el contenidor:

```powershell
docker compose up -d --force-recreate --no-deps bot
docker compose logs --tail 100 bot
```

No exposis el valor de `TELEGRAM_BOT_TOKEN` als logs, documents o missatges.

## Canvi de l'API

Per actualitzar nomes l'API:

```powershell
docker compose up -d --build --no-deps api
curl http://localhost:8000/ready
```

Per actualitzar API i bot amb el mateix canvi compartit:

```powershell
docker compose up -d --build --no-deps api bot
docker compose ps
```

Espera que `api` mostri l'estat `healthy` abans de comprovar el bot.

## Canvi de dades, sense esquema

Per consultar o modificar dades de desenvolupament manualment:

```powershell
docker compose exec db psql -U coach -d adaptive_coach
```

Per exemple, llistar les taules del schema public:

```sql
\dt public.*
```

Surt amb:

```sql
\q
```

## Canvi d'esquema de la base de dades

En aquest projecte, les migracions Alembic antigues s'han mantingut sense
modificar. Per a l'esquema actual **no** utilitzis `alembic upgrade head` ni
arrenquis `migrate` per aplicar canvis nous: el seu historial pot recrear
taules eliminades.

Quan un canvi d'esquema estigui aprovat:

1. Actualitza primer els models i el codi.
2. Executa el SQL explicit corresponent sobre la base de dades de
   desenvolupament.
3. Verifica el resultat amb una consulta a `information_schema`.
4. Reconstrueix API i bot amb `--no-deps`.

Plantilla per executar un fitxer SQL local:

```powershell
Get-Content .\database-change.sql | docker compose exec -T db psql -v ON_ERROR_STOP=1 -U coach -d adaptive_coach
docker compose up -d --build --no-deps api bot
```

Per comprovar que una taula ja no existeix:

```powershell
docker compose exec -T db psql -U coach -d adaptive_coach -Atc "SELECT to_regclass('public.nom_de_la_taula');"
```

El resultat ha de ser buit.

## Reiniciar la base de dades local

**Aquesta operacio elimina totes les dades locals.**

```powershell
docker compose down -v
docker compose up -d db adminer
```

Despres d'un reset, no arrenquis `api`, `bot` ni `migrate` fins que hagis
aplicat l'esquema SQL que correspongui al codi actual. Les migracions existents
son historiques i no representen la neteja actual de l'esquema.

## Aturar els serveis

Atura contenidors sense esborrar la base de dades:

```powershell
docker compose down
```

## Resum rapid

```powershell
# Canvi de codi del bot
docker compose up -d --build --no-deps bot

# Canvi de configuracio del bot
docker compose up -d --force-recreate --no-deps bot

# Canvi compartit API + bot
docker compose up -d --build --no-deps api bot

# Estat i logs
docker compose ps
docker compose logs -f bot
```
