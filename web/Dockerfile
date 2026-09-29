# Rastro — viewer: serve os assets estáticos gerados pelo Vite (D8).
# Build local: cd web && pnpm build
FROM nginx:1.27-alpine
COPY dist /usr/share/nginx/html
RUN chmod -R a+rX /usr/share/nginx/html
RUN printf 'server { listen 80; root /usr/share/nginx/html; index index.html; location / { try_files $uri $uri/ /index.html; } }\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80
