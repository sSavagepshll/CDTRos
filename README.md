# Portal de cadastro de produtos

Aplicação web de exemplo para receber solicitações de cadastro de produtos, anexar imagens, acompanhar os pedidos em um painel administrativo e enviar atualizações por email. O projeto usa Flask, PostgreSQL, Redis, Docker Compose e um proxy Nginx com HTTPS.

## Funcionalidades

- Formulário público com dados de contato e até cinco itens por solicitação.
- Resumo, detalhes e imagem obrigatória por item (JPG, PNG ou WEBP, até 10 MB cada).
- Identificador único para cada solicitação.
- Painel com autenticação para acompanhar solicitações pendentes e concluídas.
- Registro manual do nome e código do produto no sistema de destino, com email de confirmação.
- Indicadores de desempenho e notificações por SMTP.
- Proteções como CSRF, limitação de tentativas de login, cookies seguros, CSP, validação de upload e execução do container da aplicação sem privilégios.

## Requisitos

- Docker Engine e Docker Compose v2.
- OpenSSL para gerar o certificado local de desenvolvimento.
- Um servidor SMTP para enviar notificações.

## Configuração e execução

1. Crie sua configuração local e defina valores fortes e exclusivos:

   ```sh
   cp .env.example .env
   ```

   Edite `.env` antes de iniciar. Substitua `SECRET_KEY`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` e `POSTGRES_PASSWORD`; configure também os dados de SMTP. Gere segredos com `openssl rand -hex 32`. O email administrativo recebe os alertas para a equipe; as confirmações são enviadas ao email informado pelo solicitante.

2. Gere um certificado autoassinado para desenvolvimento local:

   ```sh
   mkdir -p tls
   openssl req -x509 -newkey rsa:3072 -sha256 -days 365 -nodes \
     -keyout tls/portfolio.key -out tls/portfolio.crt \
     -subj "/CN=localhost" \
     -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
   ```

   Para acessar de outros computadores, gere um certificado cujo `subjectAltName` inclua o nome DNS ou IP usado pelos clientes. Certificados autoassinados exibem um aviso no navegador e não substituem certificados emitidos por uma autoridade confiável.

3. Inicie a aplicação:

   ```sh
   docker compose up --build -d
   docker compose ps
   ```

   Acesse `https://localhost:8443`. Se essa porta já estiver em uso, defina outra em `.env`, por exemplo `HTTPS_PORT=9443`, e use a porta escolhida na URL.

4. Acompanhe a execução e encerre os serviços:

   ```sh
   docker compose logs -f app https_proxy
   docker compose down
   ```

Os dados do PostgreSQL e as imagens ficam em volumes Docker. `docker compose down` mantém esses dados; remova-os somente se quiser apagar permanentemente o conteúdo local.

## Variáveis de ambiente

Consulte `.env.example` para a lista completa. O Compose configura internamente os hosts de PostgreSQL e Redis; não publique as portas desses serviços na rede. `COOKIE_SECURE=1` pressupõe o acesso por HTTPS via proxy. O HSTS permanece desativado por padrão, especialmente importante ao usar um certificado autoassinado.

## Segurança e publicação

- Não envie `.env`, certificados/chaves privados, uploads ou dados reais de usuários ao GitHub.
- O `.env.example` contém apenas valores demonstrativos; nunca use esses valores em produção.
- Proteja o acesso ao painel com uma senha forte e mantenha Docker, imagens e dependências atualizados.
- Configure HTTPS com certificado confiável e revise os requisitos de infraestrutura, backup, retenção e privacidade antes de uso real.
- A imagem Docker da aplicação não contém segredos. As credenciais são injetadas pelo arquivo `.env` em tempo de execução.

## Estrutura

```text
app.py                  Aplicação Flask, modelos e rotas
entrypoint.py           Espera PostgreSQL e Redis antes de iniciar
compose.yaml            Aplicação, PostgreSQL, Redis e proxy HTTPS
deploy/nginx-https.conf Configuração do proxy reverso
templates/              Páginas e modelos de email
static/                 Estilos, scripts e marca genérica
```
