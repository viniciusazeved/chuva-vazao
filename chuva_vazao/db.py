"""
Acesso ao SQLite extraido do banco HidroFlu v2.0.

Todas as consultas retornam DataFrame (pandas). O SQLite fica em `data/chuvavazao.db`
na raiz do projeto; o caminho pode ser sobrescrito pelo argumento `db_path` em cada
funcao ou pela variavel de ambiente `CHUVAVAZAO_DB`.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "chuvavazao.db"


def _resolve_db_path(db_path: str | os.PathLike | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    env = os.environ.get("CHUVAVAZAO_DB")
    if env:
        return Path(env)
    return DEFAULT_DB_PATH


@contextmanager
def connect(db_path: str | os.PathLike | None = None) -> Iterator[sqlite3.Connection]:
    path = _resolve_db_path(db_path)
    if not path.exists():
        raise FileNotFoundError(
            f"SQLite nao encontrado em {path}. Rode `uv run python scripts/extract_mdb.py`."
        )
    conn = sqlite3.connect(path)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IDFCoef:
    """Coeficientes IDF classicos (i = K * TR^a / (t + c)^b)."""
    descricao: str
    estado: str
    K: float
    a: float
    b: float
    c: float
    fonte: str = "HidroFlu:postos_idf_coeficientes"


@dataclass(frozen=True)
class PfafstetterCoef:
    """
    Coeficientes IDF estilo Pfafstetter + betas de desagregacao regionais.

    A formulacao IDF exata para estes parametros segue a convencao do HidroFlu
    (Pfafstetter 1957 + calibracao regional). Na ausencia de K explicito,
    os a/b/c aqui nao sao diretamente intercambiaveis com os IDFCoef.
    """
    descricao: str
    estado: str
    a: float
    b: float
    c: float
    beta5min: float
    beta15min: float
    beta30min: float
    beta1h_6dias: float
    fonte: str = "HidroFlu:postos_pfafstetter_coeficientes"


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def list_estados(db_path: str | os.PathLike | None = None) -> list[str]:
    """Retorna todas as siglas de UF presentes no banco de estados."""
    with connect(db_path) as conn:
        rows = conn.execute("SELECT estado FROM estados_brasil ORDER BY estado").fetchall()
    return [r[0] for r in rows]


def list_estados_com_postos(db_path: str | os.PathLike | None = None) -> list[str]:
    """UFs que tem pelo menos um posto (IDF classico ou Pfafstetter)."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT estado FROM ("
            "  SELECT estado FROM postos_idf_coeficientes"
            "  UNION"
            "  SELECT estado FROM postos_pfafstetter_coeficientes"
            ") ORDER BY estado"
        ).fetchall()
    return [r[0] for r in rows]


def list_postos(
    estado: str | None = None,
    fonte: str | None = None,
    db_path: str | os.PathLike | None = None,
) -> pd.DataFrame:
    """
    Catalogo consolidado de postos (uniao das duas tabelas).

    Parameters
    ----------
    estado : str, opcional
        Filtra pela sigla da UF (ex: "RJ"). None = todos.
    fonte : str, opcional
        "idf" para apenas IDF classico, "pfafstetter" para apenas Pfafstetter,
        None para os dois.

    Returns
    -------
    pd.DataFrame
        Colunas: descricao, estado, fonte.
    """
    queries: list[str] = []
    if fonte in (None, "idf"):
        queries.append(
            "SELECT descricao, estado, 'idf' AS fonte FROM postos_idf_coeficientes"
        )
    if fonte in (None, "pfafstetter"):
        queries.append(
            "SELECT descricao, estado, 'pfafstetter' AS fonte "
            "FROM postos_pfafstetter_coeficientes"
        )
    if not queries:
        raise ValueError(f"fonte invalida: {fonte!r} (use 'idf', 'pfafstetter' ou None)")

    sql = " UNION ALL ".join(queries)
    params: tuple = ()
    if estado:
        sql = f"SELECT * FROM ({sql}) WHERE estado = ?"
        params = (estado,)
    sql += " ORDER BY estado, descricao"

    with connect(db_path) as conn:
        return pd.read_sql(sql, conn, params=params)


def get_idf_coef(
    descricao: str,
    estado: str | None = None,
    db_path: str | os.PathLike | None = None,
) -> IDFCoef | None:
    """Busca coeficientes IDF classicos (K/a/b/c) pelo nome do posto."""
    sql = "SELECT descricao, estado, k, a, b, c FROM postos_idf_coeficientes WHERE descricao = ?"
    params: list = [descricao]
    if estado:
        sql += " AND estado = ?"
        params.append(estado)

    with connect(db_path) as conn:
        row = conn.execute(sql, params).fetchone()

    if row is None:
        return None
    return IDFCoef(
        descricao=row[0],
        estado=row[1],
        K=float(row[2]),
        a=float(row[3]),
        b=float(row[4]),
        c=float(row[5]),
    )


def get_pfafstetter_coef(
    descricao: str,
    estado: str | None = None,
    db_path: str | os.PathLike | None = None,
) -> PfafstetterCoef | None:
    """Busca coeficientes Pfafstetter + betas regionais pelo nome do posto."""
    sql = (
        "SELECT descricao, estado, a, b, c, beta5min, beta15min, beta30min, beta1h_6dias "
        "FROM postos_pfafstetter_coeficientes WHERE descricao = ?"
    )
    params: list = [descricao]
    if estado:
        sql += " AND estado = ?"
        params.append(estado)

    with connect(db_path) as conn:
        row = conn.execute(sql, params).fetchone()

    if row is None:
        return None
    return PfafstetterCoef(
        descricao=row[0],
        estado=row[1],
        a=float(row[2]),
        b=float(row[3]),
        c=float(row[4]),
        beta5min=float(row[5]),
        beta15min=float(row[6]),
        beta30min=float(row[7]),
        beta1h_6dias=float(row[8]),
    )


def get_betas_regionais(
    descricao: str,
    estado: str | None = None,
    db_path: str | os.PathLike | None = None,
) -> dict[str, float] | None:
    """
    Retorna dict de betas regionais do posto.

    Chaves: "5min", "15min", "30min", "1h_6dias".
    """
    coef = get_pfafstetter_coef(descricao, estado, db_path)
    if coef is None:
        return None
    return {
        "5min": coef.beta5min,
        "15min": coef.beta15min,
        "30min": coef.beta30min,
        "1h_6dias": coef.beta1h_6dias,
    }


def contagem_por_estado(
    fonte: str = "pfafstetter",
    db_path: str | os.PathLike | None = None,
) -> pd.DataFrame:
    """Contagem de postos por UF para visualizacao."""
    tabela = (
        "postos_pfafstetter_coeficientes" if fonte == "pfafstetter"
        else "postos_idf_coeficientes"
    )
    sql = f"SELECT estado, COUNT(*) AS n FROM {tabela} GROUP BY estado ORDER BY n DESC"
    with connect(db_path) as conn:
        return pd.read_sql(sql, conn)
