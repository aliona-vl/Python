import psycopg2
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
import bcrypt

load_dotenv()


def seed_database():
    try:
        print("🐘 Verbinde mit PostgreSQL...")

        conn = psycopg2.connect(
            host=os.getenv('DB_HOST'),
            database=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            port=os.getenv('DB_PORT')
        )

        cursor = conn.cursor()

        print("📊 Füge Test-Daten ein...")

        # 1. Test-Benutzer erstellen
        password_hash = bcrypt.hashpw("test123".encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        cursor.execute("""
                       INSERT INTO benutzer (email, passworthash, name, zeitzone)
                       VALUES (%s, %s, %s, %s) ON CONFLICT (email) DO NOTHING
            RETURNING id;
                       """, ("admin@zeitrausch.de", password_hash, "Klaus Rausch", "Europe/Berlin"))

        result = cursor.fetchone()
        if result:
            admin_id = result[0]
            print(f"✅ Admin-User erstellt: {admin_id}")
        else:
            cursor.execute("SELECT id FROM benutzer WHERE email = %s", ("admin@zeitrausch.de",))
            admin_id = cursor.fetchone()[0]
            print(f"ℹ️ Admin-User existiert bereits: {admin_id}")

        # 2. Admin-Rolle zuweisen
        cursor.execute("""
                       INSERT INTO benutzer_rollen (benutzer_id, rolle)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING;
                       """, (admin_id, "admin"))

        # 3. Test-Team erstellen
        cursor.execute("""
                       INSERT INTO teams (name, code)
                       VALUES (%s, %s) ON CONFLICT (code) DO NOTHING
            RETURNING id;
                       """, ("Rausch Team", "RAUSCH-2024"))

        result = cursor.fetchone()
        if result:
            team_id = result[0]
            print(f"✅ Team erstellt: {team_id}")
        else:
            cursor.execute("SELECT id FROM teams WHERE code = %s", ("RAUSCH-2024",))
            team_id = cursor.fetchone()[0]
            print(f"ℹ️ Team existiert bereits: {team_id}")

        # 4. Admin zum Team hinzufügen
        cursor.execute("""
                       INSERT INTO team_mitglieder (team_id, benutzer_id, rolle_im_team)
                       VALUES (%s, %s, %s) ON CONFLICT DO NOTHING;
                       """, (team_id, admin_id, "admin"))

        # 5. Test-Unternehmen erstellen
        unternehmen_data = [
            ("Mustermann GmbH", "Europe/Berlin"),
            ("Tech Solutions AG", "Europe/Berlin"),
            ("Bauwerk Zentral", "Europe/Berlin")
        ]

        unternehmen_ids = []
        for name, zeitzone in unternehmen_data:
            cursor.execute("""
                           INSERT INTO unternehmen (name, zeitzone)
                           VALUES (%s, %s) ON CONFLICT (name) DO NOTHING
                RETURNING id;
                           """, (name, zeitzone))

            result = cursor.fetchone()
            if result:
                unternehmen_ids.append(result[0])
                print(f"✅ Unternehmen erstellt: {name}")
            else:
                cursor.execute("SELECT id FROM unternehmen WHERE name = %s", (name,))
                unternehmen_ids.append(cursor.fetchone()[0])
                print(f"ℹ️ Unternehmen existiert: {name}")

        # 6. Test-Projekt erstellen
        cursor.execute("""
                       INSERT INTO projekte (team_id, unternehmen_id, name, status, starter_id)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id;
                       """, (team_id, unternehmen_ids[0], "Website Redesign", "aktiv", admin_id))

        projekt_id = cursor.fetchone()[0]
        print(f"✅ Projekt erstellt: {projekt_id}")

        # 7. Projekt-Mitglied hinzufügen
        cursor.execute("""
                       INSERT INTO projekt_mitglieder (projekt_id, benutzer_id)
                       VALUES (%s, %s);
                       """, (projekt_id, admin_id))

        # 8. Test-Zeitbuchungen
        heute = datetime.now().date()
        cursor.execute("""
                       INSERT INTO zeitbuchungen (benutzer_id, unternehmen_id, datum_local, stunden, kommentar)
                       VALUES (%s, %s, %s, %s, %s);
                       """, (admin_id, unternehmen_ids[1], heute, 8.5, "Frontend Entwicklung"))

        conn.commit()

        print("\n🎯 Test-Daten erfolgreich eingefügt!")
        print(f"📧 Login: admin@zeitrausch.de")
        print(f"🔑 Passwort: test123")
        print(f"👥 Team-Code: RAUSCH-2024")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"❌ FEHLER: {e}")


if __name__ == '__main__':
    seed_database()