# Django Ticketsystem 🎫

Ein effizientes Ticketsystem, entwickelt mit Django.

Hinweis: Dieses Repository dient in erster Linie als Portfolio-Projekt und Demonstration, um die Umsetzung einer funktionalen Webanwendung mit Rollenverwaltung und Datenbankanbindung zu veranschaulichen.

# Kernfunktionen

Rollenbasiertes System: Unterschiedliche Ansichten und Rechte für Administratoren, Support-Mitarbeiter und Endbenutzer.

Ticket-Management: Intuitives Erstellen, Bearbeiten, Einsehen und Schließen von Support-Tickets.

Organisation: Strukturierte Kategorisierung der Tickets nach Status (z.B. Offen, In Bearbeitung, Erledigt) und Priorität.

Admin-Panel: Vollständige Verwaltung der Daten über das integrierte Django-Backend.

# Tech-Stack

Backend: Python, Django

Frontend: HTML, CSS, JavaScript

Datenbank: SQLite

Lokales Setup (Quickstart)

Um das Projekt lokal auf deinem Rechner zu testen, genügen die Standard-Befehle von Django. Voraussetzung ist, dass Python installiert ist.

# 1. Repository klonen

git clone https://github.com/emre-kalayci/Django-Ticketsystem.git
cd Django-Ticketsystem

# 2. Abhängigkeiten installieren (empfohlen in einer virtuellen Umgebung)

pip install -r requirements.txt

# 3. Datenbank aufsetzen

python manage.py makemigrations
python manage.py migrate

# 4. Admin-Nutzer erstellen (optional, für den Backend-Zugriff)

python manage.py createsuperuser

# 5. Server starten

python manage.py runserver

Die Anwendung ist anschließend unter http://127.0.0.1:8000/ erreichbar, das Admin-Panel unter http://127.0.0.1:8000/admin/.

Entwickelt von Emre Kalayci
