Das Projekt ist ein webbasiertes Ticketverwaltungssystem auf Basis von Django. Benutzer können Support-Tickets erstellen und verfolgen, während Supportmitarbeiter und Administratoren die Tickets bearbeiten und verwalten können.

## Requirements

Python 3.11 oder neuer

uv

## Abhängigkeiten installieren:

uv sync

.env

Die benötigte .env-Datei ist bereits in der ZIP-Datei enthalten.

## Den Serven Starten

uv run python manage.py migrate
uv run python manage.py runserver