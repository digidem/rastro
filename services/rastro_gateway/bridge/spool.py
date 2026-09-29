"""Spool GeoJSONSeq append-only + watermark contígua + rotação + poda limitada.

Zona de texto plano do gateway host (plano §B): decoded JSON em disco, dir
0700 (se criado), arquivos 0600, tamanho limitado. ``watermark.json`` =
``{"file", "offset"}``: TODO byte antes dessa posição já foi PUBACKed — e é a
MÍNIMA CONTÍGUA: PUBACKs chegam fora de ordem, então um span só empurra a
watermark quando for o próximo da FIFO; nunca avançar sobre buraco, senão o
registro pulado some com os bytes parecendo consumidos.

Crash entre PUBACK e gravação da watermark ⇒ replay ⇒ o dedupe do banco
(decisão D6, ``ON CONFLICT DO NOTHING``) absorve. Recuperação de corrupção
recomeça do arquivo mais antigo, offset 0 — a direção segura é sempre PARA
TRÁS. Poda: primeiro só o totalmente acked (arquivo anterior ao da watermark);
sob outage sustentado (T5), os mais antigos mesmo sem ack, com log alto.
A captura ao vivo NUNCA bloqueia: append é um ``os.write`` com O_APPEND.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

from rastro_gateway.bridge.geojson_in import from_geojson_feature
from rastro_gateway.common.records import Record

log = logging.getLogger(__name__)

Span = tuple[str, int, int]  # (arquivo, byte inicial, byte final) da linha

_FILE_RE = re.compile(r"^spool-(\d{6})\.geojsonl$")
_WATERMARK_NAME = "watermark.json"
_FIRST_FILE = "spool-000001.geojsonl"


class Spool:
    """Fila durável de records entre a captura serial e o PUBACK do broker."""

    def __init__(self, dir: Path, rotate_bytes: int, max_bytes: int) -> None:
        self._dir = Path(dir)
        self._rotate_bytes = rotate_bytes
        self._max_bytes = max_bytes
        self._lock = threading.Lock()  # protege estado + watermark + arquivos

        existed = self._dir.exists()
        self._dir.mkdir(parents=True, exist_ok=True)
        if not existed:
            os.chmod(self._dir, 0o700)

        # FIFO de spans em voo (publicados, aguardando PUBACK) + acks que
        # chegaram fora de ordem; dict = membership O(1) preservando ordem.
        self._inflight: dict[Span, None] = {}
        self._acked: dict[Span, None] = {}

        names = self._scan_names()
        self._current: str | None = names[-1] if names else None
        self._seq = int(_FILE_RE.match(self._current).group(1)) if names else 0
        self._current_size = (
            (self._dir / self._current).stat().st_size if names else 0
        )
        self._fd: int | None = None

        self._wm_file, self._wm_offset = self._recover_watermark(names)

    # --- captura -------------------------------------------------------------

    def append(self, record: Record) -> Span:
        """Grava o record como linha GeoJSONSeq e devolve o Span da linha."""
        line = json.dumps(record.to_geojson_feature(), ensure_ascii=False) + "\n"
        data = line.encode("utf-8")
        with self._lock:
            if self._current is None:
                self._open_next()
            elif self._current_size + len(data) > self._rotate_bytes:
                self._open_next()  # rotação: novo arquivo vira o corrente
            elif self._fd is None:
                self._reopen_current()
            start = self._current_size
            os.write(self._fd, data)  # type: ignore[arg-type]
            os.fdatasync(self._fd)  # queda de energia não perde o registro (gate F3 R2)
            self._current_size += len(data)
            if self._dir_total() > self._max_bytes:
                self._prune_locked()
            return (self._current, start, self._current_size)

    # --- watermark -----------------------------------------------------------

    def register(self, span: Span) -> None:
        """Entra na FIFO de voo — chamar ANTES do publish (PUBACK pode correr na frente)."""
        with self._lock:
            self._inflight.setdefault(span, None)

    def unregister(self, span: Span) -> None:
        """publish() falhou (rc != 0): sai da FIFO sem avançar nada."""
        with self._lock:
            self._inflight.pop(span, None)

    def advance(self, span: Span) -> None:
        """PUBACK do span: empurra a watermark até o mínimo CONTÍGUO ackado."""
        with self._lock:
            if span not in self._inflight:
                return  # desconhecido ou duplicado — não move a watermark
            self._acked[span] = None
            moved = False
            while self._inflight:
                first = next(iter(self._inflight))
                if first not in self._acked:
                    break  # buraco: para aqui (nunca avançar sobre gap)
                del self._inflight[first]
                del self._acked[first]
                # monotonicidade: um span re-registrado após replay não puxa a
                # watermark PARA TRÁS (seguro, mas gera replay infinito) (gate F3 NIT)
                if (first[0], first[2]) >= (self._wm_file, self._wm_offset):
                    self._wm_file = first[0]
                    self._wm_offset = first[2]
                    moved = True
            if moved:
                self._save_watermark()

    def inflight(self, span: Span) -> bool:
        """Span está publicado e aguardando PUBACK (evita duplicar no replay)."""
        with self._lock:
            return span in self._inflight

    def position(self) -> tuple[str, int]:
        """Posição da watermark: tudo antes dela está PUBACKed."""
        with self._lock:
            return self._wm_file, self._wm_offset

    # --- replay --------------------------------------------------------------

    def iter_from(self, file: str, offset: int):
        """Generator de ``((file, start, end), record | None)`` a partir da watermark.

        Linha ilegível/incompleta → ``None`` no lugar do record: o span segue
        na FIFO para a watermark poder atravessar (registro perdido só em
        disco corrompido; a alternativa seria travar o replay para sempre).
        """
        with self._lock:
            names = self._scan_names()
        if file not in names:
            # arquivo da watermark sumiu: recomeça do mais antigo (PARA TRÁS)
            file, offset = (names[0], 0) if names else (_FIRST_FILE, 0)
        for name in names:
            if name < file:
                continue
            yield from self._iter_file(name, offset if name == file else 0)

    def _iter_file(self, name: str, offset: int):
        path = self._dir / name
        try:
            f = open(path, "rb")
        except FileNotFoundError:
            return  # podado durante o replay — segue pro próximo
        with f:
            pos = offset
            f.seek(offset)
            for line in f:
                start = pos
                pos += len(line)
                span: Span = (name, start, pos)
                if not line.endswith(b"\n"):
                    # Linha final em escrita (append ao vivo) ou corrupção: NÃO
                    # atravessar — parar AQUI sem avançar a watermark; o registro
                    # fica para o próximo ciclo (e o caminho ao vivo já o publicou).
                    # Avançar sobre ela perderia o registro com os bytes "consumidos".
                    log.warning(
                        "AVISO: linha incompleta no spool (%s@%d) — replay para NESTE arquivo",
                        name,
                        start,
                    )
                    break
                try:
                    feature = json.loads(line)
                except ValueError:
                    feature = None
                record = (
                    from_geojson_feature(feature)
                    if isinstance(feature, dict)
                    else None
                )
                if record is None:
                    log.warning(
                        "AVISO: linha ilegível no spool (%s) — atravessando", name
                    )
                yield span, record

    # --- poda ----------------------------------------------------------------

    def prune(self) -> None:
        """Traz o diretório de volta ao teto (max_bytes), mais antigo primeiro."""
        with self._lock:
            self._prune_locked()

    def _prune_locked(self) -> None:
        if self._dir_total() <= self._max_bytes:
            return
        # Fase 1: só arquivos TOTALMENTE abaixo da watermark (na prática:
        # anteriores ao arquivo da watermark — nunca dados não ackados).
        for name, _size in self._scan_with_size():
            if self._dir_total() <= self._max_bytes:
                return
            if not name < self._wm_file:
                break  # ordenado: daqui pra frente é >= watermark
            if name == self._current:
                continue  # o arquivo aberto nunca é podado
            self._unlink(name)
            log.warning("AVISO: descartando spool antigo (%s)", name)
        # Fase 2: ainda estourando após podar todo acked — outage sustentado
        # (gatilho do T5): descarta os mais antigos MESMO sem ack, log alto.
        for name, _size in self._scan_with_size():
            if self._dir_total() <= self._max_bytes:
                break
            if name == self._current:
                continue
            self._unlink(name)
            log.warning(
                "AVISO: overflow do spool — descartando dados NÃO publicados (%s); "
                "outage sustentado?",
                name,
            )
        self._reanchor_watermark()

    # --- persistência da watermark --------------------------------------------

    def _recover_watermark(self, names: list[str]) -> tuple[str, int]:
        oldest = names[0] if names else _FIRST_FILE
        exists = (self._dir / _WATERMARK_NAME).exists()
        loaded = self._load_watermark()
        if loaded is not None:
            name, offset = loaded
            sizes = {n: (self._dir / n).stat().st_size for n in names}
            if name in sizes and 0 <= offset <= sizes[name]:
                return name, offset
        if exists:
            log.warning(
                "AVISO: watermark ilegível/inconsistente no spool — recomeçando do "
                "arquivo mais antigo (%s), offset 0; replay é absorvido pelo dedupe",
                oldest,
            )
        self._wm_file, self._wm_offset = oldest, 0
        self._save_watermark()
        return oldest, 0

    def _load_watermark(self) -> tuple[str, int] | None:
        try:
            raw = (self._dir / _WATERMARK_NAME).read_text(encoding="utf-8")
            obj = json.loads(raw)
        except (OSError, ValueError):
            return None
        if not isinstance(obj, dict):
            return None
        name = obj.get("file")
        offset = obj.get("offset")
        if (
            not isinstance(name, str)
            or isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
        ):
            return None
        return name, offset

    def _save_watermark(self) -> None:
        """Escrita atômica (tmp + os.replace), 0600. Chamar COM a lock."""
        tmp = self._dir / (_WATERMARK_NAME + ".tmp")
        data = json.dumps(
            {"file": self._wm_file, "offset": self._wm_offset}, ensure_ascii=False
        ).encode("utf-8")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self._dir / _WATERMARK_NAME)

    def _reanchor_watermark(self) -> None:
        """Watermark órfã (arquivo podado) → mais antigo existente, offset 0."""
        names = self._scan_names()
        if names and self._wm_file not in set(names):
            oldest = names[0]
            log.warning(
                "AVISO: watermark apontava para arquivo descartado (%s) — recomeçando "
                "do mais antigo (%s), offset 0",
                self._wm_file,
                oldest,
            )
            self._wm_file, self._wm_offset = oldest, 0
            self._save_watermark()

    # --- arquivos --------------------------------------------------------------

    def _open_next(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._seq += 1
        self._current = f"spool-{self._seq:06d}.geojsonl"
        self._current_size = 0
        self._fd = self._open_fd(self._current)

    def _reopen_current(self) -> None:
        """Reabre o arquivo corrente herdado do boot, sem criar nada."""
        assert self._current is not None
        try:
            fd = os.open(self._dir / self._current, os.O_WRONLY | os.O_APPEND)
        except FileNotFoundError:
            self._open_next()  # sumiu de fora: novo arquivo, direção segura
            return
        self._current_size = os.fstat(fd).st_size
        # cauda rasgada (último byte sem \n, ex. queda de energia): fecha a linha
        # com \n para o próximo append não se FUNDIR com ela (gate F3 NIT)
        if self._current_size > 0:
            with open(self._dir / self._current, "rb") as probe:
                probe.seek(self._current_size - 1)
                if probe.read(1) != b"\n":
                    os.write(fd, b"\n")
                    self._current_size += 1
        self._fd = fd

    def _open_fd(self, name: str) -> int:
        fd = os.open(
            self._dir / name, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
        )
        os.fchmod(fd, 0o600)  # à prova de umask
        return fd

    def _scan_names(self) -> list[str]:
        out = []
        for entry in self._dir.iterdir():
            if entry.is_file() and _FILE_RE.match(entry.name):
                out.append(entry.name)
        return sorted(out)

    def _scan_with_size(self) -> list[tuple[str, int]]:
        out = []
        for name in self._scan_names():
            try:
                out.append((name, (self._dir / name).stat().st_size))
            except FileNotFoundError:
                continue
        return out

    def _dir_total(self) -> int:
        return sum(size for _, size in self._scan_with_size())

    def _unlink(self, name: str) -> None:
        try:
            (self._dir / name).unlink()
        except FileNotFoundError:
            pass
