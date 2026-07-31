#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq postgresql postgresql-contrib
service postgresql start
VERSION=$(ls /etc/postgresql | head -n1)
CONF="/etc/postgresql/$VERSION/main/postgresql.conf"
HBA="/etc/postgresql/$VERSION/main/pg_hba.conf"
sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '*'/" "$CONF"
sed -i "s/listen_addresses = 'localhost'/listen_addresses = '*'/" "$CONF"
grep -q "0.0.0.0/0" "$HBA" || echo "host all all 0.0.0.0/0 md5" >> "$HBA"
service postgresql restart
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='poem'" | grep -q 1 || sudo -u postgres psql -c "CREATE USER poem WITH PASSWORD 'poem' SUPERUSER;"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='poem_db'" | grep -q 1 || sudo -u postgres psql -c "CREATE DATABASE poem_db OWNER poem;"
sudo -u postgres psql -c "ALTER USER poem WITH PASSWORD 'poem';"
ss -lptn | grep 5432 || netstat -lptn 2>/dev/null | grep 5432 || true
sudo -u postgres psql -c "SELECT version();"
echo SETUP_DONE
