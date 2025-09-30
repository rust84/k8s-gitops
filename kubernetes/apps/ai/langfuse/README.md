# Langfuse Deployment

## Required Doppler Secrets

### Langfuse Project
Create a Doppler project named `langfuse` with the following secrets:

#### Authentication Secrets
```
NEXTAUTH_SECRET=<random-32-char-string>
SALT=<random-salt-for-api-keys>
ENCRYPTION_KEY=<256-bit-hex-key>
```

#### Database Credentials
```
LANGFUSE_POSTGRES_USER=langfuse
LANGFUSE_POSTGRES_USER=<postgres-password>
POSTGRES_SUPER_PASS=<postgres-superuser-password>
CLICKHOUSE_PASSWORD=<clickhouse-password>
```

### ClickHouse Project
Create a Doppler project named `clickhouse` with:

```
CLICKHOUSE_PASSWORD=<same-password-as-above>
```

## Secret Generation Examples

```bash
# Generate NEXTAUTH_SECRET (32+ characters)
openssl rand -base64 32

# Generate SALT (random string)
openssl rand -base64 16

# Generate ENCRYPTION_KEY (64 hex characters = 256 bits)
openssl rand -hex 32

# Generate CLICKHOUSE_PASSWORD
openssl rand -base64 32
```

## Notes
- S3 storage is handled automatically by Rook ObjectBucketClaim
- Redis connection uses Dragonfly database index 6
- ClickHouse and PostgreSQL credentials must match between projects
- `POSTGRES_SUPER_PASS`: Get from `cloudnative-pg-secret` in database namespace
- The postgres-init container will create the `langfuse` database and user automatically
- Database URLs are constructed automatically from individual credential components