# Backend Production Deploy (EC2 + Nginx + Docker Compose)

## 1) Prerequisites on EC2
- Docker / Docker Compose plugin installed
- Nginx running on host
- Domain + SSL configured (Certbot)
- RDS PostgreSQL reachable from EC2 security group

## 2) Files and location
- Backend path: `/home/ubuntu/smart-research-manager/backend`
- Production compose file: `docker-compose.prod.yml`
- Production env file: `.env.prod` (create from `.env.prod.example`)

## 3) Create production env
```bash
cd /home/ubuntu/smart-research-manager/backend
cp .env.prod.example .env.prod
```

Fill all required values in `.env.prod`:
- `JWT_SECRET_KEY`
- `OPENAI_API_KEY`
- `FRONTEND_URL`
- `BACKEND_URL`
- `ALLOWED_ORIGINS`
- `DATABASE_URL` (RDS)

## 4) Run backend container
```bash
cd /home/ubuntu/smart-research-manager/backend
set -a
source .env.prod
set +a
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

Notes:
- Backend runs on host `127.0.0.1:5001` and is proxied by nginx `/api/`.
- `alembic upgrade head` is executed automatically in container start command.

## 5) Nginx proxy settings (host machine)
Your existing pattern is valid:
```nginx
location /api/ {
    proxy_pass http://localhost:5001;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_cache off;
    proxy_connect_timeout 300s;
    proxy_send_timeout 300s;
    proxy_read_timeout 300s;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    chunked_transfer_encoding on;
}
```

Reload nginx:
```bash
sudo nginx -t && sudo systemctl reload nginx
```

## 6) Verify
```bash
curl -i http://127.0.0.1:5001/api/auth/test
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f backend
```

## 7) Update workflow (no systemd)
```bash
cd /home/ubuntu/smart-research-manager/backend
git pull
set -a && source .env.prod && set +a
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

## 8) Rollback quick command
If you tag image versions, rollback by pointing compose image tag to previous version and run `up -d`.
