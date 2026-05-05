-- === Grundsetup ==============================================================
-- UUID-Unterstützung und CITEXT für E-Mails
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "citext";

-- === Enums ==================================================================
-- Projektstatus für Team-Oberfläche
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'projekt_status') THEN
    CREATE TYPE projekt_status AS ENUM ('pause', 'aktiv', 'beendet');
  END IF;
END$$;

-- Tätigkeitstypen innerhalb eines Projekts
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'taetigkeit_typ') THEN
    CREATE TYPE taetigkeit_typ AS ENUM ('auswaertstermin', 'zeichnung', 'besprechung', 'anderes');
  END IF;
END$$;

-- Rollen-System (global)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'rollenname') THEN
    CREATE TYPE rollenname AS ENUM ('admin', 'manager', 'mitarbeiter');
  END IF;
END$$;

-- === Stammdaten =============================================================

CREATE TABLE IF NOT EXISTS benutzer (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  email CITEXT UNIQUE NOT NULL,
  passworthash TEXT, -- für E-Mail/Passwort-Login (bcrypt/argon2)
  name TEXT,
  zeitzone TEXT NOT NULL DEFAULT 'UTC',-- IANA, z.B. Europe/Berlin
  aktiviert BOOLEAN NOT NULL DEFAULT TRUE,
  geloescht BOOLEAN NOT NULL DEFAULT FALSE,
  erstellt_am TIMESTAMPTZ NOT NULL DEFAULT now(),
  aktualisiert_am TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE benutzer IS 'Benutzerkonto. Alle Zeitpunkte in UTC (timestamptz).';
COMMENT ON COLUMN benutzer.zeitzone IS 'Bevorzugte IANA-Zeitzone des Benutzers für lokale Anzeige/Datumslogik.';

-- OAuth-Verknüpfungen (Google Login)
CREATE TABLE IF NOT EXISTS oauth_konten (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  benutzer_id UUID NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
  provider TEXT NOT NULL, -- 'google'
  provider_user_id TEXT NOT NULL,
  erstellt_am TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(provider, provider_user_id)
);
COMMENT ON TABLE oauth_konten IS 'Verknüpfung zu externen OAuth-Providern (z.B. Google).';

-- Passwort-Reset
CREATE TABLE IF NOT EXISTS passwort_reset_tokens (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  benutzer_id UUID NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
  token TEXT NOT NULL UNIQUE,
  ablauf_am TIMESTAMPTZ NOT NULL, -- Ablaufzeitpunkt (UTC)
  verwendet BOOLEAN NOT NULL DEFAULT FALSE,
  erstellt_am TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE passwort_reset_tokens IS 'Tokens für "Passwort vergessen" Workflow.';

-- Globale Rollen-Beziehung
CREATE TABLE IF NOT EXISTS benutzer_rollen (
  benutzer_id UUID NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
  rolle rollenname NOT NULL,
  PRIMARY KEY (benutzer_id, rolle)
);
COMMENT ON TABLE benutzer_rollen IS 'Globale Rollen (admin/manager/mitarbeiter).';

-- Teams (Registrierung per Team-Code)
CREATE TABLE IF NOT EXISTS teams (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name TEXT NOT NULL,
  code TEXT NOT NULL UNIQUE, -- z.B. RAUSCH-TEAM
  aktiv BOOLEAN NOT NULL DEFAULT TRUE,
  erstellt_am TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE teams IS 'Team für erweiterte Projekt-Oberfläche. Registrierung per Team-Code.';

CREATE TABLE IF NOT EXISTS team_mitglieder (
  team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  benutzer_id UUID NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
  rolle_im_team rollenname NOT NULL DEFAULT 'mitarbeiter',
  PRIMARY KEY (team_id, benutzer_id)
);
COMMENT ON TABLE team_mitglieder IS 'Mitgliedschaften und Team-Rollen.';

-- Unternehmen (Firmen/Kunden)
CREATE TABLE IF NOT EXISTS unternehmen (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name TEXT NOT NULL UNIQUE,
  zeitzone TEXT NOT NULL DEFAULT 'Europe/Berlin', -- optionale Firmen-Zeitzone
  aktiv BOOLEAN NOT NULL DEFAULT TRUE,
  erstellt_am TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE unternehmen IS 'Firmen/Kunden, denen Zeiten/Projekte zugeordnet werden.';

-- === "Einfache" Zeitbuchungen (User ohne Team-Code) =========================
CREATE TABLE IF NOT EXISTS zeitbuchungen (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  benutzer_id UUID NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
  unternehmen_id UUID NOT NULL REFERENCES unternehmen(id) ON DELETE RESTRICT,
  datum_local DATE NOT NULL, -- Datum in lokaler Logik des Benutzers
  stunden NUMERIC(6,2) NOT NULL CHECK (stunden >= 0),
  kommentar TEXT,
  bezahlt BOOLEAN NOT NULL DEFAULT FALSE, -- vom Admin gesetzt
  bezahlt_am TIMESTAMPTZ, -- UTC
  bezahlt_von UUID REFERENCES benutzer(id),
  erstellt_am TIMESTAMPTZ NOT NULL DEFAULT now(),
  aktualisiert_am TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE zeitbuchungen IS 'Einfache Stundenbuchungen (ohne Team-Projekt).';
COMMENT ON COLUMN zeitbuchungen.datum_local IS 'Kalenderdatum gemäß Benutzer-Zeitzone beim Erfassen.';

CREATE INDEX IF NOT EXISTS idx_zeitbuchungen_benutzer_datum
  ON zeitbuchungen (benutzer_id, datum_local DESC);

-- === Team-Projekte & Dashboard ==============================================
CREATE TABLE IF NOT EXISTS projekte (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  team_id UUID REFERENCES teams(id) ON DELETE SET NULL,
  unternehmen_id UUID NOT NULL REFERENCES unternehmen(id) ON DELETE RESTRICT,
  name TEXT NOT NULL,
  status projekt_status NOT NULL DEFAULT 'pause',
  starter_id UUID REFERENCES benutzer(id), -- wer angelegt hat
  start_am TIMESTAMPTZ NOT NULL DEFAULT now(),
  ende_am TIMESTAMPTZ, -- gesetzt bei 'beendet'
  erstellt_am TIMESTAMPTZ NOT NULL DEFAULT now(),
  aktualisiert_am TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE projekte IS 'Team-Projekte mit Status: pause/aktiv/beendet. Zeiten in UTC.';

CREATE INDEX IF NOT EXISTS idx_projekte_unternehmen_status
  ON projekte (unternehmen_id, status, start_am DESC);

-- Zuordnung von Mitarbeitern zu Projekten
CREATE TABLE IF NOT EXISTS projekt_mitglieder (
  projekt_id UUID NOT NULL REFERENCES projekte(id) ON DELETE CASCADE,
  benutzer_id UUID NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
  PRIMARY KEY (projekt_id, benutzer_id)
);
COMMENT ON TABLE projekt_mitglieder IS 'Welche Mitarbeiter an einem Projekt beteiligt sind.';

-- Arbeitssitzungen (Start/Stop; optional manuelle Eingabe)
CREATE TABLE IF NOT EXISTS arbeitssitzungen (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  projekt_id UUID NOT NULL REFERENCES projekte(id) ON DELETE CASCADE,
  benutzer_id UUID NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
  typ taetigkeit_typ NOT NULL, -- auswaertstermin/zeichnung/besprechung/anderes
  start_am TIMESTAMPTZ NOT NULL, -- UTC
  ende_am TIMESTAMPTZ, -- NULL = läuft
  manuell BOOLEAN NOT NULL DEFAULT FALSE, -- TRUE bei händischer Eingabe
  dauer_min INTEGER GENERATED ALWAYS AS (
                      CASE
                        WHEN ende_am IS NOT NULL THEN
                          GREATEST(0, CAST(EXTRACT(EPOCH FROM (ende_am - start_am)) / 60 AS INTEGER))
                        ELSE NULL
                      END
                    ) STORED,
  kommentar TEXT,
  erstellt_am TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE arbeitssitzungen IS 'Start/Stop-Sitzungen je Tätigkeit im Projekt (UTC).';
COMMENT ON COLUMN arbeitssitzungen.dauer_min IS 'Automatisch berechnete Dauer in Minuten, wenn Ende gesetzt.';

CREATE INDEX IF NOT EXISTS idx_arbeitssitzungen_projekt_benutzer
  ON arbeitssitzungen (projekt_id, benutzer_id);

-- === Admin-Funktionen: Zahlstatus & Erträge =================================

-- Zahlstatus pro Buchung ist in zeitbuchungen.bezaht* abgebildet.
-- Für Team-Projekte kann Auszahlung projekt-/mitarbeiterweise festgehalten werden:

CREATE TABLE IF NOT EXISTS projekt_auszahlungen (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  projekt_id UUID NOT NULL REFERENCES projekte(id) ON DELETE CASCADE,
  benutzer_id UUID NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
  bezahlt BOOLEAN NOT NULL DEFAULT FALSE,
  bezahlt_am TIMESTAMPTZ,
  bezahlt_von UUID REFERENCES benutzer(id),
  bemerkung TEXT
);
COMMENT ON TABLE projekt_auszahlungen IS 'Auszahlungsstatus je Mitarbeiter innerhalb eines Projekts.';

-- Einnahmen einer Firma in Zeitraum (für Gewinn-Report)
CREATE TABLE IF NOT EXISTS firmeneinnahmen (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  unternehmen_id UUID NOT NULL REFERENCES unternehmen(id) ON DELETE RESTRICT,
  zeitraum_von DATE NOT NULL,
  zeitraum_bis DATE NOT NULL,
  betrag_eur NUMERIC(12,2) NOT NULL CHECK (betrag_eur >= 0),
  erstellt_von UUID REFERENCES benutzer(id),
  erstellt_am TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE firmeneinnahmen IS 'Erfasste Einnahmen einer Firma für einen Zeitraum (für Gewinnberechnung).';

-- === Session-Management =====================================================
CREATE TABLE IF NOT EXISTS benutzer_sessions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  benutzer_id UUID NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL,
  erstellt_am TIMESTAMPTZ NOT NULL DEFAULT now(),
  ablauf_am TIMESTAMPTZ NOT NULL,
  aktiv BOOLEAN NOT NULL DEFAULT TRUE
);
COMMENT ON TABLE benutzer_sessions IS 'JWT-Session-Management für erweiterte Sicherheit.';

-- === Sichten (für Berichte) =================================================

-- Dauer pro Projekt/Mitarbeiter/Tätigkeit aggregiert
CREATE OR REPLACE VIEW v_projekt_aggregation AS
SELECT
  p.id AS projekt_id,
  p.name AS projekt_name,
  p.unternehmen_id,
  u.name AS unternehmen_name,
  a.benutzer_id,
  b.name AS benutzer_name,
  a.typ,
  COALESCE(SUM(a.dauer_min), 0) AS dauer_min_summe,
  MAKE_INTERVAL(mins => COALESCE(SUM(a.dauer_min),0)) AS dauer_intervall
FROM projekte p
JOIN unternehmen u ON u.id = p.unternehmen_id
LEFT JOIN arbeitssitzungen a ON a.projekt_id = p.id
LEFT JOIN benutzer b ON b.id = a.benutzer_id
GROUP BY p.id, p.name, p.unternehmen_id, u.name, a.benutzer_id, b.name, a.typ;

COMMENT ON VIEW v_projekt_aggregation IS
  'Aggregierte Dauer je Projekt/Benutzer/Tätigkeit. Grundlage für PDF-Berichte.';

-- Gesamtübersicht pro Projekt (Dauer aller Tätigkeiten)
CREATE OR REPLACE VIEW v_projekt_gesamt AS
SELECT
  p.id AS projekt_id,
  p.name AS projekt_name,
  u.name AS unternehmen_name,
  p.start_am,
  p.ende_am,
  COUNT(DISTINCT a.benutzer_id) AS anzahl_mitarbeiter,
  COALESCE(SUM(a.dauer_min),0) AS dauer_minuten_gesamt,
  MAKE_INTERVAL(mins => COALESCE(SUM(a.dauer_min),0)) AS dauer_intervall_gesamt
FROM projekte p
JOIN unternehmen u ON u.id = p.unternehmen_id
LEFT JOIN arbeitssitzungen a ON a.projekt_id = p.id
GROUP BY p.id, p.name, u.name, p.start_am, p.ende_am;

-- === Trigger: updated_at =====================================================
CREATE OR REPLACE FUNCTION set_aktualisiert_am()
RETURNS TRIGGER AS $$
BEGIN
  NEW.aktualisiert_am = now();
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_benutzer_updated ON benutzer;
CREATE TRIGGER trg_benutzer_updated
BEFORE UPDATE ON benutzer
FOR EACH ROW EXECUTE FUNCTION set_aktualisiert_am();

DROP TRIGGER IF EXISTS trg_zeitbuchungen_updated ON zeitbuchungen;
CREATE TRIGGER trg_zeitbuchungen_updated
BEFORE UPDATE ON zeitbuchungen
FOR EACH ROW EXECUTE FUNCTION set_aktualisiert_am();

DROP TRIGGER IF EXISTS trg_projekte_updated ON projekte;
CREATE TRIGGER trg_projekte_updated
BEFORE UPDATE ON projekte
FOR EACH ROW EXECUTE FUNCTION set_aktualisiert_am();

-- === Indizes für typische Abfragen ==========================================
CREATE INDEX IF NOT EXISTS idx_unternehmen_name ON unternehmen (name);
CREATE INDEX IF NOT EXISTS idx_projekt_status_sort ON projekte (status, start_am DESC);
CREATE INDEX IF NOT EXISTS idx_benutzer_sessions_benutzer ON benutzer_sessions (benutzer_id);
CREATE INDEX IF NOT EXISTS idx_benutzer_sessions_aktiv ON benutzer_sessions (aktiv, ablauf_am);