# Rastro — viewer AUTOCONTIDO para CapRover (D8): a imagem embute os assets do
# Vite e o próprio caddy, que serve a SPA, o /config.json de runtime, o volume
# /tiles/* e faz proxy de /api/* para a API. Uma imagem só = deploy CapRover
# sem serviço caddy separado (como há no perfil docker-compose).
#
# Contexto de build = web/        (docker build web/)
# Env em runtime:
#   RASTRO_TITLE="Mapa da Ilha"                     opcional (default "Rastro")
#   RASTRO_API_UPSTREAM=srv-captain--rastro-api:8080 obrigatório
#
# O nginx do CapRover termina o TLS na frente e fala HTTP/1.1 com o contêiner,
# marcando X-Forwarded-Proto — daí auto_https off + trusted_proxies no Caddyfile.

# --- estágio 1: build dos assets estáticos (Vite) --------------------------
# Imagem travada por digest do índice multi-arch (digest lido no registry).
FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS build
WORKDIR /app
# corepack fornece o pnpm (série 9, dona do pnpm-lock.yaml v9) sem nada
# instalado no host; sem prompt interativo de download.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable && corepack prepare pnpm@9 --activate
# Manifests primeiro: esta camada só refaz quando as dependências mudam.
COPY package.json pnpm-lock.yaml .npmrc ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

# --- estágio 2: runtime (caddy) -------------------------------------------
# Mesmo digest de deploy/docker-compose.yml (reprodutibilidade entre perfis).
FROM caddy:2-alpine@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b AS run

# A imagem base não tem usuário dedicado: cria um fixo — o CapRover levanta o
# contêiner sem --user, então o drop de privilégio tem de estar na imagem.
RUN adduser -D -H -u 10001 rastro \
 && mkdir -p /srv/tiles /tmp/caddy/data /tmp/caddy/config \
 && chown -R rastro:rastro /tmp/caddy

# O caddy exige um diretório gravável (config/CA/certificados/locks): vai para
# /tmp, que o usuário rastro pode escrever; /srv/www fica read-only (dono root).
ENV XDG_DATA_HOME=/tmp/caddy/data \
    XDG_CONFIG_HOME=/tmp/caddy/config

COPY --from=build /app/dist /srv/www
RUN chmod -R a-w /srv/www && chmod -R a+rX /srv/www

COPY caddy/Caddyfile /etc/caddy/Caddyfile
COPY caddy/entrypoint.sh /usr/local/bin/rastro-web-entrypoint
RUN chmod 0555 /usr/local/bin/rastro-web-entrypoint

USER rastro
EXPOSE 80
ENTRYPOINT ["/usr/local/bin/rastro-web-entrypoint"]

