#!/bin/bash
set -e

echo "Creating database pili_crm_test..."
docker exec pili-crm-postgres-1 psql -U pili -d pili_crm \
  -c "CREATE DATABASE pili_crm_test OWNER pili" \
  || echo "(database already exists, continuing)"

echo "Running migrations on pili_crm_test..."
DATABASE_URL=postgresql+asyncpg://pili:pili@localhost:5432/pili_crm_test \
  python3 -m alembic upgrade head

echo "Seeding pili_crm_test..."
DATABASE_URL=postgresql+asyncpg://pili:pili@localhost:5432/pili_crm_test \
  python3 -m scripts.seed_mvp

echo "Done. Add to your .env file:"
echo "TEST_DATABASE_URL=postgresql+asyncpg://pili:pili@localhost:5432/pili_crm_test"
