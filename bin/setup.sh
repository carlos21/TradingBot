#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "=== Liquid Setup ==="

# Detect OS
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="mac"
elif grep -qi microsoft /proc/version 2>/dev/null; then
    OS="wsl"
else
    OS="linux"
fi
echo "Detected OS: $OS"

# 1. Install Poetry if missing
if ! command -v poetry &>/dev/null; then
    echo "Installing Poetry..."
    curl -sSL https://install.python-poetry.org | python3 -
fi

# 2. Install Python dependencies
echo "Installing Python dependencies..."
poetry install --no-root

# 3. Install Playwright browsers
echo "Installing Playwright browsers..."
poetry run playwright install chromium

# 4. Install Playwright system dependencies (needs sudo on Linux/WSL)
if [[ "$OS" == "wsl" || "$OS" == "linux" ]]; then
    echo "Installing Playwright system dependencies (requires sudo)..."
    sudo poetry run playwright install-deps chromium
elif [[ "$OS" == "mac" ]]; then
    echo "macOS: Playwright system deps are bundled with the browser download."
fi

# 5. Install PostgreSQL and create the application database
if [[ "$OS" == "wsl" || "$OS" == "linux" ]]; then
    echo "Installing PostgreSQL..."
    sudo apt-get update
    sudo apt-get install -y postgresql postgresql-contrib libpq-dev

    echo "Starting PostgreSQL..."
    sudo systemctl enable postgresql
    sudo systemctl start postgresql

    # Generate a password for the tradingbot DB user if one is not already in .env
    PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    ENV_FILE="$PROJECT_DIR/.env"
    EXISTING_URL=""
    if [[ -f "$ENV_FILE" ]]; then
        EXISTING_URL=$(grep -E '^DATABASE_URL=' "$ENV_FILE" | cut -d'"' -f2 || true)
    fi

    if [[ -n "$EXISTING_URL" ]]; then
        echo "DATABASE_URL already set in .env; skipping PostgreSQL user/database creation."
    else
        DB_PASSWORD=$(python3 -c "import secrets, string; print(''.join(secrets.choice(string.ascii_letters + string.digits + '_') for _ in range(24)))")
        DB_NAME="tradingbot"
        DB_USER="tradingbot"

        echo "Creating PostgreSQL user and database..."
        sudo -u postgres psql <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DB_USER') THEN
        CREATE ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASSWORD';
    END IF;
END
\$\$;

-- Ensure the database exists and is owned by the tradingbot role
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS $DB_NAME;
CREATE DATABASE $DB_NAME OWNER $DB_USER;
GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;
SQL

        # Write DATABASE_URL to .env
        {
            echo ""
            echo "# PostgreSQL database URL"
            echo "DATABASE_URL=\"postgresql+psycopg://$DB_USER:$DB_PASSWORD@localhost/$DB_NAME\""
        } >> "$ENV_FILE"

        echo "PostgreSQL configured. DATABASE_URL written to .env."
    fi

    # Ensure the database driver is installed
    echo "Installing PostgreSQL Python driver..."
    poetry install --extras database
fi

# 6. Fix line endings on shell scripts (in case of Windows checkout)
if command -v sed &>/dev/null; then
    echo "Fixing line endings in bin/*.sh..."
    sed -i'' -e 's/\r$//' bin/*.sh 2>/dev/null || true
fi

echo ""
echo "=== Setup complete! ==="
echo "Run the app:        poetry run python backend/run.py"
echo "Run scenarios:      ./bin/run_scenarios.sh"
