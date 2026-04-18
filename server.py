import json
import mimetypes
import os
import base64
import html
from datetime import datetime
from email.utils import parseaddr
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
except ImportError:
    Request = None
    Credentials = None
    Flow = None
    build = None


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "local_state.json"
DOWNLOADS_DIR = ROOT / "downloads"
SECRETS_DIR = ROOT / "secrets"
TOKENS_DIR = ROOT / "tokens"
GMAIL_CLIENT_PATH = SECRETS_DIR / "gmail_oauth_client.json"
GMAIL_TOKEN_PATH = TOKENS_DIR / "gmail_token.json"
OAUTH_STATE_PATH = DATA_DIR / "oauth_state.json"
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "4188"))
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", f"http://127.0.0.1:{PORT}/auth/google/callback")
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")


DEFAULT_STATE = {
    "connection": {
        "status": "disconnected",
        "account": "",
        "lastSync": None,
        "scopes": [],
    },
    "rules": [],
    "importantSenders": [],
    "messages": [],
    "downloads": [],
    "sentReplies": [],
    "dailyUpdate": {
        "lastRun": None,
        "status": "czeka",
        "items": [
            "Synchronizacja nowych wiadomosci",
            "Sprawdzenie faktur i dokumentow",
            "Odswiezenie priorytetow i raportu dnia",
        ],
    },
    "activity": [],
}


def load_env_file():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def ensure_state():
    DATA_DIR.mkdir(exist_ok=True)
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    if not STATE_PATH.exists():
        STATE_PATH.write_text(json.dumps(DEFAULT_STATE, indent=2), encoding="utf-8")
        return

    state = json.loads(STATE_PATH.read_text(encoding="utf-8-sig"))
    changed = normalize_state(state)
    if changed:
        write_state(state)


def read_state():
    ensure_state()
    return json.loads(STATE_PATH.read_text(encoding="utf-8-sig"))


def write_state(state):
    DATA_DIR.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def normalize_state(state):
    changed = False
    if "downloads" not in state:
        state["downloads"] = []
        changed = True
    if "sentReplies" not in state:
        state["sentReplies"] = []
        changed = True
    if "dailyUpdate" not in state:
        state["dailyUpdate"] = DEFAULT_STATE["dailyUpdate"]
        changed = True
    if "importantSenders" not in state:
        state["importantSenders"] = []
        changed = True
    for message in state.get("messages", []):
        if "downloadedAttachments" not in message:
            message["downloadedAttachments"] = []
            changed = True
        if "downloadStatus" not in message:
            message["downloadStatus"] = "czeka na synchronizacje"
            changed = True
        if "gmailLabel" not in message:
            message["gmailLabel"] = "Agent/do-pobrania" if message.get("attachments") else "Agent/sprawdzone"
            changed = True
        if "attention" not in message:
            message["attention"] = False
            changed = True
        if "attentionReason" not in message:
            message["attentionReason"] = ""
            changed = True
        if "replyStatus" not in message:
            message["replyStatus"] = "czeka na odpowiedz" if message.get("needsReply") else "brak odpowiedzi"
            changed = True
    for rule in state.get("rules", []):
        if rule.get("id") == "rule-accounting":
            expected = {"zus", "deklaracja", "podatek", "dokumenty", "rozliczenie"}
            keywords = set(rule.get("keywords", []))
            if not expected.issubset(keywords):
                rule["keywords"] = sorted(keywords | expected)
                changed = True
    return changed


def now_label():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


load_env_file()


