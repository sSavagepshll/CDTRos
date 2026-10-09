import io
import logging
import os
import re
import smtplib
import uuid
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from email_validator import EmailNotValidError, validate_email
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import URL
from werkzeug.security import check_password_hash, generate_password_hash


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger(__name__)

MAX_ITEMS = 5
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = 52 * 1024 * 1024
MAX_PRODUCT_NAME_LENGTH = 200
MAX_PRODUCT_CODE_LENGTH = 100
Image.MAX_IMAGE_PIXELS = 20_000_000

app = Flask(__name__)
database_url = os.environ.get("DATABASE_URL")
if not database_url and all(os.environ.get(key) for key in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")):
    database_url = URL.create(
        "postgresql+psycopg",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ.get("POSTGRES_HOST", "db"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ["POSTGRES_DB"],
    )
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", ""),
    SQLALCHEMY_DATABASE_URI=database_url or "",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES,
    UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER", "/app/uploads"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
    SESSION_COOKIE_SAMESITE="Lax",
    HSTS_ENABLED=os.environ.get("HSTS_ENABLED", "0") == "1",
)

if len(app.config["SECRET_KEY"]) < 32:
    raise RuntimeError("SECRET_KEY deve conter pelo menos 32 caracteres.")
if not app.config["SQLALCHEMY_DATABASE_URI"]:
    raise RuntimeError("Configure DATABASE_URL antes de iniciar a aplicação.")


@app.context_processor
def inject_asset_version():
    styles_path = os.path.join(app.static_folder, "styles.css")
    return {"asset_version": int(os.path.getmtime(styles_path))}


db = SQLAlchemy(app)
csrf = CSRFProtect(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
    default_limits=[],
)

class RegistrationRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    requester_name = db.Column(db.String(120), nullable=False)
    requester_email = db.Column(db.String(254), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    items = db.relationship(
        "RequestedItem",
        back_populates="registration",
        cascade="all, delete-orphan",
        order_by="RequestedItem.id",
    )

    @property
    def public_id(self):
        return f"REQ-{self.created_at.year}-{self.id:06d}"

    @property
    def is_complete(self):
        return bool(self.items) and all(item.status == "registered" for item in self.items)


class RequestedItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    registration_id = db.Column(
        db.Integer, db.ForeignKey("registration_request.id", ondelete="CASCADE"), nullable=False
    )
    summary = db.Column(db.String(200), nullable=False)
    details = db.Column(db.Text, nullable=False)
    image_filename = db.Column(db.String(80), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", server_default=text("'pending'"))
    registered_at = db.Column(db.DateTime(timezone=True))
    product_name = db.Column(db.String(MAX_PRODUCT_NAME_LENGTH))
    product_code = db.Column(db.String(MAX_PRODUCT_CODE_LENGTH))
    confirmation_sent_at = db.Column(db.DateTime(timezone=True))
    registered_by_admin_id = db.Column(db.Integer, db.ForeignKey("admin.id", ondelete="SET NULL"))
    registration = db.relationship("RegistrationRequest", back_populates="items")
    registered_by = db.relationship("Admin", foreign_keys=[registered_by_admin_id])


class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)


def normalize_email(value):
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized
    except EmailNotValidError:
        return None


def process_image(upload):
    if not upload or not upload.filename:
        raise ValueError("Envie uma imagem para cada item.")

    content = upload.stream.read(MAX_IMAGE_BYTES + 1)
    upload.stream.seek(0)
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Cada imagem deve ter no máximo 10 MB.")

    try:
        with Image.open(io.BytesIO(content)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Use imagens nos formatos JPG, PNG ou WEBP.")
            if source.width * source.height > Image.MAX_IMAGE_PIXELS:
                raise ValueError("A imagem excede o limite de resolução permitido.")
            source.load()
            image = ImageOps.exif_transpose(source)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.thumbnail((2000, 2000))
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=85, method=6)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("O arquivo enviado não é uma imagem válida.") from exc


def send_notification(recipient, subject, body, html_body, reply_to):
    host = os.environ.get("SMTP_HOST", "").strip()
    sender = os.environ.get("SMTP_FROM", "").strip()
    if not host or not sender:
        raise RuntimeError("SMTP_HOST e SMTP_FROM precisam estar configurados.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message["Reply-To"] = reply_to
    message.set_content(body)
    message.add_alternative(html_body, subtype="html")

    port = int(os.environ.get("SMTP_PORT", "587"))
    timeout = 15
    if os.environ.get("SMTP_USE_SSL", "0") == "1":
        with smtplib.SMTP_SSL(host, port, timeout=timeout) as server:
            _send_smtp_message(server, message)
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as server:
            server.ehlo()
            if os.environ.get("SMTP_USE_TLS", "1") == "1":
                server.starttls()
                server.ehlo()
            _send_smtp_message(server, message)


def _send_smtp_message(server, message):
    username = os.environ.get("SMTP_USERNAME", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    if username:
        server.login(username, password)
    server.send_message(message)


def build_registration_emails(registration):
    local_time = registration.created_at.astimezone(ZoneInfo("America/Sao_Paulo"))
    items = [
        {
            "number": number,
            "summary": item.summary,
            "details": item.details,
        }
        for number, item in enumerate(registration.items, start=1)
    ]
    context = {
        "request_id": registration.public_id,
        "requester_name": registration.requester_name,
        "requester_email": registration.requester_email,
        "created_at": local_time.strftime("%d/%m/%Y às %H:%M"),
        "item_count": len(items),
        "items": items,
        "admin_email": os.environ.get("ADMIN_EMAIL", ""),
    }

    team_email = {
        "recipient": context["admin_email"],
        "subject": f"Nova solicitação de cadastro {registration.public_id}",
        "body": render_template("emails/team_notification.txt", **context),
        "html_body": render_template("emails/team_notification.html", **context),
        "reply_to": registration.requester_email,
    }
    requester_email = {
        "recipient": registration.requester_email,
        "subject": f"Recebemos sua solicitação {registration.public_id}",
        "body": render_template("emails/confirmation.txt", **context),
        "html_body": render_template("emails/confirmation.html", **context),
        "reply_to": context["admin_email"],
    }
    return team_email, requester_email


def build_product_confirmation(item, item_number):
    registration = item.registration
    registered_at = item.registered_at.astimezone(ZoneInfo("America/Sao_Paulo"))
    context = {
        "request_id": registration.public_id,
        "requester_name": registration.requester_name,
        "requester_email": registration.requester_email,
        "registered_at": registered_at.strftime("%d/%m/%Y às %H:%M"),
        "item_number": item_number,
        "item_summary": item.summary,
        "item_details": item.details,
        "product_name": item.product_name,
        "product_code": item.product_code,
        "admin_email": os.environ.get("ADMIN_EMAIL", ""),
    }
    return {
        "recipient": registration.requester_email,
        "subject": f"Cadastro no sistema de destino confirmado · {item.product_code} · {registration.public_id}",
        "body": render_template("emails/product_confirmation.txt", **context),
        "html_body": render_template("emails/product_confirmation.html", **context),
        "reply_to": context["admin_email"],
    }


def admin_required(view):
    from flask import session

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@app.get("/")
def index():
    return render_template("index.html", max_items=MAX_ITEMS)


@app.get("/solicitacao/concluida")
def request_complete():
    completion = session.pop("request_completion", None)
    if not completion:
        return redirect(url_for("index"))
    return render_template("request_complete.html", completion=completion)


@app.post("/solicitacoes")
@limiter.limit("10 per hour")
def create_request():
    name = request.form.get("requester_name", "").strip()
    email = normalize_email(request.form.get("requester_email", ""))
    try:
        item_count = int(request.form.get("item_count", ""))
    except ValueError:
        item_count = 0

    if not name or len(name) > 120:
        flash("Informe seu nome completo (até 120 caracteres).", "error")
        return redirect(url_for("index"))
    if not email:
        flash("Informe um endereço de email válido.", "error")
        return redirect(url_for("index"))
    if not 1 <= item_count <= MAX_ITEMS:
        flash("A solicitação deve conter entre 1 e 5 itens.", "error")
        return redirect(url_for("index"))

    item_data = []
    try:
        for index in range(item_count):
            summary = request.form.get(f"item_summary_{index}", "").strip()
            details = request.form.get(f"item_details_{index}", "").strip()
            if not summary or len(summary) > 200:
                raise ValueError(f"Informe um resumo válido para o item {index + 1} (até 200 caracteres).")
            if not details or len(details) > 5000:
                raise ValueError(f"Informe os detalhes do item {index + 1} (até 5.000 caracteres).")
            upload = request.files.get(f"item_image_{index}")
            if not upload or not upload.filename:
                raise ValueError(f"Envie uma imagem para o item {index + 1}.")
            item_data.append(
                {
                    "summary": summary,
                    "details": details,
                    "image": process_image(upload),
                }
            )
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("index"))

    saved_files = []
    try:
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        registration = RegistrationRequest(requester_name=name, requester_email=email)
        for data in item_data:
            filename = f"{uuid.uuid4().hex}.webp"
            with open(os.path.join(app.config["UPLOAD_FOLDER"], filename), "wb") as image_file:
                image_file.write(data["image"])
            saved_files.append(filename)
            registration.items.append(
                RequestedItem(
                    summary=data["summary"],
                    details=data["details"],
                    image_filename=filename,
                )
            )
        db.session.add(registration)
        db.session.commit()
    except (SQLAlchemyError, OSError):
        db.session.rollback()
        for filename in saved_files:
            try:
                os.remove(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            except FileNotFoundError:
                pass
        logger.exception("Falha ao salvar solicitação no banco de dados.")
        flash("Não foi possível salvar a solicitação. Tente novamente mais tarde.", "error")
        return redirect(url_for("index"))

    email_errors = []
    for notification in build_registration_emails(registration):
        try:
            if not notification["recipient"]:
                raise RuntimeError("ADMIN_EMAIL não está configurado.")
            send_notification(**notification)
        except (OSError, smtplib.SMTPException, RuntimeError, ValueError) as exc:
            email_errors.append(str(exc))
            logger.exception(
                "Falha ao enviar email da solicitação %s para %s.",
                registration.public_id,
                notification["recipient"] or "(destinatário não configurado)",
            )

    session["request_completion"] = {
        "request_id": registration.public_id,
        "item_count": len(registration.items),
        "email_sent": not email_errors,
    }
    return redirect(url_for("request_complete"))


@app.route("/painel/entrar", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))
        admin = db.session.scalar(select(Admin).where(Admin.email == email)) if email else None
        password = request.form.get("password", "")
        if admin and check_password_hash(admin.password_hash, password):
            from flask import session

            session.clear()
            session["admin_id"] = admin.id
            next_url = request.args.get("next", "")
            target = urlsplit(next_url)
            if (
                not target.path.startswith("/")
                or target.path.startswith("//")
                or target.scheme
                or target.netloc
                or "\\" in target.path
            ):
                next_url = url_for("dashboard")
            return redirect(next_url)
        flash("Email ou senha inválidos.", "error")
    return render_template("login.html")


@app.post("/painel/sair")
@admin_required
def logout():
    from flask import session

    session.clear()
    flash("Você saiu do painel.", "success")
    return redirect(url_for("login"))


@app.get("/painel")
@admin_required
def dashboard():
    status = request.args.get("status", "pending")
    if status not in {"pending", "completed"}:
        abort(400)

    pending_filter = RegistrationRequest.items.any(RequestedItem.status != "registered")
    completed_filter = (
        RegistrationRequest.items.any()
        & ~RegistrationRequest.items.any(RequestedItem.status != "registered")
    )
    pending_count = db.session.scalar(
        select(func.count(RegistrationRequest.id)).where(pending_filter)
    ) or 0
    completed_count = db.session.scalar(
        select(func.count(RegistrationRequest.id)).where(completed_filter)
    ) or 0
    selected_filter = pending_filter if status == "pending" else completed_filter
    registrations = db.session.scalars(
        select(RegistrationRequest)
        .where(selected_filter)
        .order_by(RegistrationRequest.created_at.desc())
    ).all()
    return render_template(
        "dashboard.html",
        registrations=registrations,
        status=status,
        pending_count=pending_count,
        completed_count=completed_count,
    )


@app.get("/painel/desempenho")
@admin_required
def performance_dashboard():
    local_timezone = ZoneInfo("America/Sao_Paulo")
    today = datetime.now(local_timezone).date()
    start_day = today - timedelta(days=29)
    start_utc = datetime.combine(start_day, time.min, tzinfo=local_timezone).astimezone(timezone.utc)
    end_utc = datetime.combine(today + timedelta(days=1), time.min, tzinfo=local_timezone).astimezone(timezone.utc)
    local_date = func.date(func.timezone("America/Sao_Paulo", RegistrationRequest.created_at))
    received_rows = db.session.execute(
        select(local_date, func.count(RegistrationRequest.id))
        .where(RegistrationRequest.created_at >= start_utc, RegistrationRequest.created_at < end_utc)
        .group_by(local_date)
    ).all()

    registered_date = func.date(func.timezone("America/Sao_Paulo", RequestedItem.registered_at))
    registered_rows = db.session.execute(
        select(registered_date, func.count(RequestedItem.id))
        .where(
            RequestedItem.status == "registered",
            RequestedItem.registered_at >= start_utc,
            RequestedItem.registered_at < end_utc,
        )
        .group_by(registered_date)
    ).all()

    received_by_day = {day: count for day, count in received_rows}
    registered_by_day = {day: count for day, count in registered_rows}
    daily_data = []
    for offset in range(30):
        day = start_day + timedelta(days=offset)
        daily_data.append(
            {
                "date": day.isoformat(),
                "label": day.strftime("%d/%m"),
                "weekday": ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")[day.weekday()],
                "requests": received_by_day.get(day, 0),
                "items": registered_by_day.get(day, 0),
            }
        )

    pending_count = db.session.scalar(
        select(func.count(RegistrationRequest.id)).where(
            RegistrationRequest.items.any(RequestedItem.status != "registered")
        )
    ) or 0
    completed_count = db.session.scalar(
        select(func.count(RegistrationRequest.id)).where(
            RegistrationRequest.items.any(),
            ~RegistrationRequest.items.any(RequestedItem.status != "registered"),
        )
    ) or 0
    total_item_count = db.session.scalar(select(func.count(RequestedItem.id))) or 0
    registered_item_count = db.session.scalar(
        select(func.count(RequestedItem.id)).where(RequestedItem.status == "registered")
    ) or 0
    return render_template(
        "performance.html",
        daily_data=daily_data,
        pending_count=pending_count,
        completed_count=completed_count,
        total_requests=pending_count + completed_count,
        total_item_count=total_item_count,
        registered_item_count=registered_item_count,
        item_completion_percentage=round(
            registered_item_count * 100 / total_item_count
        ) if total_item_count else 0,
        requests_total=sum(point["requests"] for point in daily_data),
        items_total=sum(point["items"] for point in daily_data),
        period_start=start_day.strftime("%d/%m/%Y"),
        period_end=today.strftime("%d/%m/%Y"),
    )


@app.get("/painel/solicitacoes/<int:request_id>")
@admin_required
def request_detail(request_id):
    registration = db.session.get(RegistrationRequest, request_id)
    if registration is None:
        abort(404)
    return render_template("detail.html", registration=registration)


@app.get("/painel/itens/<int:item_id>/confirmar-cadastro")
@admin_required
def confirm_item_registration(item_id):
    item = db.session.get(RequestedItem, item_id)
    if item is None:
        abort(404)
    item_number = next(
        number
        for number, requested_item in enumerate(item.registration.items, start=1)
        if requested_item.id == item.id
    )
    return render_template(
        "confirm_item.html",
        item=item,
        registration=item.registration,
        item_number=item_number,
        max_product_name_length=MAX_PRODUCT_NAME_LENGTH,
        max_product_code_length=MAX_PRODUCT_CODE_LENGTH,
    )


def deliver_product_confirmation(item, item_number):
    try:
        if not item.product_name or not item.product_code:
            raise RuntimeError("Informe o nome e o código do produto antes de enviar a confirmação.")
        notification = build_product_confirmation(item, item_number)
        if not notification["recipient"]:
            raise RuntimeError("O email do solicitante não está configurado.")
        send_notification(**notification)
        item.confirmation_sent_at = datetime.now(timezone.utc)
        db.session.commit()
        return True
    except (OSError, SQLAlchemyError, smtplib.SMTPException, RuntimeError, ValueError):
        db.session.rollback()
        logger.exception(
            "Falha ao enviar confirmação sistema de destino do item %s da solicitação %s.",
            item.id,
            item.registration.public_id,
        )
        return False


@app.post("/painel/itens/<int:item_id>/marcar-cadastrado")
@admin_required
def mark_item_registered(item_id):
    from flask import session

    item = db.session.scalar(select(RequestedItem).where(RequestedItem.id == item_id).with_for_update())
    if item is None:
        abort(404)

    registration = item.registration
    product_name = request.form.get("product_name", "").strip()
    product_code = request.form.get("product_code", "").strip()
    if not product_name or len(product_name) > MAX_PRODUCT_NAME_LENGTH:
        flash(f"Informe o nome do produto (até {MAX_PRODUCT_NAME_LENGTH} caracteres).", "error")
        return redirect(url_for("confirm_item_registration", item_id=item.id))
    if not product_code or len(product_code) > MAX_PRODUCT_CODE_LENGTH:
        flash(f"Informe o código do produto (até {MAX_PRODUCT_CODE_LENGTH} caracteres).", "error")
        return redirect(url_for("confirm_item_registration", item_id=item.id))

    is_new_registration = item.status != "registered"
    item.product_name = product_name
    item.product_code = product_code
    if is_new_registration:
        item.status = "registered"
        item.registered_at = datetime.now(timezone.utc)
        item.registered_by_admin_id = session["admin_id"]
    item.confirmation_sent_at = None
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Falha ao atualizar o cadastro sistema de destino do item %s.", item.id)
        flash("Não foi possível atualizar os dados do produto. Tente novamente.", "error")
        return redirect(url_for("request_detail", request_id=registration.id))

    item_number = next(
        number
        for number, requested_item in enumerate(registration.items, start=1)
        if requested_item.id == item.id
    )
    if deliver_product_confirmation(item, item_number):
        flash(
            f"O produto “{product_name}” (código {product_code}) foi salvo e a confirmação foi enviada.",
            "success",
        )
    else:
        flash(
            f"Os dados do produto “{product_name}” foram salvos, mas o email de confirmação não foi enviado. "
            "Use “Tentar enviar confirmação” na solicitação para reenviar.",
            "warning",
        )
    return redirect(url_for("request_detail", request_id=registration.id))


@app.post("/painel/itens/<int:item_id>/reenviar-confirmacao")
@admin_required
def resend_product_confirmation(item_id):
    item = db.session.scalar(select(RequestedItem).where(RequestedItem.id == item_id).with_for_update())
    if item is None:
        abort(404)
    registration = item.registration
    if item.status != "registered" or item.registered_at is None:
        flash("Marque o item como cadastrado no sistema de destino antes de enviar a confirmação.", "error")
        return redirect(url_for("request_detail", request_id=registration.id))
    if not item.product_name or not item.product_code:
        flash("Informe o nome e o código do produto antes de enviar a confirmação.", "warning")
        return redirect(url_for("confirm_item_registration", item_id=item.id))
    if item.confirmation_sent_at is not None:
        flash("A confirmação deste item já foi enviada.", "warning")
        return redirect(url_for("request_detail", request_id=registration.id))

    item_number = next(
        number
        for number, requested_item in enumerate(registration.items, start=1)
        if requested_item.id == item.id
    )
    if deliver_product_confirmation(item, item_number):
        flash(f"A confirmação do item “{item.summary}” foi enviada ao solicitante.", "success")
    else:
        flash(
            f"Não foi possível enviar a confirmação para {registration.requester_email}. "
            "O status do item continua marcado como cadastrado no sistema de destino; você pode tentar novamente.",
            "error",
        )
    return redirect(url_for("request_detail", request_id=registration.id))


@app.get("/painel/imagens/<filename>")
@admin_required
def uploaded_image(filename):
    if not re.fullmatch(r"[a-f0-9]{32}\.webp", filename):
        abort(404)
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename, mimetype="image/webp")


@app.after_request
def set_security_headers(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if app.config["SESSION_COOKIE_SECURE"] and app.config["HSTS_ENABLED"]:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


def initialize_database():
    with app.app_context():
        db.create_all()
        item_columns = {column["name"] for column in inspect(db.engine).get_columns("requested_item")}
        migrations = {
            "status": "ALTER TABLE requested_item ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'pending'",
            "registered_at": "ALTER TABLE requested_item ADD COLUMN registered_at TIMESTAMP WITH TIME ZONE",
            "product_name": "ALTER TABLE requested_item ADD COLUMN product_name VARCHAR(200)",
            "product_code": "ALTER TABLE requested_item ADD COLUMN product_code VARCHAR(100)",
            "confirmation_sent_at": "ALTER TABLE requested_item ADD COLUMN confirmation_sent_at TIMESTAMP WITH TIME ZONE",
            "registered_by_admin_id": (
                "ALTER TABLE requested_item ADD COLUMN registered_by_admin_id INTEGER "
                "REFERENCES admin(id) ON DELETE SET NULL"
            ),
        }
        for column_name, statement in migrations.items():
            if column_name not in item_columns:
                db.session.execute(text(statement))
        db.session.commit()

        admin_email = normalize_email(os.environ.get("ADMIN_EMAIL", ""))
        admin_password = os.environ.get("ADMIN_PASSWORD", "")
        if not admin_email:
            raise RuntimeError("Configure um ADMIN_EMAIL válido.")
        admin = db.session.scalar(select(Admin).where(Admin.email == admin_email))
        if len(admin_password) < 12:
            if admin is None:
                raise RuntimeError(
                    "ADMIN_PASSWORD deve conter pelo menos 12 caracteres para criar o acesso inicial."
                )
            logger.error(
                "ADMIN_PASSWORD possui menos de 12 caracteres; a alteração não foi aplicada. "
                "Use uma senha mais longa no .env e recrie o container da aplicação."
            )
            return
        if admin is None:
            db.session.add(
                Admin(email=admin_email, password_hash=generate_password_hash(admin_password))
            )
            db.session.commit()
        elif not check_password_hash(admin.password_hash, admin_password):
            admin.password_hash = generate_password_hash(admin_password)
            db.session.commit()


if __name__ == "__main__":
    initialize_database()
