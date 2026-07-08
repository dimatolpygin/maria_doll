#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Установщик Telegram-бота платного клуба @Fit_it_bot (maria_doll) на чистой Ubuntu.
# Поднимает самодостаточный стек: бот + свой Postgres 16 + Redis в одном
# docker-compose. Данные — в именованных томах, автоперезапуск.
# Использование:  bash scripts/install.sh
# ─────────────────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
prompt()  { read -rp "$(echo -e "${YELLOW}>>> $1: ${NC}")" "$2"; }
promptp() { read -rsp "$(echo -e "${YELLOW}>>> $1: ${NC}")" "$2"; echo; }

echo ""
echo "=========================================================="
echo "   Бот платного клуба @Fit_it_bot (maria_doll) — установщик"
echo "=========================================================="
echo ""

# ── Сбор переменных ──────────────────────────────────────────────────────────
prompt  "Git репозиторий (https://github.com/...)" GIT_REPO
GIT_REPO=${GIT_REPO:-https://github.com/dimatolpygin/maria_doll.git}

prompt  "Ветка для деплоя [master]" BRANCH
BRANCH=${BRANCH:-master}

prompt  "Каталог установки [/opt/maria_doll]" INSTALL_DIR
INSTALL_DIR=${INSTALL_DIR:-/opt/maria_doll}

echo ""
promptp "BOT_TOKEN (от @BotFather)" BOT_TOKEN
prompt  "ADMIN_IDS (id админов через запятую)" ADMIN_IDS
prompt  "CLUB_CHAT_ID (числовой id супергруппы -100...)" CLUB_CHAT_ID
prompt  "CLUB_CHAT_INVITE_URL (инвайт-ссылка с заявкой на вступление)" CLUB_CHAT_INVITE_URL

echo ""
echo "Продамус (разовый платёж через payform, подтверждение вебхуком)."
prompt  "PRODAMUS_FORM_URL [https://dolbikfit.payform.ru]" PRODAMUS_FORM_URL
PRODAMUS_FORM_URL=${PRODAMUS_FORM_URL:-https://dolbikfit.payform.ru}
promptp "PRODAMUS_SECRET_KEY (секретный ключ платёжной страницы)" PRODAMUS_SECRET_KEY
prompt  "PRODAMUS_RETURN_URL (Enter — можно оставить пустым)" PRODAMUS_RETURN_URL

echo ""
echo "S3-хранилище для фото в рассылках и на экранах (необязательно; Enter — пропустить)."
prompt  "S3_ENDPOINT" S3_ENDPOINT
prompt  "S3_REGION" S3_REGION
prompt  "S3_BUCKET" S3_BUCKET
prompt  "S3_ACCESS_KEY" S3_ACCESS_KEY
promptp "S3_SECRET_KEY" S3_SECRET_KEY
prompt  "S3_PUBLIC_BASE_URL (обычно = S3_ENDPOINT)" S3_PUBLIC_BASE_URL

echo ""
echo "Веб-админка (dolbikfit.ru). Логин по умолчанию — admin."
prompt  "ADMIN_WEB_LOGIN [admin]" ADMIN_WEB_LOGIN
ADMIN_WEB_LOGIN=${ADMIN_WEB_LOGIN:-admin}
promptp "ADMIN_WEB_PASSWORD (Enter — сгенерировать)" ADMIN_WEB_PASSWORD

# Пароль встроенного Postgres генерируется автоматически.
POSTGRES_PASSWORD=$(openssl rand -hex 24)
# Секрет подписи cookie-сессии админки — всегда случайный.
ADMIN_WEB_SECRET=$(openssl rand -hex 32)
# Если пароль админки не задан — генерируем и покажем в конце.
ADMIN_WEB_PASSWORD=${ADMIN_WEB_PASSWORD:-$(openssl rand -base64 12 | tr -d '/+=' | head -c 16)}

echo ""
info "Начинаю установку..."

# ── Docker ───────────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  info "Устанавливаю Docker..."
  curl -fsSL https://get.docker.com | sh
fi
if ! docker compose version &>/dev/null; then
  error "Не найден плагин 'docker compose'. Установите docker-compose-plugin и повторите."
fi
info "Docker: $(docker --version)"

# ── git ──────────────────────────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
  info "Устанавливаю git..."
  apt-get update -qq && apt-get install -y -qq git
fi

# ── Клонирование / обновление ────────────────────────────────────────────────
if [[ -d "$INSTALL_DIR/.git" ]]; then
  warn "Каталог уже существует — обновляю..."
  cd "$INSTALL_DIR"
  git fetch --all
  git checkout "$BRANCH"
  git reset --hard "origin/$BRANCH"
else
  info "Клонирую репозиторий ($BRANCH)..."
  git clone --branch "$BRANCH" "$GIT_REPO" "$INSTALL_DIR"
  cd "$INSTALL_DIR"
fi

# ── .env ─────────────────────────────────────────────────────────────────────
# Если .env уже есть — сохраняем прежние секреты, чтобы не потерять данные/сессии.
if [[ -f .env ]] && grep -q '^POSTGRES_PASSWORD=' .env; then
  POSTGRES_PASSWORD=$(grep '^POSTGRES_PASSWORD=' .env | head -1 | cut -d= -f2-)
  warn "Использую существующий POSTGRES_PASSWORD из .env"
fi
if [[ -f .env ]] && grep -q '^ADMIN_WEB_SECRET=' .env; then
  ADMIN_WEB_SECRET=$(grep '^ADMIN_WEB_SECRET=' .env | head -1 | cut -d= -f2-)
  warn "Использую существующий ADMIN_WEB_SECRET из .env"
fi
if [[ -f .env ]] && grep -q '^ADMIN_WEB_PASSWORD=' .env; then
  ADMIN_WEB_PASSWORD=$(grep '^ADMIN_WEB_PASSWORD=' .env | head -1 | cut -d= -f2-)
fi

info "Создаю .env (боевые интервалы)..."
cat > .env <<ENVEOF
# Telegram
BOT_TOKEN=${BOT_TOKEN}
ADMIN_IDS=${ADMIN_IDS}
CLUB_CHAT_ID=${CLUB_CHAT_ID}
CLUB_CHAT_INVITE_URL=${CLUB_CHAT_INVITE_URL}

# База данных (встроенный Postgres)
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
DB_SCHEMA=maria_doll

# Продамус (разовый платёж, подтверждение вебхуком)
PRODAMUS_FORM_URL=${PRODAMUS_FORM_URL}
PRODAMUS_SECRET_KEY=${PRODAMUS_SECRET_KEY}
PRODAMUS_RETURN_URL=${PRODAMUS_RETURN_URL}
PRODAMUS_DEMO_MODE=false
PRODAMUS_RECURRENT_ENABLED=false
PRODAMUS_WEBHOOK_PORT=8080
PRODAMUS_WEBHOOK_PATH=/prodamus/webhook

# Проверка окончаний подписок (минуты)
EXPIRY_CHECK_INTERVAL_MIN=30

# Напоминания о продлении (боевые: проверка раз в час, пороги 3/1/0 дня)
REMINDER_CHECK_INTERVAL_MIN=60
REMINDER_UNIT=day
REMINDER_EARLY_OFFSET=3
REMINDER_SOON_OFFSET=1
REMINDER_LAST_OFFSET=0

# Рассылки: как часто бот забирает очередь из админки (секунды)
BROADCAST_CHECK_INTERVAL_SEC=30

# S3-хранилище (фото в рассылках и на экранах бота)
S3_ENDPOINT=${S3_ENDPOINT}
S3_REGION=${S3_REGION}
S3_BUCKET=${S3_BUCKET}
S3_ACCESS_KEY=${S3_ACCESS_KEY}
S3_SECRET_KEY=${S3_SECRET_KEY}
S3_PUBLIC_BASE_URL=${S3_PUBLIC_BASE_URL}

# Веб-админка (этап 6)
ADMIN_WEB_SECRET=${ADMIN_WEB_SECRET}
ADMIN_WEB_LOGIN=${ADMIN_WEB_LOGIN}
ADMIN_WEB_PASSWORD=${ADMIN_WEB_PASSWORD}

# Прочее
LOG_LEVEL=INFO
ENVEOF
chmod 600 .env

# ── Запуск ───────────────────────────────────────────────────────────────────
info "Собираю и запускаю стек (бот + Postgres + Redis + админка)..."
docker compose up -d --build

echo ""
echo "=========================================================="
info "Установка завершена!"
echo ""
echo "  Каталог:   ${INSTALL_DIR}"
echo "  Логи:      docker compose -f ${INSTALL_DIR}/docker-compose.yml logs -f bot"
echo "  Рестарт:   docker compose -f ${INSTALL_DIR}/docker-compose.yml restart"
echo "  Стоп:      docker compose -f ${INSTALL_DIR}/docker-compose.yml down"
echo ""
echo "  Веб-админка: http://127.0.0.1:8011 (наружу — Nginx на https://dolbikfit.ru)"
echo "    Логин:  ${ADMIN_WEB_LOGIN}"
echo "    Пароль: ${ADMIN_WEB_PASSWORD}"
echo ""
echo "  Nginx + https (один раз):"
echo "    cp deploy/nginx/dolbikfit.ru.conf /etc/nginx/sites-available/dolbikfit.ru"
echo "    ln -sf /etc/nginx/sites-available/dolbikfit.ru /etc/nginx/sites-enabled/"
echo "    rm -f /etc/nginx/sites-enabled/default"
echo "    nginx -t && systemctl reload nginx && certbot --nginx -d dolbikfit.ru -d www.dolbikfit.ru"
echo ""
echo "  Продамус: в ЛК прописать «URL для уведомлений»:"
echo "    https://dolbikfit.ru/prodamus/webhook"
echo ""
warn "Не забудьте сделать бота администратором группы ${CLUB_CHAT_ID} (право одобрять заявки и банить)!"
echo "=========================================================="
