"""
Extracao one-shot: MDB do HidroFlu v2.0 -> SQLite.

Abre o banco Access via pyodbc (driver `Microsoft Access Driver (*.mdb, *.accdb)`),
le todas as tabelas de usuario, normaliza nomes para snake_case ASCII e grava
em `data/chuvavazao.db`. Em paralelo gera `schema_inspection.md` na raiz do projeto
com esquema, contagens e amostra dos 3 primeiros registros de cada tabela.

Uso:
    uv run python scripts/extract_mdb.py [--mdb PATH] [--db PATH] [--force]
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd
import pyodbc


logger = logging.getLogger("extract_mdb")

ACCESS_DRIVER = "{Microsoft Access Driver (*.mdb, *.accdb)}"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MDB = PROJECT_ROOT / "data" / "mdb_origem.mdb"
DEFAULT_DB = PROJECT_ROOT / "data" / "chuvavazao.db"
DEFAULT_REPORT = PROJECT_ROOT / "schema_inspection.md"

HEURISTIC_KEYS = ("posto", "idf", "beta", "coef", "estado", "pfaf")


def normalize(name: str) -> str:
    """snake_case ASCII: remove acentos, baixa caixa, troca nao-alfanumerico por `_`."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    snake = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_name).strip("_").lower()
    return snake or "tabela_sem_nome"


def connect_mdb(mdb_path: Path) -> pyodbc.Connection:
    conn_str = f"DRIVER={ACCESS_DRIVER};DBQ={mdb_path};"
    conn = pyodbc.connect(conn_str)
    conn.setdecoding(pyodbc.SQL_CHAR, encoding="cp1252")
    conn.setdecoding(pyodbc.SQL_WCHAR, encoding="cp1252")
    conn.setencoding(encoding="cp1252")
    return conn


def list_user_tables(conn: pyodbc.Connection) -> list[str]:
    cur = conn.cursor()
    tables = [
        row.table_name
        for row in cur.tables(tableType="TABLE")
        if not row.table_name.startswith("MSys")
    ]
    cur.close()
    return sorted(tables)


def read_table(conn: pyodbc.Connection, table: str) -> pd.DataFrame:
    query = f"SELECT * FROM [{table}]"
    return pd.read_sql(query, conn)


def extract(mdb_path: Path, db_path: Path, force: bool) -> list[dict[str, Any]]:
    if db_path.exists():
        if not force:
            logger.warning(
                "SQLite ja existe em %s; use --force para sobrescrever. Sobrescrevendo tabelas individualmente.",
                db_path,
            )
        else:
            logger.info("--force: removendo %s", db_path)
            db_path.unlink()

    db_path.parent.mkdir(parents=True, exist_ok=True)

    meta: list[dict[str, Any]] = []

    with connect_mdb(mdb_path) as mdb_conn, sqlite3.connect(db_path) as sqlite_conn:
        tables = list_user_tables(mdb_conn)
        logger.info("Tabelas encontradas no MDB: %d", len(tables))
        for table in tables:
            logger.info("  - %s", table)

        for original_name in tables:
            df = read_table(mdb_conn, original_name)
            normalized_name = normalize(original_name)
            df.columns = [normalize(c) for c in df.columns]
            df.to_sql(normalized_name, sqlite_conn, if_exists="replace", index=False)

            meta.append({
                "original": original_name,
                "normalized": normalized_name,
                "rows": len(df),
                "columns": list(zip(df.columns, [str(dt) for dt in df.dtypes], strict=True)),
                "head": df.head(3).to_dict(orient="records"),
            })
            logger.info("    -> %s (%d linhas, %d colunas)", normalized_name, len(df), len(df.columns))

    return meta


def _fmt_cell(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).replace("|", "\\|").replace("\n", " ")
    return s[:80]


def write_inspection_report(meta: list[dict[str, Any]], out_path: Path) -> None:
    lines: list[str] = [
        "# Inspecao do esquema extraido do MDB HidroFlu",
        "",
        f"Total de tabelas: **{len(meta)}**",
        "",
        "Gerado automaticamente por `scripts/extract_mdb.py`.",
        "",
    ]

    for entry in meta:
        lines.append(f"## `{entry['normalized']}`")
        lines.append("")
        lines.append(f"- Nome original no MDB: `{entry['original']}`")
        lines.append(f"- Linhas: **{entry['rows']}**")
        lines.append(f"- Colunas: **{len(entry['columns'])}**")
        lines.append("")
        lines.append("### Esquema")
        lines.append("")
        lines.append("| Coluna | Tipo (pandas) |")
        lines.append("|---|---|")
        for col, dtype in entry["columns"]:
            lines.append(f"| `{col}` | `{dtype}` |")
        lines.append("")

        if entry["rows"] > 0 and entry["columns"]:
            lines.append("### Amostra (3 primeiras linhas)")
            lines.append("")
            col_names = [c for c, _ in entry["columns"]]
            lines.append("| " + " | ".join(f"`{c}`" for c in col_names) + " |")
            lines.append("|" + "|".join(["---"] * len(col_names)) + "|")
            for row in entry["head"]:
                lines.append("| " + " | ".join(_fmt_cell(row.get(c)) for c in col_names) + " |")
            lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def print_heuristic_summary(meta: list[dict[str, Any]]) -> None:
    print("\n=== Resumo heuristico (palavras-chave de interesse) ===")
    for entry in meta:
        name = entry["normalized"]
        if any(k in name for k in HEURISTIC_KEYS):
            print(f"  [{name}] linhas={entry['rows']} colunas={len(entry['columns'])}")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Extrai MDB HidroFlu -> SQLite")
    parser.add_argument("--mdb", type=Path, default=DEFAULT_MDB, help="Caminho do MDB (default: data/mdb_origem.mdb)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Caminho do SQLite destino (default: data/chuvavazao.db)")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Caminho do relatorio markdown (default: schema_inspection.md)")
    parser.add_argument("--force", action="store_true", help="Apaga o SQLite existente antes de extrair")
    args = parser.parse_args()

    if not args.mdb.exists():
        logger.error("MDB nao encontrado: %s", args.mdb)
        return 1

    try:
        meta = extract(args.mdb, args.db, args.force)
    except pyodbc.InterfaceError as exc:
        logger.error(
            "Falha ao conectar no MDB: %s\n"
            "Driver ODBC nao encontrado ou bitness incompativel.\n"
            "Instale o 'Microsoft Access Database Engine 2016 Redistributable' (x64) "
            "compativel com a sua versao do Python.",
            exc,
        )
        return 2

    write_inspection_report(meta, args.report)
    logger.info("Relatorio gravado em %s", args.report)
    logger.info("SQLite gerado em %s (%d tabelas)", args.db, len(meta))

    print_heuristic_summary(meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