class GmailAssistantServer(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_url = urlparse(self.path)
        request_path = unquote(parsed_url.path)

        if request_path == "/api/dashboard":
            self._send_json(build_dashboard(), HTTPStatus.OK)
            return

        if request_path == "/api/health":
            self._send_json({"ok": True, "mode": "local"}, HTTPStatus.OK)
            return

        if request_path == "/auth/google/start":
            self._start_google_auth()
            return

        if request_path == "/auth/google/callback":
            self._finish_google_auth(parsed_url)
            return

        if request_path in {"/", ""}:
            self._serve_file("index.html")
            return

        safe_path = request_path.lstrip("/")
        target = ROOT / safe_path
        if target.is_file() and ROOT in target.resolve().parents:
            self._serve_file(safe_path)
            return

        self._send_json({"error": "Nie znaleziono zasobu."}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed_url = urlparse(self.path)

        if parsed_url.path == "/api/sync":
            state = read_state()
            try:
                sync_gmail_messages(state)
                state = read_state()
            except GmailIntegrationError as error:
                self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            except Exception as error:
                self._send_json({"error": f"Nie udalo sie zsynchronizowac Gmaila: {error}"}, HTTPStatus.BAD_GATEWAY)
                return
            self._send_json(build_dashboard(state), HTTPStatus.OK)
            return

        if parsed_url.path == "/api/rules":
            payload = self._read_json_body()
            if payload is None:
                return
            rule = {
                "id": f"rule-{int(datetime.now().timestamp())}",
                "name": str(payload.get("name", "Nowa regula")).strip() or "Nowa regula",
                "sender": str(payload.get("sender", "")).strip(),
                "keywords": [part.strip() for part in str(payload.get("keywords", "")).split(",") if part.strip()],
                "folder": str(payload.get("folder", "downloads")).strip() or "downloads",
                "label": str(payload.get("name", "Pliki")).strip() or "Pliki",
                "enabled": True,
            }
            state = read_state()
            state["rules"].insert(0, rule)
            state["activity"].insert(0, f"Dodano regule: {rule['name']}.")
            write_state(state)
            self._send_json(build_dashboard(state), HTTPStatus.CREATED)
            return

        if parsed_url.path == "/api/important-senders":
            payload = self._read_json_body()
            if payload is None:
                return
            sender = {
                "id": f"sender-{int(datetime.now().timestamp())}",
                "email": str(payload.get("email", "")).strip().lower(),
                "name": str(payload.get("name", "Wazny nadawca")).strip() or "Wazny nadawca",
                "reason": str(payload.get("reason", "Wymaga szybkiej uwagi")).strip() or "Wymaga szybkiej uwagi",
                "label": build_important_sender_label(str(payload.get("name", "Wazny nadawca")).strip()),
                "enabled": True,
            }
            if "@" not in sender["email"]:
                self._send_json({"error": "Podaj poprawny adres e-mail."}, HTTPStatus.BAD_REQUEST)
                return
            state = read_state()
            state.setdefault("importantSenders", []).insert(0, sender)
            apply_important_senders(state)
            state["activity"].insert(0, f"Dodano waznego nadawce: {sender['email']}.")
            write_state(state)
            self._send_json(build_dashboard(state), HTTPStatus.CREATED)
            return

        if parsed_url.path == "/api/daily-update":
            state = read_state()
            try:
                sync_gmail_messages(state)
                state = read_state()
            except GmailIntegrationError:
                state["dailyUpdate"] = {
                    "lastRun": now_label(),
                    "status": "czeka",
                    "items": [
                        "Brak polaczenia Gmail",
                        "Polacz konto, a potem uruchom aktualizacje ponownie",
                    ],
                }
                write_state(state)
            self._send_json(build_dashboard(state), HTTPStatus.OK)
            return

        if parsed_url.path == "/api/draft":
            payload = self._read_json_body()
            if payload is None:
                return
            draft = build_demo_draft(str(payload.get("messageId", "")))
            self._send_json({"draft": draft}, HTTPStatus.OK)
            return

        if parsed_url.path == "/api/send":
            payload = self._read_json_body()
            if payload is None:
                return
            message_id = str(payload.get("messageId", "")).strip()
            body = str(payload.get("body", "")).strip()
            if not body:
                self._send_json({"error": "Wpisz tresc odpowiedzi przed wyslaniem."}, HTTPStatus.BAD_REQUEST)
                return

            state = read_state()
            message = find_message(state, message_id)
            if message is None:
                self._send_json({"error": "Nie znaleziono wiadomosci."}, HTTPStatus.NOT_FOUND)
                return

            sent = {
                "messageId": message_id,
                "to": message["from"],
                "subject": f"Re: {message['subject']}",
                "body": body,
                "sentAt": now_label(),
                "mode": "demo",
            }
            state.setdefault("sentReplies", []).insert(0, sent)
            state["sentReplies"] = state["sentReplies"][:30]
            message["needsReply"] = False
            message["replyStatus"] = "wyslano demo"
            message["gmailLabel"] = "Agent/odpowiedziano"
            state["activity"].insert(0, f"Wyslano odpowiedz demo do {message['from']} w sprawie: {message['subject']}.")
            write_state(state)
            self._send_json(build_dashboard(state), HTTPStatus.OK)
            return

        self._send_json({"error": "Nieprawidlowy endpoint."}, HTTPStatus.NOT_FOUND)

    def log_message(self, format, *args):
        return

    def _read_json_body(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        try:
            return json.loads(raw_body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json({"error": "Nieprawidlowe dane."}, HTTPStatus.BAD_REQUEST)
            return None

    def _serve_file(self, relative_path):
        target = ROOT / relative_path
        content_type, _ = mimetypes.guess_type(target.name)
        if content_type and (content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}):
            content_type = f"{content_type}; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.end_headers()
        self.wfile.write(target.read_bytes())

    def _send_json(self, payload, status):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location):
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.end_headers()

    def _send_html(self, html, status=HTTPStatus.OK):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _start_google_auth(self):
        if not google_libs_available():
            self._send_html("<h1>Brakuje bibliotek Google</h1><p>Uruchom: pip install -r requirements.txt</p>", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if not GMAIL_CLIENT_PATH.exists():
            self._send_html("<h1>Brakuje pliku OAuth</h1><p>Oczekiwany plik: secrets/gmail_oauth_client.json</p>", HTTPStatus.BAD_REQUEST)
            return

        flow = Flow.from_client_secrets_file(str(GMAIL_CLIENT_PATH), scopes=GMAIL_SCOPES, redirect_uri=GOOGLE_REDIRECT_URI)
        auth_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="false", prompt="consent")
        DATA_DIR.mkdir(exist_ok=True)
        OAUTH_STATE_PATH.write_text(json.dumps({"state": state}), encoding="utf-8")
        self._redirect(auth_url)

    def _finish_google_auth(self, parsed_url):
        if not google_libs_available():
            self._send_html("<h1>Brakuje bibliotek Google</h1><p>Uruchom: pip install -r requirements.txt</p>", HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        params = parse_qs(parsed_url.query)
        if "error" in params:
            self._send_html(f"<h1>Logowanie przerwane</h1><p>{params['error'][0]}</p>", HTTPStatus.BAD_REQUEST)
            return

        expected_state = {}
        if OAUTH_STATE_PATH.exists():
            expected_state = json.loads(OAUTH_STATE_PATH.read_text(encoding="utf-8"))
        if expected_state.get("state") and params.get("state", [""])[0] != expected_state["state"]:
            self._send_html("<h1>Blad OAuth</h1><p>Nieprawidlowy parametr state.</p>", HTTPStatus.BAD_REQUEST)
            return

        code = params.get("code", [""])[0]
        if not code:
            self._send_html("<h1>Blad OAuth</h1><p>Brakuje kodu autoryzacji.</p>", HTTPStatus.BAD_REQUEST)
            return

        try:
            flow = Flow.from_client_secrets_file(str(GMAIL_CLIENT_PATH), scopes=GMAIL_SCOPES, redirect_uri=GOOGLE_REDIRECT_URI)
            flow.fetch_token(code=code)
            credentials = flow.credentials

            TOKENS_DIR.mkdir(exist_ok=True)
            GMAIL_TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")

            service = build_gmail_service(credentials)
            profile = service.users().getProfile(userId="me").execute()
        except Exception as error:
            safe_error = html.escape(str(error))
            self._send_html(
                (
                    "<h1>Nie udalo sie polaczyc Gmaila</h1>"
                    "<p>Google wrocilo do aplikacji, ale token nie zostal zapisany.</p>"
                    f"<pre>{safe_error}</pre>"
                    "<p>Najczestsza przyczyna: zly URI przekierowania albo aplikacja/test user w Google Cloud.</p>"
                    "<p>Wroc do aplikacji i sprobuj polaczyc Gmail ponownie.</p>"
                ),
                HTTPStatus.BAD_REQUEST,
            )
            return

        state = read_state()
        state["connection"] = {
            "status": "connected",
            "account": profile.get("emailAddress", ""),
            "lastSync": state.get("connection", {}).get("lastSync"),
            "scopes": GMAIL_SCOPES,
        }
        state["activity"].insert(0, f"Polaczono Gmail: {profile.get('emailAddress', '')}.")
        write_state(state)
        self._send_html("<h1>Gmail polaczony</h1><p>Mozesz wrocic do aplikacji.</p><script>setTimeout(() => location.href='/', 1200)</script>")


def build_dashboard(state=None):
    state = state or read_state()
    normalize_state(state)
    apply_important_senders(state)
    messages = state["messages"]
    today = datetime.now().strftime("%Y-%m-%d")
    return {
        **state,
        "stats": {
            "messagesToday": sum(1 for item in messages if item.get("receivedAt", "").startswith(today)),
            "needsReply": sum(1 for item in messages if item["needsReply"]),
            "attachments": sum(len(item["attachments"]) for item in messages),
            "attention": sum(1 for item in messages if item.get("attention")),
            "downloaded": len(state.get("downloads", [])),
            "rules": sum(1 for item in state["rules"] if item["enabled"]),
        },
        "report": {
            "daily": [
                "Brak danych z Gmaila. Polacz konto i uruchom synchronizacje.",
            ],
            "weekly": [
                "Brak danych tygodniowych. Raport powstanie po synchronizacji z Gmail.",
            ],
        },
    }


class GmailIntegrationError(Exception):
    pass


def google_libs_available():
    return all([Request, Credentials, Flow, build])


def load_gmail_credentials():
    if not google_libs_available():
        raise GmailIntegrationError("Brakuje bibliotek Google. Uruchom: pip install -r requirements.txt")
    if not GMAIL_CLIENT_PATH.exists():
        raise GmailIntegrationError("Brakuje pliku secrets/gmail_oauth_client.json.")
    if not GMAIL_TOKEN_PATH.exists():
        raise GmailIntegrationError("Najpierw polacz Gmail przyciskiem 'Przygotuj polaczenie Gmail'.")

    credentials = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_PATH), GMAIL_SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        GMAIL_TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
    if not credentials.valid:
        raise GmailIntegrationError("Token Gmail jest niewazny. Polacz konto ponownie.")
    return credentials


def build_gmail_service(credentials):
    return build("gmail", "v1", credentials=credentials)


def sync_gmail_messages(state):
    credentials = load_gmail_credentials()
    service = build_gmail_service(credentials)
    profile = service.users().getProfile(userId="me").execute()

    result = service.users().messages().list(userId="me", maxResults=25, q="newer_than:30d").execute()
    message_refs = result.get("messages", [])
    synced_messages = []
    saved_count = 0

    state["connection"] = {
        "status": "connected",
        "account": profile.get("emailAddress", ""),
        "lastSync": now_label(),
        "scopes": GMAIL_SCOPES,
    }
    state.setdefault("downloads", [])
    known_downloads = {item.get("path") for item in state["downloads"]}

    for message_ref in message_refs:
        raw_message = service.users().messages().get(userId="me", id=message_ref["id"], format="full").execute()
        message = build_message_from_gmail(raw_message)
        attachments = collect_gmail_attachments(raw_message.get("payload", {}))
        message["attachments"] = [attachment["filename"] for attachment in attachments]

        rule = find_matching_rule(message, state.get("rules", []))
        if rule and attachments:
            downloaded, new_count = download_gmail_attachments(service, raw_message["id"], attachments, rule, message, state, known_downloads)
            saved_count += new_count
            message["downloadedAttachments"] = downloaded
            if downloaded:
                folder = resolve_download_folder(rule["folder"])
                message["downloadStatus"] = f"pobrano {len(downloaded)} plikow do {folder}"
                message["gmailLabel"] = f"Agent/pobrane/{rule['label']}"
            else:
                message["downloadStatus"] = "brak bezpiecznych plikow do pobrania"
        elif attachments:
            message["downloadStatus"] = "brak pasujacej reguly"
        else:
            message["downloadStatus"] = "brak zalacznikow"

        synced_messages.append(message)

    state["messages"] = synced_messages
    apply_important_senders(state)
    state["dailyUpdate"] = {
        "lastRun": now_label(),
        "status": "zrobione",
        "items": [
            f"Pobrano metadane wiadomosci: {len(synced_messages)}",
            f"Pobrano pliki wedlug regul: {saved_count}",
            f"Konto Gmail: {profile.get('emailAddress', '')}",
        ],
    }
    state["activity"].insert(0, f"Synchronizacja Gmail: {len(synced_messages)} wiadomosci, {saved_count} nowych plikow.")
    state["activity"] = state["activity"][:50]
    state["downloads"] = state["downloads"][:100]
    write_state(state)


def build_message_from_gmail(raw_message):
    payload = raw_message.get("payload", {})
    headers = {header.get("name", "").lower(): header.get("value", "") for header in payload.get("headers", [])}
    from_header = headers.get("from", "")
    _, sender_email = parseaddr(from_header)
    received_at = gmail_internal_date(raw_message.get("internalDate"))
    return {
        "id": raw_message.get("id", ""),
        "from": sender_email or from_header,
        "subject": headers.get("subject", "(bez tematu)"),
        "receivedAt": received_at,
        "category": "Gmail",
        "priority": "sredni",
        "needsReply": False,
        "summary": raw_message.get("snippet", ""),
        "attachments": [],
        "downloadedAttachments": [],
        "downloadStatus": "czeka na synchronizacje",
        "gmailLabel": "Agent/sprawdzone",
        "attention": False,
        "attentionReason": "",
        "replyStatus": "brak odpowiedzi",
    }


def gmail_internal_date(value):
    try:
        timestamp = int(value) / 1000
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return now_label()


def collect_gmail_attachments(part):
    attachments = []
    filename = part.get("filename", "")
    body = part.get("body", {})
    if filename:
        attachments.append(
            {
                "filename": filename,
                "attachmentId": body.get("attachmentId"),
                "data": body.get("data"),
                "mimeType": part.get("mimeType", ""),
            }
        )
    for child in part.get("parts", []) or []:
        attachments.extend(collect_gmail_attachments(child))
    return attachments


def download_gmail_attachments(service, message_id, attachments, rule, message, state, known_downloads):
    folder = resolve_download_folder(rule["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    downloaded = []
    new_count = 0

    for attachment in attachments:
        filename = sanitize_filename(attachment["filename"])
        if not is_auto_downloadable(filename):
            continue

        target = unique_path(folder / filename)
        payload = attachment.get("data")
        if not payload and attachment.get("attachmentId"):
            attachment_body = service.users().messages().attachments().get(
                userId="me",
                messageId=message_id,
                id=attachment["attachmentId"],
            ).execute()
            payload = attachment_body.get("data")
        if not payload:
            continue

        target.write_bytes(decode_gmail_data(payload))
        downloaded.append({"name": filename, "path": str(target)})

        if str(target) not in known_downloads:
            known_downloads.add(str(target))
            new_count += 1
            state["downloads"].insert(
                0,
                {
                    "file": filename,
                    "path": str(target),
                    "sender": message["from"],
                    "subject": message["subject"],
                    "rule": rule["name"],
                    "downloadedAt": now_label(),
                    "status": "pobrano",
                },
            )

    return downloaded, new_count


def decode_gmail_data(data):
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def sanitize_filename(filename):
    cleaned = "".join("_" if char in '<>:"/\\|?*' else char for char in filename).strip()
    return cleaned or "zalacznik"


def unique_path(path):
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 1
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def materialize_demo_downloads(state):
    saved_count = 0
    apply_important_senders(state)
    state.setdefault("downloads", [])
    known_downloads = {item.get("path") for item in state["downloads"]}
    for message in state["messages"]:
        rule = find_matching_rule(message, state["rules"])
        if not rule:
            if message.get("attachments"):
                message["downloadStatus"] = "brak pasujacej reguly"
            continue

        folder = resolve_download_folder(rule["folder"])
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            message["downloadStatus"] = f"nie udalo sie utworzyc folderu: {folder}"
            message["gmailLabel"] = "Agent/blad-pobierania"
            state["activity"].insert(0, f"Blad folderu {folder}: {error}.")
            continue
        downloaded = []
        new_downloaded = []
        skipped = []

        for attachment in message["attachments"]:
            if not is_auto_downloadable(attachment):
                skipped.append(attachment)
                continue

            target = folder / attachment
            try:
                target.write_text(
                    "\n".join(
                        [
                            f"Demo placeholder for {attachment}",
                            f"Source: {message['from']}",
                            f"Message: {message['subject']}",
                            f"Saved at: {now_label()}",
                        ]
                    ),
                    encoding="utf-8",
                )
            except OSError as error:
                skipped.append(f"{attachment} ({error})")
                continue
            downloaded.append({"name": attachment, "path": str(target)})

            if str(target) not in known_downloads:
                saved_count += 1
                known_downloads.add(str(target))
                new_downloaded.append(attachment)
                state["downloads"].insert(
                    0,
                    {
                        "file": attachment,
                        "path": str(target),
                        "sender": message["from"],
                        "subject": message["subject"],
                        "rule": rule["name"],
                        "downloadedAt": now_label(),
                        "status": "pobrano",
                    },
                )

        message["downloadedAttachments"] = downloaded
        if downloaded:
            message["downloadStatus"] = f"pobrano {len(downloaded)} plikow do {folder}"
            message["gmailLabel"] = f"Agent/pobrane/{rule['label']}"
            if new_downloaded:
                state["activity"].insert(0, f"Pobrano {len(new_downloaded)} plikow z wiadomosci od {message['from']} do {folder}.")
        elif skipped:
            message["downloadStatus"] = f"pominieto: {', '.join(skipped)}"
            message["gmailLabel"] = "Agent/do-sprawdzenia"

    state["downloads"] = state["downloads"][:30]
    return saved_count


def apply_important_senders(state):
    senders = {
        item.get("email", "").lower(): item
        for item in state.get("importantSenders", [])
        if item.get("enabled") and item.get("email")
    }
    for message in state.get("messages", []):
        important = senders.get(message.get("from", "").lower())
        if important:
            message["attention"] = True
            message["priority"] = "wysoki"
            message["attentionReason"] = f"Wazny nadawca: {important['name']}"
            if not message.get("needsReply") and important.get("label") and not message.get("gmailLabel", "").startswith("Agent/pobrane"):
                message["gmailLabel"] = important["label"]
        elif "attention" not in message:
            message["attention"] = False
            message["attentionReason"] = ""


def build_important_sender_label(name):
    safe_name = "".join(char.lower() if char.isalnum() else "-" for char in name).strip("-")
    return f"Agent/wazne/{safe_name or 'nadawca'}"


def resolve_download_folder(folder):
    candidate = Path(folder)
    if candidate.is_absolute():
        return candidate
    return ROOT / folder


def is_auto_downloadable(filename):
    return filename.lower().endswith((".pdf", ".xml", ".doc", ".docx", ".xls", ".xlsx"))


def find_matching_rule(message, rules):
    haystack = " ".join([message.get("subject", ""), message.get("summary", ""), " ".join(message.get("attachments", []))]).lower()
    sender = message.get("from", "").lower()
    for rule in rules:
        if not rule.get("enabled"):
            continue
        rule_sender = rule.get("sender", "").lower()
        if rule_sender and rule_sender != sender:
            continue
        keywords = [keyword.lower() for keyword in rule.get("keywords", [])]
        if keywords and not any(keyword in haystack for keyword in keywords):
            continue
        return rule
    return None


def build_demo_draft(message_id):
    if message_id == "msg-1002":
        return (
            "Dzien dobry,\n\n"
            "dziekuje za wiadomosc i przeslane materialy. Sprawdze zakres prac oraz dostepne terminy, "
            "a nastepnie wroce z konkretna propozycja i wstepna wycena.\n\n"
            "Pozdrawiam"
        )
    return "Dzien dobry,\n\ndziekuje za wiadomosc. Temat zostal odnotowany i wroce z odpowiedzia.\n\nPozdrawiam"


def find_message(state, message_id):
    for message in state.get("messages", []):
        if message.get("id") == message_id:
            return message
    return None


if __name__ == "__main__":
    ensure_state()
    print(f"Agent Gmail dziala na http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), GmailAssistantServer).serve_forever()
