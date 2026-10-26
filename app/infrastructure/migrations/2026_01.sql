CREATE TABLE artists (
    id uuid PRIMARY KEY, name varchar(200) NOT NULL CHECK (length(btrim(name)) > 0),
    description text, primary_genre_code text, image_url text,
    created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
    CHECK (primary_genre_code IS NULL OR primary_genre_code IN
        ('concert','theatre','standup','lecture','exhibition','festival','cinema','workshop','excursion'))
);
CREATE TABLE venues (
    id uuid PRIMARY KEY, name varchar(200) NOT NULL CHECK (length(btrim(name)) > 0),
    city text NOT NULL CHECK (city = 'Москва'), district_code text NOT NULL CHECK (district_code IN
        ('CAO','SAO','SVAO','VAO','UVAO','UAO','UZAO','ZAO','SZAO','ZELAO','TAO','NAO')),
    street text NOT NULL CHECK (length(btrim(street)) > 0),
    house text NOT NULL CHECK (length(btrim(house)) > 0),
    formatted_address text NOT NULL CHECK (length(btrim(formatted_address)) > 0),
    location geography(Point,4326), capacity integer NOT NULL CHECK (capacity > 0), description text,
    created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL
);
CREATE INDEX venues_location_gix ON venues USING gist(location);
CREATE INDEX venues_district_idx ON venues(district_code);
CREATE INDEX venues_capacity_idx ON venues(capacity);
CREATE INDEX artists_genre_idx ON artists(primary_genre_code);
CREATE TABLE series (
    id uuid PRIMARY KEY, name varchar(200) NOT NULL CHECK (length(btrim(name)) > 0), description text,
    created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL
);
CREATE TABLE events (
    id uuid PRIMARY KEY, title varchar(250) NOT NULL CHECK (length(btrim(title)) > 0), description text,
    artist_id uuid NOT NULL REFERENCES artists(id) ON DELETE RESTRICT,
    venue_id uuid NOT NULL REFERENCES venues(id) ON DELETE RESTRICT,
    series_id uuid REFERENCES series(id) ON DELETE RESTRICT,
    genre_code text NOT NULL CHECK (genre_code IN
        ('concert','theatre','standup','lecture','exhibition','festival','cinema','workshop','excursion')),
    starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL CHECK (ends_at > starts_at),
    price_rub bigint NOT NULL CHECK (price_rub >= 0),
    age_rating integer NOT NULL CHECK (age_rating IN (0,6,12,16,18)),
    ticket_quota integer NOT NULL CHECK (ticket_quota > 0),
    status text NOT NULL CHECK (status IN ('draft','published','cancelled','archived')),
    tags text[] NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL
);
CREATE INDEX events_artist_idx ON events(artist_id);
CREATE INDEX events_venue_idx ON events(venue_id);
CREATE INDEX events_series_idx ON events(series_id);
CREATE INDEX events_starts_idx ON events(starts_at, id);
CREATE INDEX events_status_starts_idx ON events(status, starts_at, id);
CREATE INDEX events_genre_idx ON events(genre_code);
CREATE INDEX events_price_idx ON events(price_rub);
CREATE INDEX events_age_idx ON events(age_rating);
CREATE INDEX events_created_idx ON events(created_at);
CREATE TABLE reservations (
    id uuid PRIMARY KEY, event_id uuid NOT NULL REFERENCES events(id) ON DELETE RESTRICT,
    quantity integer NOT NULL CHECK (quantity > 0),
    unit_price_rub bigint NOT NULL CHECK (unit_price_rub >= 0),
    total_price_rub bigint NOT NULL CHECK (total_price_rub = quantity::bigint * unit_price_rub),
    status text NOT NULL CHECK (status IN ('confirmed','cancelled')),
    booked_at timestamptz NOT NULL, cancelled_at timestamptz,
    created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
    CHECK ((status = 'confirmed' AND cancelled_at IS NULL) OR (status = 'cancelled' AND cancelled_at IS NOT NULL))
);
CREATE INDEX reservations_event_idx ON reservations(event_id);
CREATE INDEX reservations_confirmed_event_idx ON reservations(event_id) INCLUDE (quantity) WHERE status = 'confirmed';
CREATE INDEX reservations_booked_idx ON reservations(booked_at, id);
CREATE INDEX reservations_status_idx ON reservations(status);
CREATE TABLE dataset_metadata (
    singleton integer PRIMARY KEY DEFAULT 1 CHECK (singleton = 1), schema_version text NOT NULL,
    dataset_id uuid NOT NULL UNIQUE, dataset_kind text NOT NULL CHECK (dataset_kind IN ('sample','student_created','imported')),
    sample_seed_version text, reference_time timestamptz NOT NULL, created_at timestamptz NOT NULL,
    source_dataset_id uuid, description text
);
CREATE TABLE idempotency (
    scope text NOT NULL, key varchar(200) NOT NULL, fingerprint text NOT NULL, response jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (scope, key)
);
