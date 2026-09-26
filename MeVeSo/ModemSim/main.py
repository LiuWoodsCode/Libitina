#!/usr/bin/env python3
"""Stateful 3GPP smartphone modem/USIM emulator.

The control plane is intentionally convincing; the data plane is intentionally
isolated.  No code in this program opens an Internet socket or speaks to radio
hardware.  Activated PDP contexts receive RFC 5737/RFC 3849 documentation
addresses, and dial-up data is accepted and discarded locally.

The AT DTE/DCE interface follows the shapes in 3GPP TS 27.007 and TS 27.005.
It is a compatibility simulator, not a conformance-certified baseband.
"""

from __future__ import annotations

import argparse
import cmd
import copy
import datetime as _dt
import json
import os
import pty
import re
import select
import shlex
import signal
import socket
import sys
import threading
import time
import tty
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROGRAM_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PROGRAM_DIR / "config.json"

# +COPS AcT values from TS 27.007.  Friendly aliases are accepted by the
# debug console, while the canonical name is retained in state/config output.
RATS: Dict[str, Dict[str, Any]] = {
    "GSM": {"act": 0, "family": "GERAN", "aliases": ("2G", "GPRS")},
    "GSM_COMPACT": {"act": 1, "family": "GERAN", "aliases": ()},
    "UMTS": {"act": 2, "family": "UTRAN", "aliases": ("3G", "WCDMA")},
    "EDGE": {"act": 3, "family": "GERAN", "aliases": ("EGPRS", "GSM_EDGE")},
    "HSDPA": {"act": 4, "family": "UTRAN", "aliases": ()},
    "HSUPA": {"act": 5, "family": "UTRAN", "aliases": ()},
    "HSPA": {"act": 6, "family": "UTRAN", "aliases": ("HSPA+", "HSPAP")},
    "LTE": {"act": 7, "family": "EUTRAN", "aliases": ("4G", "EUTRAN")},
    "EC_GSM_IOT": {"act": 8, "family": "GERAN", "aliases": ()},
    "NB_IOT": {"act": 9, "family": "EUTRAN", "aliases": ("NBIOT",)},
    "CAT_M": {"act": 10, "family": "EUTRAN", "aliases": ("CATM", "LTE_M")},
    "NR": {"act": 11, "family": "NGRAN", "aliases": ("5G", "5G_SA", "NR_SA")},
    "NR_NSA": {"act": 13, "family": "NGRAN", "aliases": ("5G_NSA",)},
}


def canonical_rat(value: str) -> str:
    wanted = value.strip().upper().replace("-", "_").replace(" ", "_")
    for name, info in RATS.items():
        if wanted == name or wanted in {a.upper().replace("-", "_") for a in info["aliases"]}:
            return name
    raise ValueError(f"unknown RAT {value!r}; use one of: {', '.join(RATS)}")


