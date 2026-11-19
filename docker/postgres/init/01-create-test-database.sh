#!/bin/sh
# Creates the separate database used by the automated test suite.
# Runs once, when the Postgres data volume is first initialised.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE "${POSTGRES_TEST_DB:-credify_test}" OWNER "$POSTGRES_USER";
EOSQL
