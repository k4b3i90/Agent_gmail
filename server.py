import json
import mimetypes
import os
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "local_state.json"
DOWNLOADS_DIR = ROOT / "downloads"
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "4188"))


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
            self._send_json({"ok": True, "mode": "demo"}, HTTPStatus.OK)
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
            state["connection"]["lastSync"] = now_label()
            saved_count = materialize_demo_downloads(state)
            state["activity"].insert(0, f"Synchronizacja demo: pobrano {saved_count} pliki do wskazanych folderow.")
            write_state(state)
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
            state["dailyUpdate"] = {
                "lastRun": now_label(),
                "status": "zrobione",
                "items": [
                    "Pobrano najnowsze wiadomosci demo",
                    "Przeliczono waznych nadawcow",
                    "Odswiezono raport dzienny i statusy odpowiedzi",
                ],
            }
            apply_important_senders(state)
            state["activity"].insert(0, "Wykonano codzienna aktualizacje danych demo.")
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