def load_config(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            cfg = json.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"configuration file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(cfg, dict):
        raise ValueError("configuration root must be a JSON object")
    required = (
        "manufacturer", "model", "revision", "imei", "imsi", "iccid",
        "msisdn", "carrier_long", "carrier_short", "plmn", "rat", "lac",
        "tac", "cell_id", "smsc", "incoming_number", "pdp_contexts",
    )
    missing = [key for key in required if key not in cfg]
    if missing:
        raise ValueError("configuration is missing: " + ", ".join(missing))
    cfg["rat"] = canonical_rat(str(cfg["rat"]))
    if not re.fullmatch(r"\d{5,6}", str(cfg["plmn"])):
        raise ValueError("plmn must contain 5 or 6 decimal digits")
    if not re.fullmatch(r"\d{14,16}", str(cfg["imei"])):
        raise ValueError("imei must contain 14 to 16 decimal digits")
    return cfg


def save_config(path: Path, cfg: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(cfg, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)


# ------------------------------ encoding helpers ------------------------------


def _swap_bcd_digits(digits: str) -> str:
    """Encode decimal digits as semi-octets (low nibble first)."""
    digits = re.sub(r"\D", "", digits)
    if len(digits) % 2:
        digits += "F"
    return "".join(digits[i + 1] + digits[i] for i in range(0, len(digits), 2))


_GSM7_TABLE = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ"
    " !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM7_MAP = {character: index for index, character in enumerate(_GSM7_TABLE)}
_GSM7_EXT = {"\f": 0x0A, "^": 0x14, "{": 0x28, "}": 0x29, "\\": 0x2F,
             "[": 0x3C, "~": 0x3D, "]": 0x3E, "|": 0x40, "€": 0x65}


def _gsm7_septets(text: str, replace: bool = True) -> List[int]:
    septets: List[int] = []
    for character in text:
        if character in _GSM7_MAP:
            septets.append(_GSM7_MAP[character])
        elif character in _GSM7_EXT:
            septets.extend((0x1B, _GSM7_EXT[character]))
        elif replace:
            septets.append(_GSM7_MAP["?"])
        else:
            raise UnicodeEncodeError("gsm0338", character, 0, 1, "not in GSM default alphabet")
    return septets


def _gsm7_pack(text: str, pad_to_septets: Optional[int] = None) -> bytes:
    septets = _gsm7_septets(text)
    if pad_to_septets is not None and len(septets) < pad_to_septets:
        # 3GPP CBS commonly pads 7-bit text with CR septets.
        septets.extend([0x0D] * (pad_to_septets - len(septets)))
    out = bytearray((len(septets) * 7 + 7) // 8)
    for i, septet in enumerate(septets):
        bitpos = i * 7
        bytepos = bitpos // 8
        shift = bitpos % 8
        out[bytepos] |= (septet << shift) & 0xFF
        if shift > 1 and bytepos + 1 < len(out):
            out[bytepos + 1] |= (septet >> (8 - shift)) & 0xFF
    return bytes(out)


def _scts(now: Optional[_dt.datetime] = None) -> bytes:
    """Build a GSM SMS Service Centre Time Stamp (7 semi-octet bytes)."""
    now = now or _dt.datetime.now().astimezone()
    offset = now.utcoffset() or _dt.timedelta(0)
    quarters = int(abs(offset.total_seconds()) // (15 * 60))

    def enc2(v: int) -> int:
        s = f"{v:02d}"
        return int(s[1] + s[0], 16)

    tz = enc2(quarters)
    if offset.total_seconds() < 0:
        # Sign bit is bit 3 of the low-order semi-octet after swapping.
        tz |= 0x08

    return bytes(
        [
            enc2(now.year % 100),
            enc2(now.month),
            enc2(now.day),
            enc2(now.hour),
            enc2(now.minute),
            enc2(now.second),
            tz,
        ]
    )


def build_sms_deliver_pdu(sender: str, text: str, now: Optional[_dt.datetime] = None) -> Tuple[str, int]:
    """Return (PDU hex including SCA, TPDU length in octets)."""
    digits = re.sub(r"\D", "", sender)
    toa = 0x91 if sender.startswith("+") else 0x81
    try:
        septets = _gsm7_septets(text, replace=False)
        ud = _gsm7_pack(text)
        dcs = 0x00
        udl = len(septets)
    except UnicodeEncodeError:
        ud = text.encode("utf-16-be")
        dcs = 0x08
        udl = len(ud)

    tpdu = bytearray()
    tpdu.append(0x04)  # SMS-DELIVER, no UDH
    tpdu.append(len(digits))
    tpdu.append(toa)
    tpdu.extend(bytes.fromhex(_swap_bcd_digits(digits)))
    tpdu.append(0x00)  # PID
    tpdu.append(dcs)
    tpdu.extend(_scts(now))
    tpdu.append(udl)
    tpdu.extend(ud)

    # SCA length 0 means the SMSC address is omitted.
    pdu = bytes([0x00]) + bytes(tpdu)
    return pdu.hex().upper(), len(tpdu)


def build_cbs_pdu(message_id: int, text: str, serial: int = 1) -> str:
    """Build one 88-octet GSM/UMTS Cell Broadcast page in 7-bit encoding."""
    # CBS page: SN(2), MID(2), DCS(1), page parameter(1), user data(82).
    # 82 octets hold 93 GSM septets. Page parameter 0x11 = page 1 of 1.
    user_data = _gsm7_pack(text, pad_to_septets=93)
    user_data = (user_data + b"\r" * 82)[:82]
    pdu = bytes(
        [
            (serial >> 8) & 0xFF,
            serial & 0xFF,
            (message_id >> 8) & 0xFF,
            message_id & 0xFF,
            0x00,  # GSM 7-bit, language group 0
            0x11,
        ]
    ) + user_data
    assert len(pdu) == 88
    return pdu.hex().upper()


def text_timestamp(now: Optional[_dt.datetime] = None) -> str:
    now = now or _dt.datetime.now().astimezone()
    offset = now.utcoffset() or _dt.timedelta(0)
    quarters = int(round(offset.total_seconds() / (15 * 60)))
    sign = "+" if quarters >= 0 else "-"
    return now.strftime("%y/%m/%d,%H:%M:%S") + f"{sign}{abs(quarters):02d}"


def iccid_ef_bytes(iccid: str) -> str:
    """ICCID as EF_ICCID semi-octets, suitable for +CRSM."""
    return _swap_bcd_digits(iccid)


# -------------------------------- data models --------------------------------


@dataclass
class SMS:
    index: int
    sender: str
    text: str
    timestamp: _dt.datetime = field(default_factory=lambda: _dt.datetime.now().astimezone())
    unread: bool = True
    outgoing: bool = False
    recipient: str = ""


@dataclass
class Call:
    index: int = 1
    direction: int = 1  # 0 outgoing, 1 incoming
    state: int = 4      # +CLCC: 0 active, 2 dialing, 3 alerting, 4 incoming
    number: str = ""
    multiparty: int = 0
    voice: bool = True


@dataclass
class PDPContext:
    cid: int
    pdp_type: str = "IPV4V6"
    apn: str = ""
    ipv4: str = "192.0.2.2"
    ipv6: str = "2001:db8::2"
    active: bool = False

    @classmethod
    def from_config(cls, value: Dict[str, Any]) -> "PDPContext":
        return cls(
            cid=int(value.get("cid", 1)),
            pdp_type=str(value.get("type", "IPV4V6")).upper(),
            apn=str(value.get("apn", "")),
            ipv4=str(value.get("ipv4", "192.0.2.2")),
            ipv6=str(value.get("ipv6", "2001:db8::2")),
            active=bool(value.get("active", False)),
        )

    def addresses(self) -> str:
        if self.pdp_type == "IP":
            return self.ipv4
        if self.pdp_type == "IPV6":
            return self.ipv6
        return f"{self.ipv4} {self.ipv6}".strip()


@dataclass
class ATResult:
    lines: List[str] = field(default_factory=list)
    final: Optional[str] = "OK"
    prompt: bool = False
    prompt_kind: Optional[str] = None
    prompt_arg: Optional[str] = None


# -------------------------------- modem core ---------------------------------


class ModemState:
    def __init__(self, cfg: Dict[str, Any], config_path: Path, verbose: bool = True):
        self.cfg = copy.deepcopy(cfg)
        self.config_path = config_path
        self.verbose = verbose
        self.lock = threading.RLock()
        self.print_lock = threading.Lock()
        self.sessions: List["ATSession"] = []
        self.sms: Dict[int, SMS] = {}
        self.next_sms_index = 1
        self.next_message_ref = 1
        self.call: Optional[Call] = None
        self.cfun = 1
        self.network_registered = bool(cfg.get("registered", True))
        self.roaming = bool(cfg.get("roaming", False))
        self.registration_state = (5 if self.roaming else 1) if self.network_registered else 0
        self.attached = bool(cfg.get("packet_attached", True))
        self.sim_state = "SIM PIN" if cfg.get("pin_required_at_start") else str(cfg.get("sim_state", "READY"))
        self.pin_attempts = int(cfg.get("pin_attempts", 3))
        self.puk_attempts = int(cfg.get("puk_attempts", 10))
        self.pdp_contexts: Dict[int, PDPContext] = {
            ctx.cid: ctx for ctx in (PDPContext.from_config(item) for item in cfg.get("pdp_contexts", []))
        }
        self.last_reject_cause = 0
        self.cbs_serial = 1
        self.stop_event = threading.Event()
        self._ring_thread = threading.Thread(target=self._ring_worker, name="ring-worker", daemon=True)
        self._ring_thread.start()

    @property
    def rat(self) -> str:
        return str(self.cfg["rat"])

    @property
    def rat_info(self) -> Dict[str, Any]:
        return RATS[self.rat]

    @property
    def act(self) -> int:
        return int(self.rat_info["act"])

    def log(self, msg: str, kind: str = "STATE") -> None:
        if self.verbose:
            stamp = _dt.datetime.now().astimezone().strftime("%H:%M:%S.%f")[:-3]
            with self.print_lock:
                print(f"[{stamp}] [{kind:<7}] {msg}", file=sys.stderr, flush=True)

    def register_session(self, session: "ATSession") -> None:
        with self.lock:
            self.sessions.append(session)
        self.log(f"{session.label} connected (clients={len(self.sessions)})", "CONNECT")

    def unregister_session(self, session: "ATSession") -> None:
        with self.lock:
            if session in self.sessions:
                self.sessions.remove(session)
        self.log(f"{session.label} disconnected (clients={len(self.sessions)})", "CLOSE")

    def shutdown(self) -> None:
        self.stop_event.set()
        with self.lock:
            sessions = list(self.sessions)
        for session in sessions:
            session.close()

    @property
    def registered(self) -> bool:
        return self.cfun == 1 and self.registration_state in (1, 5) and self.sim_state == "READY"

    @property
    def reg_stat(self) -> int:
        if self.cfun != 1 or self.sim_state != "READY":
            return 0
        return self.registration_state

    def now(self) -> _dt.datetime:
        offset = int(self.cfg.get("clock_offset_seconds", 0))
        return _dt.datetime.now().astimezone() + _dt.timedelta(seconds=offset)

    def sessions_snapshot(self) -> List["ATSession"]:
        with self.lock:
            return list(self.sessions)

    def broadcast_urc(self, *lines: str) -> None:
        for session in self.sessions_snapshot():
            session.send_urc(*lines)

    def notify_registration(self) -> None:
        for session in self.sessions_snapshot():
            session.notify_registration()

    def set_registered(self, registered: bool, roaming: Optional[bool] = None) -> None:
        with self.lock:
            self.network_registered = registered
            if roaming is not None:
                self.roaming = roaming
            self.registration_state = (5 if self.roaming else 1) if registered else 0
            if not registered:
                self.attached = False
                for ctx in self.pdp_contexts.values():
                    ctx.active = False
        self.log(f"registration={self.reg_stat} roaming={self.roaming}")
        self.notify_registration()
        self.broadcast_urc(f"+CGEV: {'ME ATTACH' if registered else 'NW DETACH'}")

    def set_registration_state(self, state: int) -> None:
        if state not in range(0, 6):
            raise ValueError("registration state must be 0..5")
        with self.lock:
            self.registration_state = state
            self.network_registered = state in (1, 5)
            self.roaming = state == 5
            if not self.network_registered:
                self.attached = False
                for context in self.pdp_contexts.values():
                    context.active = False
        self.log(f"registration state -> {state}")
        self.notify_registration()

    def set_rat(self, rat: str) -> None:
        rat = canonical_rat(rat)
        old = self.rat
        self.cfg["rat"] = rat
        self.log(f"radio access technology {old} -> {rat} (AcT={self.act})")
        self.notify_registration()
        self.broadcast_urc(f'+CTEC: {self.act},"{rat}"')

    def sync_runtime_to_config(self) -> None:
        self.cfg["registered"] = self.network_registered
        self.cfg["roaming"] = self.roaming
        self.cfg["packet_attached"] = self.attached
        self.cfg["sim_state"] = self.sim_state
        self.cfg["pin_attempts"] = self.pin_attempts
        self.cfg["puk_attempts"] = self.puk_attempts
        self.cfg["pdp_contexts"] = [
            {
                "cid": ctx.cid, "type": ctx.pdp_type, "apn": ctx.apn,
                "ipv4": ctx.ipv4, "ipv6": ctx.ipv6, "active": ctx.active,
            }
            for ctx in sorted(self.pdp_contexts.values(), key=lambda item: item.cid)
        ]

    def save(self) -> None:
        with self.lock:
            self.sync_runtime_to_config()
            save_config(self.config_path, self.cfg)
        self.log(f"configuration saved to {self.config_path}", "CONFIG")

    def inject_sms(self, sender: Optional[str] = None, text: Optional[str] = None) -> SMS:
        sender = sender or self.cfg["incoming_number"]
        text = text if text is not None else self.cfg["incoming_sms"]
        with self.lock:
            if len(self.sms) >= int(self.cfg.get("sms_capacity", 255)):
                raise RuntimeError("SMS storage is full")
            sms = SMS(index=self.next_sms_index, sender=sender, text=text, timestamp=self.now())
            self.sms[sms.index] = sms
            self.next_sms_index += 1
            sessions = list(self.sessions)
        self.log(f"inject SMS #{sms.index} from {sender}: {text!r}", "INJECT")
        for s in sessions:
            s.notify_sms(sms)
        return sms

    def inject_cmas(self, message_id: Optional[int] = None, text: Optional[str] = None) -> None:
        message_id = int(message_id if message_id is not None else self.cfg["cmas_message_id"])
        text = text if text is not None else self.cfg["cmas_text"]
        serial = self.cbs_serial
        self.cbs_serial = (self.cbs_serial + 1) & 0xFFFF
        pdu = build_cbs_pdu(message_id, text, serial=serial)
        with self.lock:
            sessions = list(self.sessions)
        self.log(f"inject CBS/CMAS SN={serial} MID={message_id} text={text!r}", "INJECT")
        for s in sessions:
            s.notify_cbs(message_id, text, pdu)

    def inject_call(self, number: Optional[str] = None) -> bool:
        number = number or self.cfg["incoming_number"]
        with self.lock:
            if self.call is not None:
                return False
            self.call = Call(index=1, direction=1, state=4, number=number)
            sessions = list(self.sessions)
        self.log(f"inject incoming call from {number}", "INJECT")
        for s in sessions:
            s.notify_ring()
        return True

    def answer_call(self) -> bool:
        with self.lock:
            if not self.call or self.call.state not in (4, 5):
                return False
            self.call.state = 0
        self.log("call answered", "CALL")
        self.broadcast_urc("+CIEV: 4,1")
        return True

    def hangup_call(self, send_urc: bool = True) -> bool:
        with self.lock:
            if not self.call:
                return False
            self.call = None
            sessions = list(self.sessions)
        self.log("call ended", "CALL")
        if send_urc:
            for s in sessions:
                s.send_urc("NO CARRIER")
        return True

    def start_outgoing_call(self, number: str) -> bool:
        with self.lock:
            if self.call is not None:
                return False
            self.call = Call(index=1, direction=0, state=2, number=number)
        self.log(f"outgoing call to {number}", "CALL")

        def progress() -> None:
            time.sleep(0.4)
            with self.lock:
                if self.call and self.call.direction == 0 and self.call.number == number:
                    self.call.state = 3
            time.sleep(0.6)
            with self.lock:
                if self.call and self.call.direction == 0 and self.call.number == number:
                    self.call.state = 0
            self.broadcast_urc(f'+COLP: "{number}",145')

        threading.Thread(target=progress, name="call-progress", daemon=True).start()
        return True

    def _ring_worker(self) -> None:
        interval = max(0.5, float(self.cfg.get("ring_interval_seconds", 3.0)))
        while not self.stop_event.wait(interval):
            with self.lock:
                call = self.call
                sessions = list(self.sessions)
            if call and call.direction == 1 and call.state in (4, 5):
                for s in sessions:
                    s.notify_ring()

    def inject_ussd(self, text: str, dcs: int = 15, status: int = 0) -> None:
        self.log(f"inject USSD status={status} dcs={dcs}: {text!r}", "INJECT")
        for session in self.sessions_snapshot():
            if session.cusd_enabled:
                session.send_urc(f'+CUSD: {status},"{text}",{dcs}')


# ------------------------------- AT connection -------------------------------


class ATSession(threading.Thread):
    def __init__(self, modem: ModemState, conn: Any, peer: str = "unix"):
        super().__init__(daemon=True, name=f"at-{id(self):x}")
        self.modem = modem
        self.conn = conn
        self.peer = peer
        self.label = f"client-{id(self) & 0xFFFF:04x}"
        self.send_lock = threading.Lock()
        self.closed = threading.Event()
        self.buffer = bytearray()
        self.data_mode = False
        self.data_escape_buffer = bytearray()

        # Per-TE settings, because these are DTE/TA interface settings rather
        # than network-wide state.
        self.echo = True
        self.verbose_results = True
        self.quiet = False
        self.cmee = 2
        self.sms_text_mode = 0  # 0=PDU, 1=text. ModemManager normally uses PDU.
        self.charset = "IRA"
        self.cnmi = [2, 1, 2, 1, 0]
        self.creg_n = 2
        self.cgreg_n = 2
        self.cereg_n = 2
        self.c5greg_n = 2
        self.cops_mode = 0
        self.cops_format = 0
        self.clip = 1
        self.colp = 1
        self.ccwa = 1
        self.crc = 0
        self.cusd_enabled = True
        self.phonebook_storage = "SM"
        self.mute = 0
        self.volume = 3
        self.cmer = [3, 0, 0, 1, 0]
        self.cscb_mode = 0
        self.cscb_mids = "0-65535"
        self.cscb_dcss = "0-255"
        self.pending_submission: Optional[Tuple[str, str]] = None
        self.pending_payload = bytearray()

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.conn.close()
        except OSError:
            pass

    def run(self) -> None:
        self.modem.register_session(self)
        try:
            while not self.closed.is_set() and not self.modem.stop_event.is_set():
                try:
                    data = self.conn.recv(4096)
                except (ConnectionResetError, OSError):
                    break
                if not data:
                    break
                self.modem.log(f"{self.label} RX {self._wire_repr(data)}", "AT RX")
                self._feed(data)
        finally:
            self.close()
            self.modem.unregister_session(self)

    def _send(self, data: bytes) -> None:
        if self.closed.is_set():
            return
        self.modem.log(f"{self.label} TX {self._wire_repr(data)}", "AT TX")
        with self.send_lock:
            try:
                self.conn.sendall(data)
            except OSError:
                self.close()

    @staticmethod
    def _wire_repr(data: bytes) -> str:
        """Readable, lossless-enough transcript representation for console logs."""
        pieces: List[str] = []
        for byte in data:
            if byte == 13:
                pieces.append("<CR>")
            elif byte == 10:
                pieces.append("<LF>")
            elif byte == 26:
                pieces.append("<CTRL-Z>")
            elif byte == 27:
                pieces.append("<ESC>")
            elif 32 <= byte < 127:
                pieces.append(chr(byte))
            else:
                pieces.append(f"<{byte:02X}>")
        return "".join(pieces)

    def send_urc(self, *lines: str) -> None:
        if not lines:
            return
        payload = "\r\n" + "\r\n".join(lines) + "\r\n"
        self._send(payload.encode("ascii", errors="replace"))

    def _final_text(self, token: str) -> str:
        if self.verbose_results:
            return token
        numeric = {
            "OK": "0",
            "CONNECT": "1",
            "RING": "2",
            "NO CARRIER": "3",
            "ERROR": "4",
            "NO DIALTONE": "6",
            "BUSY": "7",
            "NO ANSWER": "8",
        }
        return numeric.get(token, token)

    def _reply(self, result: ATResult) -> None:
        if result.prompt:
            self.pending_submission = (result.prompt_kind or "sms", result.prompt_arg or "")
            self.pending_payload.clear()
            self._send(b"\r\n> ")
            return

        chunks: List[str] = []
        if result.lines:
            chunks.extend(result.lines)
        if result.final and not self.quiet:
            chunks.append(self._final_text(result.final))
        if chunks:
            self._send(("\r\n" + "\r\n".join(chunks) + "\r\n").encode("ascii", errors="replace"))

    def _feed(self, data: bytes) -> None:
        if self.data_mode:
            # A deliberately inert fake data plane.  The conventional escape
            # sequence is accepted without attempting guard-time enforcement.
            self.data_escape_buffer.extend(data)
            if b"+++" in self.data_escape_buffer:
                self.data_mode = False
                self.data_escape_buffer.clear()
                self._send(b"\r\nOK\r\n")
            elif len(self.data_escape_buffer) > 16:
                self.data_escape_buffer.clear()
            return
        if self.pending_submission is not None:
            self._feed_submission(data)
            return

        self.buffer.extend(data)
        while True:
            positions = [p for p in (self.buffer.find(b"\r"), self.buffer.find(b"\n")) if p >= 0]
            if not positions:
                return
            pos = min(positions)
            line = bytes(self.buffer[:pos])
            del self.buffer[: pos + 1]
            while self.buffer[:1] in (b"\r", b"\n"):
                del self.buffer[:1]
            if not line:
                continue
            self._handle_line(line)

    def _feed_submission(self, data: bytes) -> None:
        # Ctrl-Z commits an SMS submission; ESC cancels it.
        for i, b in enumerate(data):
            if b in (0x1A, 0x1B):
                self.pending_payload.extend(data[:i])
                kind, arg = self.pending_submission or ("sms", "")
                self.pending_submission = None
                payload = bytes(self.pending_payload)
                self.pending_payload.clear()
                if b == 0x1B:
                    self._reply(ATResult(final="ERROR"))
                else:
                    self._complete_submission(kind, arg, payload)
                rest = data[i + 1 :]
                if rest:
                    self._feed(rest)
                return
        self.pending_payload.extend(data)

    def _complete_submission(self, kind: str, arg: str, payload: bytes) -> None:
        if kind not in ("sms", "store"):
            self._reply(ATResult(final="ERROR"))
            return
        ref = self.modem.next_message_ref
        self.modem.next_message_ref = (ref + 1) & 0xFF
        if self.sms_text_mode:
            text = payload.decode("utf-8", errors="replace")
            self.modem.log(f"simulated outbound SMS to {arg}: {text!r}", "SMS")
            if kind == "store":
                with self.modem.lock:
                    index = self.modem.next_sms_index
                    self.modem.next_sms_index += 1
                    self.modem.sms[index] = SMS(
                        index=index, sender="", recipient=arg, text=text,
                        timestamp=self.modem.now(), unread=False, outgoing=True,
                    )
                self._reply(ATResult(lines=[f"+CMGW: {index}"], final="OK"))
                return
        else:
            pdu = payload.decode("ascii", errors="replace").strip()
            self.modem.log(f"simulated outbound SMS PDU len={arg}: {pdu}", "SMS")
            if kind == "store":
                with self.modem.lock:
                    index = self.modem.next_sms_index
                    self.modem.next_sms_index += 1
                    self.modem.sms[index] = SMS(
                        index=index, sender="", recipient="PDU", text=pdu,
                        timestamp=self.modem.now(), unread=False, outgoing=True,
                    )
                self._reply(ATResult(lines=[f"+CMGW: {index}"], final="OK"))
                return
        self._reply(ATResult(lines=[f"+CMGS: {ref}"], final="OK"))

    def _handle_line(self, raw: bytes) -> None:
        line = raw.decode("ascii", errors="ignore").strip()
        if not line:
            return
        if self.echo:
            self._send(raw + b"\r\n")
        if len(line) < 2 or line[:2].upper() != "AT":
            self._reply(ATResult(final="ERROR"))
            return
        result = self.execute(line[2:])
        self._reply(result)

    def _unsupported(self) -> ATResult:
        # CME 4 = operation not supported. Keep plain ERROR if CMEE is disabled.
        if self.cmee:
            return ATResult(final="+CME ERROR: 4")
        return ATResult(final="ERROR")

    def execute(self, body: str) -> ATResult:
        body = body.strip()
        if not body:
            return ATResult()

        # A voice dial command conventionally ends in ';', and that semicolon is
        # part of ATD syntax. Other semicolons can chain extended commands.
        if body[:1].upper() == "D" and body.endswith(";"):
            parts = [body]
        else:
            parts = [p for p in body.split(";") if p != ""]
            if not parts:
                return ATResult()

        all_lines: List[str] = []
        for part in parts:
            r = self._execute_one(part.strip())
            all_lines.extend(r.lines)
            if r.prompt:
                if len(parts) != 1:
                    return ATResult(lines=all_lines, final="ERROR")
                r.lines = all_lines
                return r
            if r.final not in (None, "OK"):
                return ATResult(lines=all_lines, final=r.final)
        return ATResult(lines=all_lines, final="OK")

    def _execute_one(self, token: str) -> ATResult:
        u = token.upper()
        cfg = self.modem.cfg

        # ----- basic V.25ter-ish commands -----
        if u in ("", "Z", "Z0", "&F", "&F0"):
            if u in ("Z", "&F"):
                self.echo = True
                self.verbose_results = True
                self.quiet = False
                self.cmee = 0
                self.sms_text_mode = 0
            return ATResult()
        if re.fullmatch(r"E[01]", u):
            self.echo = u == "E1"
            return ATResult()
        if re.fullmatch(r"Q[01]", u):
            self.quiet = u == "Q1"
            return ATResult()
        if re.fullmatch(r"V[01]", u):
            self.verbose_results = u == "V1"
            return ATResult()
        if u in ("I", "I0", "I1", "I2", "I3", "I4"):
            return ATResult(lines=[f"{cfg['manufacturer']} {cfg['model']}", f"Revision: {cfg['revision']}"])
        if u == "+IPR?":
            return ATResult(lines=["+IPR: 115200"])
        if u == "+IPR=?":
            return ATResult(lines=["+IPR: (0,9600,19200,38400,57600,115200,230400)"])
        if re.fullmatch(r"\+IPR=\d+", u) or u in ("&C0", "&C1", "&D0", "&D1", "&D2", "&W", "&W0"):
            return ATResult()
        if u.startswith("S0=") or u == "S0?":
            return ATResult(lines=["0"] if u == "S0?" else [])

        # ----- identification / capabilities -----
        if u in ("+GCAP", "+GCAP?"):
            return ATResult(lines=["+GCAP: +CGSM,+DS,+ES,+FCLASS"])
        if u in ("+CGMI", "+GMI"):
            return ATResult(lines=[cfg["manufacturer"]])
        if u in ("+CGMM", "+GMM"):
            return ATResult(lines=[cfg["model"]])
        if u in ("+CGMR", "+GMR"):
            return ATResult(lines=[cfg["revision"]])
        if u in ("+CGSN", "+GSN", "+GSN?", "+CGSN=1"):
            return ATResult(lines=[cfg["imei"]])
        if u == "+CGSN=2":
            return ATResult(lines=[str(cfg.get("imeisv", cfg["imei"] + "0"))])
        if u in ("+SN", "+SN?"):
            return ATResult(lines=[str(cfg.get("serial_number", cfg["imei"]))])
        if u == "+CIMI":
            return ATResult(lines=[cfg["imsi"]])
        if u in ("+CCID", "+ICCID", "+ICCID?"):
            prefix = "+CCID: " if u == "+CCID" else "+ICCID: "
            return ATResult(lines=[prefix + cfg["iccid"]])
        if u == "+CNUM":
            return ATResult(lines=[f'+CNUM: "","{cfg["msisdn"]}",145'])
        if u in ("+EID", "+EID?"):
            return ATResult(lines=[f'+EID: {cfg.get("eid", "")}'])

        # Minimal transparent SIM EF_ICCID read for software that asks via CRSM.
        m = re.fullmatch(r"\+CRSM=176,12258,0,0,(?:10|0)(?:,.*)?", u)
        if m:
            return ATResult(lines=[f'+CRSM: 144,0,"{iccid_ef_bytes(cfg["iccid"])}"'])

        # ----- equipment / SIM status -----
        if u == "+CPIN?":
            return ATResult(lines=[f"+CPIN: {self.modem.sim_state}"])
        if u == "+CPIN=?":
            return ATResult()
        m = re.fullmatch(r'\+CPIN="([^"]+)"(?:,"([^"]+)")?', token, re.IGNORECASE)
        if m:
            if self.modem.sim_state == "SIM PIN":
                if m.group(1) == str(cfg.get("sim_pin", "1234")):
                    self.modem.sim_state = "READY"
                    self.modem.broadcast_urc("+CPIN: READY")
                    self.modem.notify_registration()
                    return ATResult()
                self.modem.pin_attempts -= 1
                if self.modem.pin_attempts <= 0:
                    self.modem.sim_state = "SIM PUK"
                return ATResult(final="+CME ERROR: 16" if self.cmee else "ERROR")
            if self.modem.sim_state == "SIM PUK" and m.group(2):
                if m.group(1) == str(cfg.get("sim_puk", "12345678")):
                    cfg["sim_pin"] = m.group(2)
                    self.modem.sim_state = "READY"
                    self.modem.pin_attempts = int(cfg.get("pin_attempts", 3))
                    self.modem.broadcast_urc("+CPIN: READY")
                    return ATResult()
                self.modem.puk_attempts -= 1
                return ATResult(final="+CME ERROR: 16" if self.cmee else "ERROR")
            return ATResult(final="+CME ERROR: 3" if self.cmee else "ERROR")
        if u == "+CPINR":
            return ATResult(lines=[f'+CPINR: "SIM PIN",{self.modem.pin_attempts}', f'+CPINR: "SIM PUK",{self.modem.puk_attempts}'])
        if u == '+CLCK="SC",2':
            enabled = int(self.modem.sim_state == "SIM PIN" or bool(cfg.get("pin_required_at_start")))
            return ATResult(lines=[f"+CLCK: {enabled}"])
        m = re.fullmatch(r'\+CLCK="SC",([01]),"([^"]+)"', token, re.IGNORECASE)
        if m:
            if m.group(2) != str(cfg.get("sim_pin", "1234")):
                return ATResult(final="+CME ERROR: 16" if self.cmee else "ERROR")
            cfg["pin_required_at_start"] = bool(int(m.group(1)))
            return ATResult()
        if u == "+CFUN?":
            return ATResult(lines=[f"+CFUN: {self.modem.cfun}"])
        if u == "+CFUN=?":
            return ATResult(lines=["+CFUN: (0,1,4)"])
        m = re.fullmatch(r"\+CFUN=(0|1|4)(?:,\d+)?", u)
        if m:
            self.modem.cfun = int(m.group(1))
            self.modem.attached = self.modem.cfun == 1 and self.modem.network_registered
            self.modem.notify_registration()
            return ATResult()
        if u == "+CPAS":
            with self.modem.lock:
                call = self.modem.call
            if not call:
                pas = 0
            elif call.state in (4, 5):
                pas = 3
            else:
                pas = 4
            return ATResult(lines=[f"+CPAS: {pas}"])
        if u == "+CBC":
            charging = 1 if cfg.get("battery_charging", True) else 0
            return ATResult(lines=[f'+CBC: {charging},{int(cfg.get("battery_percent", 85))},{int(cfg.get("battery_mv", 4060))}'])
        if u == "+CCLK?":
            return ATResult(lines=[f'+CCLK: "{text_timestamp(self.modem.now())}"'])
        m = re.fullmatch(r'\+CCLK="(\d\d/\d\d/\d\d,\d\d:\d\d:\d\d)([+-]\d\d)?"', token, re.IGNORECASE)
        if m:
            try:
                requested = _dt.datetime.strptime(m.group(1), "%y/%m/%d,%H:%M:%S").astimezone()
            except ValueError:
                return ATResult(final="+CME ERROR: 50" if self.cmee else "ERROR")
            cfg["clock_offset_seconds"] = int((requested - _dt.datetime.now().astimezone()).total_seconds())
            return ATResult()

        # ----- error reporting / charset -----
        if u == "+CMEE?":
            return ATResult(lines=[f"+CMEE: {self.cmee}"])
        if u == "+CMEE=?":
            return ATResult(lines=["+CMEE: (0-2)"])
        m = re.fullmatch(r"\+CMEE=(0|1|2)", u)
        if m:
            self.cmee = int(m.group(1))
            return ATResult()
        if u == "+CSCS?":
            return ATResult(lines=[f'+CSCS: "{self.charset}"'])
        if u == "+CSCS=?":
            return ATResult(lines=['+CSCS: ("IRA","GSM","UCS2")'])
        m = re.fullmatch(r'\+CSCS="(IRA|GSM|UCS2)"', u)
        if m:
            self.charset = m.group(1)
            return ATResult()

        # ----- network registration and operator -----
        reg_modes = {
            "+CREG": ("creg_n", 2),
            "+CGREG": ("cgreg_n", 2),
            "+CEREG": ("cereg_n", 5),
            "+C5GREG": ("c5greg_n", 5),
        }
        for stem, (attribute, max_mode) in reg_modes.items():
            if u == stem + "=?":
                return ATResult(lines=[f"{stem}: (0-{max_mode})"])
            if u == stem + "?":
                return ATResult(lines=[self._registration_line(stem, getattr(self, attribute))])
            match = re.fullmatch(re.escape(stem) + rf"=(\d)", u)
            if match:
                mode = int(match.group(1))
                if mode > max_mode:
                    return ATResult(final="+CME ERROR: 50" if self.cmee else "ERROR")
                setattr(self, attribute, mode)
                return ATResult()

        if u == "+CSQ":
            rssi = cfg["rssi"] if self.modem.registered else 99
            return ATResult(lines=[f"+CSQ: {rssi},{cfg['ber']}"])
        if u == "+CSQ=?":
            return ATResult(lines=["+CSQ: (0-31,99),(0-7,99)"])
        if u == "+CESQ":
            rxlev = min(63, max(0, int(cfg.get("rssi", 99)) * 2)) if int(cfg.get("rssi", 99)) != 99 else 99
            return ATResult(lines=[f'+CESQ: {rxlev},{cfg.get("ber", 99)},{cfg.get("rscp", 255)},{cfg.get("ecno", 255)},{cfg.get("rsrq", 255)},{cfg.get("rsrp", 255)}'])
        if u == "+CESQ=?":
            return ATResult(lines=["+CESQ: (0-63,99),(0-7,99),(0-96,255),(0-49,255),(0-34,255),(0-97,255)"])

        if u == "+COPS?":
            if self.cops_format == 2:
                oper = cfg["plmn"]
            elif self.cops_format == 1:
                oper = cfg["carrier_short"]
            else:
                oper = cfg["carrier_long"]
            return ATResult(lines=[f'+COPS: {self.cops_mode},{self.cops_format},"{oper}",{self.modem.act}'])
        if u == "+COPS=?":
            entries = []
            for operator in cfg.get("available_operators", []):
                try:
                    op_act = RATS[canonical_rat(str(operator.get("rat", self.modem.rat)))]["act"]
                except ValueError:
                    op_act = self.modem.act
                entries.append(
                    f'({int(operator.get("status", 1))},"{operator.get("long", "")}",'
                    f'"{operator.get("short", "")}","{operator.get("plmn", "")}",{op_act})'
                )
            if not entries:
                entries.append(f'(2,"{cfg["carrier_long"]}","{cfg["carrier_short"]}","{cfg["plmn"]}",{self.modem.act})')
            return ATResult(lines=[f'+COPS: {",".join(entries)},,(0-4),(0-2)'])
        m = re.fullmatch(r"\+COPS=3,(0|1|2)", u)
        if m:
            self.cops_format = int(m.group(1))
            return ATResult()
        if re.fullmatch(r"\+COPS=0(?:,\d+)?", u):
            self.cops_mode = 0
            return ATResult()
        m = re.fullmatch(r'\+COPS=1,2,"([0-9]{5,6})"(?:,\d+)?', u)
        if m:
            if m.group(1) != cfg["plmn"]:
                return ATResult(final="+CME ERROR: 30" if self.cmee else "ERROR")
            self.cops_mode = 1
            return ATResult()
        if u == "+COPS=2":
            self.cops_mode = 2
            self.modem.set_registered(False)
            return ATResult()

        if u == "+WS46?":
            return ATResult(lines=["+WS46: 22"])
        if u == "+WS46=?":
            return ATResult(lines=["+WS46: (12,22,25)"])
        if re.fullmatch(r"\+WS46=(12|22|25)", u):
            return ATResult()
        if u in ("+CTEC?", "+CTEC"):
            return ATResult(lines=[f'+CTEC: {self.modem.act},"{self.modem.rat}"'])
        if u == "+CTEC=?":
            return ATResult(lines=["+CTEC: " + ",".join(str(info["act"]) for info in RATS.values())])
        m = re.fullmatch(r"\+CTEC=(\d+)", u)
        if m:
            wanted = int(m.group(1))
            for name, info in RATS.items():
                if int(info["act"]) == wanted:
                    self.modem.set_rat(name)
                    return ATResult()
            return ATResult(final="+CME ERROR: 50" if self.cmee else "ERROR")

        # ----- packet service: successful control plane, isolated data plane -----
        if u == "+CGATT?":
            return ATResult(lines=[f"+CGATT: {1 if self.modem.attached and self.modem.registered else 0}"])
        if u == "+CGATT=?":
            return ATResult(lines=["+CGATT: (0,1)"])
        m = re.fullmatch(r"\+CGATT=(0|1)", u)
        if m:
            want = int(m.group(1))
            if want and not self.modem.registered:
                return ATResult(final="+CME ERROR: 30" if self.cmee else "ERROR")
            self.modem.attached = bool(want)
            if not want:
                for context in self.modem.pdp_contexts.values():
                    context.active = False
                self.send_urc("+CGEV: ME DETACH")
            else:
                self.send_urc("+CGEV: ME ATTACH")
            return ATResult()
        if u == "+CGCLASS?":
            return ATResult(lines=['+CGCLASS: "A"'])
        if u == "+CGCLASS=?":
            return ATResult(lines=['+CGCLASS: ("A","B","CG")'])
        if u == "+CGDCONT?":
            lines = []
            for context in sorted(self.modem.pdp_contexts.values(), key=lambda item: item.cid):
                lines.append(f'+CGDCONT: {context.cid},"{context.pdp_type}","{context.apn}","{context.addresses()}",0,0')
            return ATResult(lines=lines)
        if u == "+CGDCONT=?":
            return ATResult(lines=['+CGDCONT: (1-8),"IP",,,(0-2),(0-4)', '+CGDCONT: (1-8),"IPV6",,,(0-2),(0-4)', '+CGDCONT: (1-8),"IPV4V6",,,(0-2),(0-4)'])
        m = re.fullmatch(r'\+CGDCONT=(\d+),"(IP|IPV6|IPV4V6)","([^"]*)"(?:,"([^"]*)")?.*', token, re.IGNORECASE)
        if m:
            cid = int(m.group(1))
            if not 1 <= cid <= 16:
                return ATResult(final="+CME ERROR: 50" if self.cmee else "ERROR")
            old = self.modem.pdp_contexts.get(cid)
            self.modem.pdp_contexts[cid] = PDPContext(
                cid=cid,
                pdp_type=m.group(2).upper(),
                apn=m.group(3),
                ipv4=old.ipv4 if old else f"192.0.2.{min(254, cid + 1)}",
                ipv6=old.ipv6 if old else f"2001:db8::{cid + 1:x}",
                active=False,
            )
            return ATResult()
        if u == "+CGACT?":
            return ATResult(lines=[f"+CGACT: {ctx.cid},{int(ctx.active)}" for ctx in sorted(self.modem.pdp_contexts.values(), key=lambda item: item.cid)])
        if u == "+CGACT=?":
            return ATResult(lines=["+CGACT: (0,1),(1-8)"])
        m = re.fullmatch(r"\+CGACT=([01]),(\d+)", u)
        if m:
            active, cid = bool(int(m.group(1))), int(m.group(2))
            context = self.modem.pdp_contexts.get(cid)
            if not context:
                return ATResult(final="+CME ERROR: 50" if self.cmee else "ERROR")
            if active and (not self.modem.registered or not self.modem.attached):
                return ATResult(final="+CME ERROR: 30" if self.cmee else "ERROR")
            context.active = active
            self.send_urc(f"+CGEV: {'ME PDN ACT' if active else 'ME PDN DEACT'} {cid}")
            return ATResult()
        m = re.fullmatch(r"\+CGPADDR(?:=(\d+))?", u)
        if m:
            requested = int(m.group(1)) if m.group(1) else None
            contexts = [self.modem.pdp_contexts[requested]] if requested in self.modem.pdp_contexts else self.modem.pdp_contexts.values()
            return ATResult(lines=[f'+CGPADDR: {ctx.cid},"{ctx.addresses()}"' for ctx in contexts])
        m = re.fullmatch(r"\+CGCONTRDP(?:=(\d+))?", u)
        if m:
            requested = int(m.group(1)) if m.group(1) else None
            contexts = [ctx for ctx in self.modem.pdp_contexts.values() if ctx.active and (requested is None or ctx.cid == requested)]
            dns = cfg.get("fake_dns", ["192.0.2.53", "2001:db8::53"])
            return ATResult(lines=[f'+CGCONTRDP: {ctx.cid},5,"{ctx.apn}","{ctx.addresses()}","","{dns[0]}","{dns[-1]}"' for ctx in contexts])
        if u in ("+CGEREP?",):
            return ATResult(lines=["+CGEREP: 1,0"])
        if u == "+CGEREP=?":
            return ATResult(lines=["+CGEREP: (0-2),(0,1)"])
        if u.startswith("+CGEREP="):
            return ATResult()
        if u.startswith("+CGDATA"):
            self.data_mode = True
            return ATResult(final="CONNECT")
        if u.startswith("D*99"):
            context = self.modem.pdp_contexts.get(1)
            if context:
                context.active = True
            self.data_mode = True
            return ATResult(final="CONNECT")

        # ----- SMS -----
        if u == "+CSMS?":
            return ATResult(lines=["+CSMS: 1,1,1,1"])
        if u == "+CSMS=?":
            return ATResult(lines=["+CSMS: (0,1)"])
        if re.fullmatch(r"\+CSMS=[01]", u):
            return ATResult(lines=["+CSMS: 1,1,1"])

        if u == "+CPMS=?":
            return ATResult(lines=['+CPMS: ("SM","ME"),("SM","ME"),("SM","ME")'])
        if u == "+CPMS?":
            used = len(self.modem.sms)
            capacity = int(cfg.get("sms_capacity", 255))
            return ATResult(lines=[f'+CPMS: "SM",{used},{capacity},"SM",{used},{capacity},"SM",{used},{capacity}'])
        if u.startswith("+CPMS="):
            used = len(self.modem.sms)
            capacity = int(cfg.get("sms_capacity", 255))
            return ATResult(lines=[f"+CPMS: {used},{capacity},{used},{capacity},{used},{capacity}"])

        if u == "+CMGF?":
            return ATResult(lines=[f"+CMGF: {self.sms_text_mode}"])
        if u == "+CMGF=?":
            return ATResult(lines=["+CMGF: (0,1)"])
        m = re.fullmatch(r"\+CMGF=(0|1)", u)
        if m:
            self.sms_text_mode = int(m.group(1))
            return ATResult()

        if u == "+CNMI?":
            return ATResult(lines=["+CNMI: " + ",".join(map(str, self.cnmi))])
        if u == "+CNMI=?":
            return ATResult(lines=["+CNMI: (0-3),(0-3),(0,2),(0-2),(0,1)"])
        m = re.fullmatch(r"\+CNMI=(\d+),(\d+),(\d+),(\d+),(\d+)", u)
        if m:
            vals = list(map(int, m.groups()))
            if vals[0] > 3 or vals[1] > 3 or vals[2] not in (0, 2) or vals[3] > 2 or vals[4] > 1:
                return ATResult(final="+CMS ERROR: 302" if self.cmee else "ERROR")
            self.cnmi = vals
            return ATResult()

        if u == "+CSCA?":
            return ATResult(lines=[f'+CSCA: "{cfg["smsc"]}",145'])
        if u == "+CSCA=?":
            return ATResult()
        if u.startswith("+CSCA="):
            return ATResult()
        if u == "+CSMP?":
            return ATResult(lines=["+CSMP: 17,167,0,0"])
        if u.startswith("+CSMP="):
            return ATResult()
        if u == "+CSDH?":
            return ATResult(lines=["+CSDH: 0"])
        if re.fullmatch(r"\+CSDH=[01]", u):
            return ATResult()
        if u == "+CNMA" or u.startswith("+CNMA="):
            return ATResult()

        if u == "+CSCB?":
            return ATResult(lines=[f'+CSCB: {self.cscb_mode},"{self.cscb_mids}","{self.cscb_dcss}"'])
        if u == "+CSCB=?":
            return ATResult(lines=["+CSCB: (0,1)"])
        m = re.fullmatch(r'\+CSCB=(0|1)(?:,"([^"]*)")?(?:,"([^"]*)")?', token, re.IGNORECASE)
        if m:
            self.cscb_mode = int(m.group(1))
            if m.group(2) is not None:
                self.cscb_mids = m.group(2)
            if m.group(3) is not None:
                self.cscb_dcss = m.group(3)
            return ATResult()

        if u.startswith("+CMGR="):
            try:
                idx = int(token.split("=", 1)[1].split(",", 1)[0])
            except ValueError:
                return ATResult(final="+CMS ERROR: 321" if self.cmee else "ERROR")
            return self._cmgr(idx)

        if u == "+CMGL=?":
            return ATResult(lines=['+CMGL: ("REC UNREAD","REC READ","STO UNSENT","STO SENT","ALL")'] if self.sms_text_mode else ["+CMGL: (0-4)"])
        if u.startswith("+CMGL"):
            return self._cmgl()

        m = re.fullmatch(r"\+CMGD=(\d+)(?:,(\d+))?", u)
        if m:
            idx = int(m.group(1))
            delflag = int(m.group(2) or 0)
            with self.modem.lock:
                if delflag in (1, 2, 3, 4):
                    doomed = []
                    for sms_index, sms in self.modem.sms.items():
                        is_read = not sms.unread and not sms.outgoing
                        is_sent = sms.outgoing
                        if (delflag >= 1 and is_read) or (delflag >= 2 and is_sent) or delflag == 4:
                            doomed.append(sms_index)
                    for sms_index in doomed:
                        del self.modem.sms[sms_index]
                    return ATResult()
                if idx not in self.modem.sms:
                    return ATResult(final="+CMS ERROR: 321" if self.cmee else "ERROR")
                del self.modem.sms[idx]
            return ATResult()
        if u == "+CMGD=?":
            return ATResult(lines=[f'+CMGD: (1-{int(cfg.get("sms_capacity", 255))}),(0-4)'])

        if self.sms_text_mode:
            m = re.fullmatch(r'\+CMGS="([^"]+)"(?:,\d+)?', token, re.IGNORECASE)
            if m:
                return ATResult(prompt=True, prompt_kind="sms", prompt_arg=m.group(1), final=None)
        else:
            m = re.fullmatch(r"\+CMGS=(\d+)", u)
            if m:
                return ATResult(prompt=True, prompt_kind="sms", prompt_arg=m.group(1), final=None)
        if u == "+CMGS=?":
            return ATResult()
        if self.sms_text_mode:
            m = re.fullmatch(r'\+CMGW="([^"]+)"(?:,\d+)?', token, re.IGNORECASE)
            if m:
                return ATResult(prompt=True, prompt_kind="store", prompt_arg=m.group(1), final=None)
        else:
            m = re.fullmatch(r"\+CMGW=(\d+)", u)
            if m:
                return ATResult(prompt=True, prompt_kind="store", prompt_arg=m.group(1), final=None)
        if u == "+CMGW=?":
            return ATResult()
        m = re.fullmatch(r"\+CMSS=(\d+)(?:,\"([^\"]+)\")?.*", token, re.IGNORECASE)
        if m:
            index = int(m.group(1))
            if index not in self.modem.sms:
                return ATResult(final="+CMS ERROR: 321" if self.cmee else "ERROR")
            ref = self.modem.next_message_ref
            self.modem.next_message_ref = (ref + 1) & 0xFF
            return ATResult(lines=[f"+CMSS: {ref}"])
        if u == "+CMMS?":
            return ATResult(lines=["+CMMS: 0"])
        if u == "+CMMS=?":
            return ATResult(lines=["+CMMS: (0-2)"])
        if re.fullmatch(r"\+CMMS=[0-2]", u):
            return ATResult()

        # ----- voice -----
        if u == "+CLIP?":
            return ATResult(lines=[f"+CLIP: {self.clip},1"])
        if u == "+CLIP=?":
            return ATResult(lines=["+CLIP: (0,1)"])
        m = re.fullmatch(r"\+CLIP=(0|1)", u)
        if m:
            self.clip = int(m.group(1))
            return ATResult()
        if u == "+COLP?":
            return ATResult(lines=[f"+COLP: {self.colp},1"])
        if u == "+COLP=?":
            return ATResult(lines=["+COLP: (0,1)"])
        m = re.fullmatch(r"\+COLP=(0|1)", u)
        if m:
            self.colp = int(m.group(1))
            return ATResult()
        if u == "+CLIR?":
            return ATResult(lines=["+CLIR: 0,1"])
        if u == "+CLIR=?":
            return ATResult(lines=["+CLIR: (0-2)"])
        if re.fullmatch(r"\+CLIR=[0-2]", u):
            return ATResult()
        if u == "+CCWA?":
            return ATResult(lines=[f"+CCWA: {self.ccwa}"])
        if u == "+CCWA=?":
            return ATResult(lines=["+CCWA: (0,1)"])
        m = re.fullmatch(r"\+CCWA=(0|1)(?:,.*)?", u)
        if m:
            self.ccwa = int(m.group(1))
            return ATResult()
        if u == "+CRC?":
            return ATResult(lines=[f"+CRC: {self.crc}"])
        if u == "+CRC=?":
            return ATResult(lines=["+CRC: (0,1)"])
        m = re.fullmatch(r"\+CRC=(0|1)", u)
        if m:
            self.crc = int(m.group(1))
            return ATResult()
        if u == "+CLCC":
            with self.modem.lock:
                call = self.modem.call
            if not call:
                return ATResult()
            return ATResult(lines=[f'+CLCC: {call.index},{call.direction},{call.state},0,0,"{call.number}",145,"",{call.multiparty}'])
        if u == "+CHLD=?":
            return ATResult(lines=["+CHLD: (0,1,1x,2,2x,3,4)"])
        if re.fullmatch(r"\+CHLD=[0-4](?:\d+)?", u):
            return ATResult()
        if re.fullmatch(r"\+VTS=.?", token, re.IGNORECASE) or re.fullmatch(r'\+VTS=".?"', token, re.IGNORECASE):
            return ATResult()
        if u in ("A", "+CHUP") or u == "H" or u == "H0":
            if u == "A":
                if not self.modem.answer_call():
                    return ATResult(final="NO CARRIER")
                return ATResult()
            self.modem.hangup_call(send_urc=False)
            return ATResult()
        if u.startswith("D") and u.endswith(";"):
            number = token[1:-1].strip()
            if not number:
                return ATResult(final="NO DIALTONE")
            if not self.modem.start_outgoing_call(number):
                return ATResult(final="BUSY")
            return ATResult()

        # ----- supplementary services, audio and USIM phonebook -----
        if u == "+CUSD?":
            return ATResult(lines=[f"+CUSD: {int(self.cusd_enabled)}"])
        if u == "+CUSD=?":
            return ATResult(lines=["+CUSD: (0,1,2)"])
        m = re.fullmatch(r'\+CUSD=([012])(?:,"([^"]*)")?(?:,(\d+))?', token, re.IGNORECASE)
        if m:
            mode = int(m.group(1))
            self.cusd_enabled = mode == 1
            if mode == 1 and m.group(2) is not None:
                code = m.group(2)
                # Local, deterministic reply; it never reaches an operator.
                self.send_urc(f'+CUSD: 0,"Grizzco service reply for {code}",{int(m.group(3) or 15)}')
            return ATResult()
        if u == "+CMUT?":
            return ATResult(lines=[f"+CMUT: {self.mute}"])
        if u == "+CMUT=?":
            return ATResult(lines=["+CMUT: (0,1)"])
        m = re.fullmatch(r"\+CMUT=([01])", u)
        if m:
            self.mute = int(m.group(1))
            return ATResult()
        if u == "+CLVL?":
            return ATResult(lines=[f"+CLVL: {self.volume}"])
        if u == "+CLVL=?":
            return ATResult(lines=["+CLVL: (0-5)"])
        m = re.fullmatch(r"\+CLVL=([0-5])", u)
        if m:
            self.volume = int(m.group(1))
            return ATResult()

        if u == "+CPBS?":
            entries = len(cfg.get("phonebook", []))
            return ATResult(lines=[f'+CPBS: "{self.phonebook_storage}",{entries},250'])
        if u == "+CPBS=?":
            return ATResult(lines=['+CPBS: ("SM","ME","ON")'])
        m = re.fullmatch(r'\+CPBS="(SM|ME|ON)"', u)
        if m:
            self.phonebook_storage = m.group(1)
            return ATResult()
        if u == "+CPBR=?":
            return ATResult(lines=["+CPBR: (1-250),40,16"])
        m = re.fullmatch(r"\+CPBR=(\d+)(?:,(\d+))?", u)
        if m:
            first, last = int(m.group(1)), int(m.group(2) or m.group(1))
            lines = []
            for entry in cfg.get("phonebook", []):
                if first <= int(entry.get("index", 0)) <= last:
                    lines.append(f'+CPBR: {entry["index"]},"{entry["number"]}",{entry.get("type", 145)},"{entry.get("name", "")}"')
            return ATResult(lines=lines)
        m = re.fullmatch(r'\+CPBF="([^"]*)"', token, re.IGNORECASE)
        if m:
            needle = m.group(1).casefold()
            lines = [
                f'+CPBF: {entry["index"]},"{entry["number"]}",{entry.get("type", 145)},"{entry.get("name", "")}"'
                for entry in cfg.get("phonebook", []) if needle in str(entry.get("name", "")).casefold()
            ]
            return ATResult(lines=lines)

        # ----- indicators / harmless initialization commands -----
        if u == "+CIND=?":
            return ATResult(lines=['+CIND: ("battchg",(0-5)),("signal",(0-5)),("service",(0,1)),("call",(0,1)),("roam",(0,1))'])
        if u == "+CIND?":
            signal_level = max(0, min(5, int(cfg["rssi"] * 5 / 31))) if self.modem.registered else 0
            with self.modem.lock:
                in_call = int(self.modem.call is not None)
            battery_level = max(0, min(5, round(int(cfg.get("battery_percent", 85)) / 20)))
            return ATResult(lines=[f"+CIND: {battery_level},{signal_level},{int(self.modem.registered)},{in_call},{int(self.modem.roaming)}"])
        if u == "+CMER?":
            return ATResult(lines=["+CMER: " + ",".join(map(str, self.cmer))])
        if u == "+CMER=?":
            return ATResult(lines=["+CMER: (0-3),(0),(0),(0,1),(0,1)"])
        m = re.fullmatch(r"\+CMER=(\d+),(\d+),(\d+),(\d+)(?:,(\d+))?", u)
        if m:
            self.cmer = [int(value or 0) for value in m.groups()]
            return ATResult()
        if u in ("+CTZU?", "+CTZR?"):
            return ATResult(lines=[f"{u[:-1]} 1"])
        if re.fullmatch(r"\+CTZ[UR]=[01]", u):
            return ATResult()

        return self._unsupported()

    def _registration_line(self, stem: str, n: int) -> str:
        cfg = self.modem.cfg
        stat = self.modem.reg_stat
        if n >= 2:
            area = cfg["tac"] if stem in ("+CEREG", "+C5GREG") else cfg["lac"]
            extra = ""
            if stem in ("+CEREG", "+C5GREG") and n >= 5:
                # cause_type, reject_cause, active-time, periodic-TAU placeholders
                extra = ',0,0,"00100001","00000110"'
            return f'{stem}: {n},{stat},"{area}","{cfg["cell_id"]}",{self.modem.act}{extra}'
        return f"{stem}: {n},{stat}"

    def notify_registration(self) -> None:
        for stem, mode in (
            ("+CREG", self.creg_n), ("+CGREG", self.cgreg_n),
            ("+CEREG", self.cereg_n), ("+C5GREG", self.c5greg_n),
        ):
            if mode:
                line = self._registration_line(stem, mode)
                # URCs omit the reporting-mode field present in read responses.
                prefix = f"{stem}: {mode},"
                if line.startswith(prefix):
                    line = f"{stem}: " + line[len(prefix):]
                self.send_urc(line)

    def _cmgr(self, idx: int) -> ATResult:
        with self.modem.lock:
            sms = self.modem.sms.get(idx)
            if not sms:
                return ATResult(final="+CMS ERROR: 321" if self.cmee else "ERROR")
            was_unread = sms.unread
            sms.unread = False
        if self.sms_text_mode:
            if sms.outgoing:
                status = "STO SENT"
                number = sms.recipient
            else:
                status = "REC UNREAD" if was_unread else "REC READ"
                number = sms.sender
            return ATResult(
                lines=[
                    f'+CMGR: "{status}","{number}",,"{text_timestamp(sms.timestamp)}"',
                    sms.text,
                ]
            )
        if sms.outgoing and sms.recipient == "PDU":
            return ATResult(lines=[f"+CMGR: 3,,{len(sms.text) // 2}", sms.text])
        pdu, length = build_sms_deliver_pdu(sms.sender, sms.text, sms.timestamp)
        status = 0 if was_unread else 1
        return ATResult(lines=[f"+CMGR: {status},,{length}", pdu])

    def _cmgl(self) -> ATResult:
        lines: List[str] = []
        with self.modem.lock:
            messages = list(sorted(self.modem.sms.values(), key=lambda s: s.index))
        for sms in messages:
            if self.sms_text_mode:
                if sms.outgoing:
                    status = "STO SENT"
                    number = sms.recipient
                else:
                    status = "REC UNREAD" if sms.unread else "REC READ"
                    number = sms.sender
                lines.extend(
                    [
                        f'+CMGL: {sms.index},"{status}","{number}",,"{text_timestamp(sms.timestamp)}"',
                        sms.text,
                    ]
                )
            else:
                if sms.outgoing and sms.recipient == "PDU":
                    lines.extend([f"+CMGL: {sms.index},3,,{len(sms.text) // 2}", sms.text])
                    continue
                pdu, length = build_sms_deliver_pdu(sms.sender, sms.text, sms.timestamp)
                status = 0 if sms.unread else 1
                lines.extend([f"+CMGL: {sms.index},{status},,{length}", pdu])
        return ATResult(lines=lines)

    def notify_sms(self, sms: SMS) -> None:
        mt = self.cnmi[1]
        if mt == 2:
            if self.sms_text_mode:
                self.send_urc(
                    f'+CMT: "{sms.sender}",,"{text_timestamp(sms.timestamp)}"',
                    sms.text,
                )
            else:
                pdu, length = build_sms_deliver_pdu(sms.sender, sms.text, sms.timestamp)
                self.send_urc(f"+CMT: ,{length}", pdu)
        elif mt in (1, 3):
            self.send_urc(f'+CMTI: "SM",{sms.index}')

    def _cscb_accepts(self, message_id: int) -> bool:
        # Prototype parser for lists/ranges such as "4370,4371-4399".
        try:
            selected = False
            for part in self.cscb_mids.split(","):
                part = part.strip()
                if not part:
                    continue
                if "-" in part:
                    a, b = map(int, part.split("-", 1))
                    if a <= message_id <= b:
                        selected = True
                elif int(part) == message_id:
                    selected = True
            return selected if self.cscb_mode == 0 else not selected
        except ValueError:
            return True

    def notify_cbs(self, message_id: int, text: str, pdu_hex: str) -> None:
        bm = self.cnmi[2]
        if bm != 2 or not self._cscb_accepts(message_id):
            return
        if self.sms_text_mode:
            self.send_urc(f"+CBM: 1,{message_id},0,1,1", text)
        else:
            self.send_urc("+CBM: 88", pdu_hex)

    def notify_ring(self) -> None:
        with self.modem.lock:
            call = self.modem.call
        if not call or call.direction != 1 or call.state not in (4, 5):
            return
        ring = "+CRING: VOICE" if self.crc else "RING"
        lines = [ring]
        if self.clip:
            lines.append(f'+CLIP: "{call.number}",145,,,,0')
        self.send_urc(*lines)


# -------------------------------- debug shell --------------------------------


class DebugConsole(cmd.Cmd):
    intro = (
        "MeVeSo ModemSim debug console. Type 'help' for commands.\n"
        "AT traffic is served separately over the configured transport."
    )
    prompt = "modemsim> "

    def __init__(self, modem: ModemState):
        super().__init__()
        self.modem = modem

    def emptyline(self) -> bool:
        return False

    def do_call(self, arg: str) -> None:
        """call [NUMBER]  -- inject an incoming voice call (default +14244879494)."""
        number = arg.strip() or self.modem.cfg["incoming_number"]
        if self.modem.inject_call(number):
            print(f"Incoming call injected from {number}")
        else:
            print("A call is already active/ringing; use 'hangup' first.")

    do_incoming_call = do_call

    def do_sms(self, arg: str) -> None:
        """sms [NUMBER] [TEXT]  -- inject SMS. No args uses the requested canned test SMS."""
        if not arg.strip():
            number = self.modem.cfg["incoming_number"]
            text = self.modem.cfg["incoming_sms"]
        else:
            try:
                parts = shlex.split(arg)
            except ValueError as e:
                print(f"parse error: {e}")
                return
            if not parts:
                return
            if parts[0].startswith("+") or parts[0].isdigit():
                number = parts[0]
                text = " ".join(parts[1:]) or self.modem.cfg["incoming_sms"]
            else:
                number = self.modem.cfg["incoming_number"]
                text = " ".join(parts)
        try:
            sms = self.modem.inject_sms(number, text)
        except RuntimeError as error:
            print(error)
            return
        print(f"SMS #{sms.index} injected from {number}: {text}")

    def do_smslist(self, arg: str) -> None:
        """smslist  -- list messages currently stored in the simulated USIM/ME."""
        with self.modem.lock:
            messages = list(sorted(self.modem.sms.values(), key=lambda item: item.index))
        if not messages:
            print("SMS storage is empty.")
        for sms in messages:
            direction = f"to {sms.recipient}" if sms.outgoing else f"from {sms.sender}"
            print(f"#{sms.index} {direction} unread={sms.unread} {sms.timestamp.isoformat()} {sms.text!r}")

    def do_smsdelete(self, arg: str) -> None:
        """smsdelete INDEX|all  -- remove stored SMS messages."""
        with self.modem.lock:
            if arg.strip().lower() == "all":
                count = len(self.modem.sms)
                self.modem.sms.clear()
                print(f"Deleted {count} messages.")
                return
            try:
                index = int(arg.strip())
            except ValueError:
                print("Usage: smsdelete INDEX|all")
                return
            if self.modem.sms.pop(index, None) is None:
                print(f"No SMS #{index}.")
            else:
                print(f"Deleted SMS #{index}.")

    def do_cmas(self, arg: str) -> None:
        """cmas [MESSAGE_ID] [TEXT]  -- inject cell broadcast. Default: 4370 ':3'."""
        mid = self.modem.cfg["cmas_message_id"]
        text = self.modem.cfg["cmas_text"]
        if arg.strip():
            try:
                parts = shlex.split(arg)
            except ValueError as e:
                print(f"parse error: {e}")
                return
            if parts and re.fullmatch(r"0x[0-9A-Fa-f]+|\d+", parts[0]):
                mid = int(parts.pop(0), 0)
            if parts:
                text = " ".join(parts)
        self.modem.inject_cmas(mid, text)
        print(f"CMAS/CBS injected: MID={mid} (0x{mid:04X}) text={text!r}")

    do_cb = do_cmas

    def do_hangup(self, arg: str) -> None:
        """hangup  -- end the current simulated call."""
        print("Call ended." if self.modem.hangup_call() else "No call is active.")

    def do_answer(self, arg: str) -> None:
        """answer  -- force-answer the current incoming call."""
        print("Call answered." if self.modem.answer_call() else "No incoming call is ringing.")

    def do_outgoing(self, arg: str) -> None:
        """outgoing [NUMBER]  -- originate a simulated voice call."""
        number = arg.strip() or self.modem.cfg["incoming_number"]
        print(f"Calling {number}." if self.modem.start_outgoing_call(number) else "A call is already active.")

    def do_tech(self, arg: str) -> None:
        """tech [GSM|EDGE|UMTS|HSPA|LTE|NR|NR_NSA]  -- show or change radio technology."""
        if not arg.strip():
            print(f"{self.modem.rat} (3GPP +COPS AcT {self.modem.act}, {self.modem.rat_info['family']})")
            return
        try:
            self.modem.set_rat(arg)
        except ValueError as error:
            print(error)
            return
        print(f"RAT changed to {self.modem.rat} (AcT {self.modem.act}).")

    do_rat = do_tech

    def do_register(self, arg: str) -> None:
        """register [home|roaming|off|searching|denied|unknown]  -- set network registration."""
        names = {"off": 0, "home": 1, "searching": 2, "denied": 3, "unknown": 4, "roaming": 5}
        value = arg.strip().lower()
        if not value:
            reverse = {state: name for name, state in names.items()}
            print(f"{self.modem.reg_stat} ({reverse.get(self.modem.reg_stat, 'unknown')})")
            return
        if value not in names and not value.isdigit():
            print("Use home, roaming, off, searching, denied, unknown, or 0..5.")
            return
        try:
            self.modem.set_registration_state(names.get(value, int(value) if value.isdigit() else -1))
        except ValueError as error:
            print(error)
            return
        if self.modem.registered:
            self.modem.attached = True
        print(f"Registration state is now {self.modem.reg_stat}.")

    def do_operator(self, arg: str) -> None:
        """operator [LONG_NAME [SHORT_NAME [PLMN]]]  -- show or change the serving operator."""
        if not arg.strip():
            print(f'{self.modem.cfg["carrier_long"]} / {self.modem.cfg["carrier_short"]} / {self.modem.cfg["plmn"]}')
            return
        try:
            parts = shlex.split(arg)
        except ValueError as error:
            print(f"parse error: {error}")
            return
        long_name = parts[0]
        short_name = parts[1] if len(parts) > 1 else long_name[:8].upper()
        plmn = parts[2] if len(parts) > 2 else str(self.modem.cfg["plmn"])
        if not re.fullmatch(r"\d{5,6}", plmn):
            print("PLMN must be 5 or 6 decimal digits.")
            return
        self.modem.cfg.update(carrier_long=long_name, carrier_short=short_name, plmn=plmn,
                              mcc=plmn[:3], mnc=plmn[3:])
        self.modem.notify_registration()
        print(f"Serving operator changed to {long_name} ({plmn}).")

    def do_cell(self, arg: str) -> None:
        """cell [CELL_ID [LAC [TAC [PCI [BAND [CHANNEL]]]]]]  -- show or change cell identity."""
        if not arg.strip():
            cfg = self.modem.cfg
            print(f'cell={cfg["cell_id"]} lac={cfg["lac"]} tac={cfg["tac"]} pci={cfg.get("physical_cell_id")} band={cfg.get("band")} channel={cfg.get("channel")}')
            return
        parts = shlex.split(arg)
        keys = ("cell_id", "lac", "tac", "physical_cell_id", "band", "channel")
        for key, value in zip(keys, parts):
            self.modem.cfg[key] = int(value, 0) if key in ("physical_cell_id", "band", "channel") else value.upper()
        self.modem.notify_registration()
        print("Cell identity updated.")

    def do_signal(self, arg: str) -> None:
        """signal [0-31|99]  -- get or set +CSQ RSSI."""
        if not arg.strip():
            print(f"RSSI={self.modem.cfg['rssi']} BER={self.modem.cfg['ber']}")
            return
        try:
            value = int(arg.strip())
        except ValueError:
            print("RSSI must be an integer 0..31 or 99.")
            return
        if value not in range(0, 32) and value != 99:
            print("RSSI must be 0..31 or 99.")
            return
        self.modem.cfg["rssi"] = value
        for session in self.modem.sessions_snapshot():
            if session.cmer[3]:
                level = max(0, min(5, int(value * 5 / 31))) if value != 99 else 0
                session.send_urc(f"+CIEV: 2,{level}")
        print(f"RSSI set to {value}")

    def do_metrics(self, arg: str) -> None:
        """metrics [KEY=VALUE ...]  -- show/set ber, rscp, ecno, rsrq, rsrp, and sinr_db."""
        keys = ("rssi", "ber", "rscp", "ecno", "rsrq", "rsrp", "sinr_db")
        if not arg.strip():
            print(" ".join(f"{key}={self.modem.cfg.get(key)}" for key in keys))
            return
        for assignment in shlex.split(arg):
            if "=" not in assignment:
                print(f"Expected KEY=VALUE, got {assignment!r}")
                return
            key, value = assignment.split("=", 1)
            if key not in keys:
                print(f"Unknown metric {key!r}; choose {', '.join(keys)}")
                return
            self.modem.cfg[key] = float(value) if key == "sinr_db" else int(value, 0)
        print("Radio metrics updated.")

    def do_battery(self, arg: str) -> None:
        """battery [PERCENT [MILLIVOLTS [charging|discharging]]]  -- show/set battery state."""
        cfg = self.modem.cfg
        if not arg.strip():
            print(f'{cfg.get("battery_percent")}% {cfg.get("battery_mv")}mV charging={cfg.get("battery_charging")} temp={cfg.get("temperature_c")}C')
            return
        parts = shlex.split(arg)
        cfg["battery_percent"] = max(0, min(100, int(parts[0])))
        if len(parts) > 1:
            cfg["battery_mv"] = int(parts[1])
        if len(parts) > 2:
            cfg["battery_charging"] = parts[2].lower() in ("charging", "on", "true", "1")
        self.modem.broadcast_urc(f'+CBC: {int(cfg["battery_charging"])},{cfg["battery_percent"]},{cfg["battery_mv"]}')
        print("Battery state updated.")

    def do_sim(self, arg: str) -> None:
        """sim [ready|pin|puk|absent]  -- show or change USIM state."""
        if not arg.strip():
            print(f"{self.modem.sim_state}; PIN tries={self.modem.pin_attempts}, PUK tries={self.modem.puk_attempts}")
            return
        states = {"ready": "READY", "pin": "SIM PIN", "puk": "SIM PUK", "absent": "NOT INSERTED"}
        wanted = states.get(arg.strip().lower())
        if not wanted:
            print("Use ready, pin, puk, or absent.")
            return
        self.modem.sim_state = wanted
        self.modem.broadcast_urc(f"+CPIN: {wanted}")
        self.modem.notify_registration()
        print(f"USIM state is now {wanted}.")

    def do_attach(self, arg: str) -> None:
        """attach [on|off]  -- show or change packet attachment."""
        if not arg.strip():
            print("on" if self.modem.attached else "off")
            return
        enabled = arg.strip().lower() in ("on", "1", "true", "yes")
        self.modem.attached = enabled
        if not enabled:
            for context in self.modem.pdp_contexts.values():
                context.active = False
        self.modem.broadcast_urc(f"+CGEV: {'ME ATTACH' if enabled else 'ME DETACH'}")
        print(f"Packet attachment {'enabled' if enabled else 'disabled'}.")

    def do_pdp(self, arg: str) -> None:
        """pdp [up|down CID]  -- list or toggle fake packet-data contexts."""
        parts = shlex.split(arg)
        if not parts:
            for ctx in sorted(self.modem.pdp_contexts.values(), key=lambda item: item.cid):
                print(f"cid={ctx.cid} active={ctx.active} type={ctx.pdp_type} apn={ctx.apn!r} address={ctx.addresses()}")
            return
        if len(parts) != 2 or parts[0].lower() not in ("up", "down"):
            print("Usage: pdp [up|down CID]")
            return
        cid = int(parts[1])
        context = self.modem.pdp_contexts.get(cid)
        if not context:
            print(f"No PDP context {cid}.")
            return
        context.active = parts[0].lower() == "up"
        self.modem.broadcast_urc(f"+CGEV: ME PDN {'ACT' if context.active else 'DEACT'} {cid}")
        print(f"PDP context {cid} is {'active' if context.active else 'inactive'}.")

    def do_ussd(self, arg: str) -> None:
        """ussd [TEXT]  -- inject a network-originated USSD notification."""
        self.modem.inject_ussd(arg or "Grizzco service notification")

    def do_urc(self, arg: str) -> None:
        """urc TEXT  -- send an arbitrary unsolicited result code to every AT client."""
        if not arg:
            print("Usage: urc TEXT")
            return
        self.modem.broadcast_urc(arg)
        print("URC sent.")

    def do_config(self, arg: str) -> None:
        """config [get KEY|set KEY JSON_VALUE|save]  -- inspect/edit/save configuration."""
        parts = arg.split(maxsplit=2)
        if not parts:
            print(json.dumps(self.modem.cfg, indent=2, ensure_ascii=False))
            return
        action = parts[0].lower()
        if action == "save":
            try:
                self.modem.save()
                print(f"Saved {self.modem.config_path}.")
            except OSError as error:
                print(f"save failed: {error}")
            return
        if len(parts) < 2:
            print("Usage: config get KEY | config set KEY JSON_VALUE | config save")
            return
        key = parts[1]
        if action == "get":
            print(json.dumps(self.modem.cfg.get(key), indent=2, ensure_ascii=False))
        elif action == "set" and len(parts) == 3:
            try:
                value = json.loads(parts[2])
            except json.JSONDecodeError:
                value = parts[2]
            if key == "rat":
                self.modem.set_rat(str(value))
            else:
                self.modem.cfg[key] = value
            print(f"{key} = {value!r}")
        else:
            print("Usage: config get KEY | config set KEY JSON_VALUE | config save")

    def do_verbose(self, arg: str) -> None:
        """verbose [on|off]  -- enable/disable the complete AT transcript."""
        if arg.strip():
            self.modem.verbose = arg.strip().lower() in ("on", "1", "true", "yes")
        print(f"Verbose transcript {'enabled' if self.modem.verbose else 'disabled'}.")

    def do_status(self, arg: str) -> None:
        """status  -- show the current simulated baseband state."""
        with self.modem.lock:
            call = self.modem.call
            clients = len(self.modem.sessions)
            sms_count = len(self.modem.sms)
        print(f"AT clients:      {clients}")
        print(f"Radio CFUN:      {self.modem.cfun}")
        print(f"USIM:            {self.modem.sim_state}")
        print(f"Registered:      stat={self.modem.reg_stat} roaming={self.modem.roaming}")
        print(f"Packet attached: {self.modem.attached} (data plane is a local sink)")
        print(f"Operator:        {self.modem.cfg['carrier_long']} / {self.modem.cfg['plmn']}")
        print(f"Radio:           {self.modem.rat} / AcT {self.modem.act} / band {self.modem.cfg.get('band')} / channel {self.modem.cfg.get('channel')}")
        print(f"Cell:            LAC {self.modem.cfg['lac']} TAC {self.modem.cfg['tac']} CI {self.modem.cfg['cell_id']}")
        print(f"Signal:          +CSQ {self.modem.cfg['rssi']},{self.modem.cfg['ber']}")
        print(f"Stored SMS:      {sms_count}")
        print(f"PDP contexts:    {', '.join(f'{ctx.cid}:{"up" if ctx.active else "down"}' for ctx in self.modem.pdp_contexts.values())}")
        print(f"Call:            {call if call else 'none'}")

    def do_clients(self, arg: str) -> None:
        """clients  -- list attached AT clients."""
        with self.modem.lock:
            sessions = list(self.modem.sessions)
        if not sessions:
            print("No AT clients connected.")
            return
        for i, s in enumerate(sessions, 1):
            print(f"{i}: {s.label} thread={s.name} echo={s.echo} data={s.data_mode} CMGF={s.sms_text_mode} CNMI={','.join(map(str, s.cnmi))}")

    def do_disconnect(self, arg: str) -> None:
        """disconnect INDEX|all  -- close one or every AT client."""
        sessions = self.modem.sessions_snapshot()
        if arg.strip().lower() == "all":
            for session in sessions:
                session.close()
            print(f"Disconnected {len(sessions)} clients.")
            return
        try:
            index = int(arg.strip()) - 1
            session = sessions[index]
        except (ValueError, IndexError):
            print("Usage: disconnect INDEX|all (see 'clients')")
            return
        session.close()
        print(f"Disconnected {session.label}.")

    def do_reset(self, arg: str) -> None:
        """reset  -- warm-reset calls, messages, contexts, radio and client settings."""
        self.modem.hangup_call()
        with self.modem.lock:
            self.modem.sms.clear()
            self.modem.next_sms_index = 1
            self.modem.cfun = 1
            self.modem.registration_state = 5 if self.modem.cfg.get("roaming") else (1 if self.modem.cfg.get("registered", True) else 0)
            self.modem.attached = bool(self.modem.cfg.get("packet_attached", True))
            for context in self.modem.pdp_contexts.values():
                context.active = False
        self.modem.notify_registration()
        self.modem.broadcast_urc("+CGEV: ME RESET")
        print("Warm reset complete.")

    def do_quit(self, arg: str) -> bool:
        """quit  -- stop ModemSim."""
        self.modem.stop_event.set()
        return True

    do_exit = do_quit

    def do_EOF(self, arg: str) -> bool:
        print()
        return True


# ---------------------------------- server ------------------------------------


class PTYConnection:
    """Small socket-like wrapper around a PTY master file descriptor."""

    def __init__(self, fd: int):
        self.fd = fd
        self.closed = threading.Event()

    def recv(self, size: int) -> bytes:
        # A timeout keeps shutdown responsive even on platforms where closing a
        # descriptor from another thread does not wake a blocked read.
        while not self.closed.is_set():
            readable, _, _ = select.select([self.fd], [], [], 0.5)
            if readable:
                return os.read(self.fd, size)
        return b""

    def sendall(self, data: bytes) -> None:
        view = memoryview(data)
        while view and not self.closed.is_set():
            written = os.write(self.fd, view)
            view = view[written:]

    def shutdown(self, how: int) -> None:
        self.close()

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        try:
            os.close(self.fd)
        except OSError:
            pass


def start_console_and_signals(modem: ModemState, no_console: bool) -> None:
    if not no_console:
        console = DebugConsole(modem)
        threading.Thread(target=console.cmdloop, name="debug-console", daemon=True).start()

    def stop_handler(signum, frame):  # type: ignore[no-untyped-def]
        modem.stop_event.set()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)


def print_profile(modem: ModemState) -> None:
    print(
        f"Profile: {modem.cfg['carrier_long']} {modem.cfg['plmn']}, "
        f"{modem.rat}, {modem.cfg['msisdn']}, +CSQ {modem.cfg['rssi']}"
    )
    print("Packet service control succeeds; all user-plane bytes remain in a local sink.")
    print(f"Configuration: {modem.config_path}")


def serve(socket_path: str, modem: ModemState, no_console: bool = False) -> int:
    if os.path.exists(socket_path):
        if not stat_is_socket(socket_path):
            print(f"Refusing to remove non-socket path: {socket_path}", file=sys.stderr)
            return 2
        os.unlink(socket_path)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    os.chmod(socket_path, 0o660)
    server.listen(4)
    server.settimeout(0.5)

    print(f"MeVeSo ModemSim listening on unix:{socket_path}")
    print_profile(modem)
    start_console_and_signals(modem, no_console)

    try:
        while not modem.stop_event.is_set():
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                if modem.stop_event.is_set():
                    break
                raise
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            ATSession(modem, conn).start()
    finally:
        modem.shutdown()
        try:
            server.close()
        finally:
            try:
                if os.path.exists(socket_path):
                    os.unlink(socket_path)
            except OSError:
                pass
    return 0


def serve_pty(link_path: str, modem: ModemState, no_console: bool = False) -> int:
    master_fd, slave_fd = pty.openpty()
    slave_path = os.ttyname(slave_fd)
    tty.setraw(slave_fd)
    link_path = os.path.abspath(link_path)
    made_link = False

    try:
        if link_path != slave_path:
            if os.path.lexists(link_path):
                raise OSError(f"refusing to replace existing PTY path: {link_path}")
            os.symlink(slave_path, link_path)
            made_link = True

        print(f"MeVeSo ModemSim listening on pty:{link_path} -> {slave_path}")
        print_profile(modem)
        start_console_and_signals(modem, no_console)

        connection = PTYConnection(master_fd)
        session = ATSession(modem, connection, peer="pty")
        session.start()
        while not modem.stop_event.wait(0.5):
            pass
    except OSError as error:
        print(f"Could not create PTY {link_path}: {error}", file=sys.stderr)
        return 2
    finally:
        modem.shutdown()
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            os.close(slave_fd)
        except OSError:
            pass
        if made_link:
            try:
                if os.path.islink(link_path) and os.readlink(link_path) == slave_path:
                    os.unlink(link_path)
            except OSError:
                pass
    return 0


def stat_is_socket(path: str) -> bool:
    import stat

    try:
        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="MeVeSo stateful 3GPP AT modem and USIM emulator")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help=f"JSON configuration (default: {DEFAULT_CONFIG})")
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--socket", help="Unix socket path (default: socket_path in config.json)")
    transport.add_argument(
        "--pty", nargs="?", const="/dev/ttyMeVeSo", metavar="PATH",
        help="use a pseudo-terminal, optionally linked at PATH (default: /dev/ttyMeVeSo)",
    )
    parser.add_argument("--no-console", action="store_true", help="disable the local stdin debug console")
    transcript = parser.add_mutually_exclusive_group()
    transcript.add_argument("--verbose", action="store_true", help="force complete AT traffic logging")
    transcript.add_argument("--quiet", action="store_true", help="disable AT traffic logging")
    args = parser.parse_args()

    try:
        cfg = load_config(args.config.resolve())
    except ValueError as error:
        parser.error(str(error))
    verbose = bool(cfg.get("debug_verbose", True))
    if args.verbose:
        verbose = True
    if args.quiet:
        verbose = False
    socket_path = args.socket or str(cfg.get("socket_path", "/tmp/meveso-modem.sock"))
    modem = ModemState(cfg=cfg, config_path=args.config.resolve(), verbose=verbose)
    if args.pty:
        return serve_pty(args.pty, modem, no_console=args.no_console)
    return serve(socket_path, modem, no_console=args.no_console)


if __name__ == "__main__":
    raise SystemExit(main())
