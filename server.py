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
        "status": "demo",
        "account": "p.p.profinish@gmail.com",
        "lastSync": None,
        "scopes": ["gmail.readonly", "gmail.modify"],
    },
    "rules": [
        {
            "id": "rule-invoices",
            "name": "Faktury od dostawcow",
            "sender": "faktury@hurtownia.pl",
            "keywords": ["faktura", "vat", "pdf"],
            "folder": "downloads/faktury/hurtownia",
            "label": "Faktury",
            "enabled": True,
        },
        {
            "id": "rule-accounting",
            "name": "Dokumenty od ksiegowej",
            "sender": "biuro@ksiegowosc.pl",
            "keywords": ["zus", "deklaracja", "podatek"],
            "folder": "downloads/ksiegowosc",
            "label": "Ksiegowosc",
            "enabled": True,
        },
    ],
    "messages": [
        {
            "id": "msg-1001",
            "from": "faktury@hurtownia.pl",
            "subject": "Faktura VAT FV/04/2026/182",
            "receivedAt": "2026-04-12 08:42",
            "category": "Faktury",
            "priority": "wysoki",
            "needsReply": False,
            "summary": "Nowa faktura za materialy budowlane. Termin platnosci: 7 dni.",
            "attachments": ["FV-04-2026-182.pdf"],
        },
        {
            "id": "msg-1002",
            "from": "klient@firma-example.pl",
            "subject": "Prosba o termin wykonczenia lokalu",
            "receivedAt": "2026-04-12 10:15",
            "category": "Do odpowiedzi",
            "priority": "wysoki",
            "needsReply": True,
            "summary": "Klient pyta o wolny termin i prosi o wstepna wycene prac.",
            "attachments": ["rzut-lokalu.pdf", "zdjecia.zip"],
        },
        {
            "id": "msg-1003",
            "from": "biuro@ksiegowosc.pl",
            "subject": "Dokumenty do rozliczenia tygodnia",
            "receivedAt": "2026-04-11 16:30",
            "category": "Ksiegowosc",
            "priority": "sredni",
            "needsReply": False,
            "summary": "Ksiegowa przesyla zestawienie i prosi o doslanie brakujacych kosztow paliwa.",
            "attachments": ["rozliczenie-tydzien-15.pdf"],
        },
    ],
    "activity": [
        "Przygotowano reguly pobierania faktur i dokumentow.",
        "Wykryto 2 wiadomosci wymagajace uwagi w trybie demo.",
    ],
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


def read_state():
    ensure_state()
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def write_state(state):
    DATA_DIR.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


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
            state["activity"].insert(0, "Synchronizacja demo: pobrano 3 wiadomosci i sprawdzono zalaczniki.")
            materialize_demo_downloads(state)
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
                "label": str(payload.get("label", "Inne")).strip() or "Inne",
                "enabled": True,
            }
            state = read_state()
            state["rules"].insert(0, rule)
            state["activity"].insert(0, f"Dodano regule: {rule['name']}.")
            write_state(state)
            self._send_json(build_dashboard(state), HTTPStatus.CREATED)
            return

        if parsed_url.path == "/api/draft":
            payload = self._read_json_body()
            if payload is None:
                return
            draft = build_demo_draft(str(payload.get("messageId", "")))
            self._send_json({"draft": draft}, HTTPStatus.OK)
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
    messages = state["messages"]
    return {
        **state,
        "stats": {
            "messagesToday": sum(1 for item in messages if item["receivedAt"].startswith("2026-04-12")),
            "needsReply": sum(1 for item in messages if item["needsReply"]),
            "attachments": sum(len(item["attachments"]) for item in messages),
            "rules": sum(1 for item in state["rules"] if item["enabled"]),
        },
        "report": {
            "daily": [
                "Najwazniejsze: klient pyta o termin i wycene prac.",
                "Do pobrania: faktura FV/04/2026/182 oraz dokumenty ksiegowe.",
                "Ryzyko: wiadomosc ze zdjeciami ZIP wymaga recznego sprawdzenia przed otwarciem.",
            ],
            "weekly": [
                "W tym tygodniu dominowaly faktury, rozliczenia i zapytania ofertowe.",
                "Najczesciej powtarzajacy sie temat: dosylanie dokumentow do ksiegowosci.",
                "Rekomendacja: dodac regule dla stalego klienta i osobny folder na rzuty lokali.",
            ],
        },
    }


def materialize_demo_downloads(state):
    for rule in state["rules"]:
        (ROOT / rule["folder"]).mkdir(parents=True, exist_ok=True)
    for message in state["messages"]:
        for attachment in message["attachments"]:
            if attachment.lower().endswith((".pdf", ".xml")):
                target = DOWNLOADS_DIR / attachment
                if not target.exists():
                    target.write_text(f"Demo placeholder for {attachment}\nSource: {message['from']}\n", encoding="utf-8")


def build_demo_draft(message_id):
    if message_id == "msg-1002":
        return (
            "Dzien dobry,\n\n"
            "dziekuje za wiadomosc i przeslane materialy. Sprawdze zakres prac oraz dostepne terminy, "
            "a nastepnie wroce z konkretna propozycja i wstepna wycena.\n\n"
            "Pozdrawiam"
        )
    return "Dzien dobry,\n\ndziekuje za wiadomosc. Temat zostal odnotowany i wroce z odpowiedzia.\n\nPozdrawiam"


if __name__ == "__main__":
    ensure_state()
    print(f"Agent Gmail dziala na http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), GmailAssistantServer).serve_forever()
