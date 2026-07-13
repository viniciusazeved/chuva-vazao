"""
Identidade visual (marca) do app e dos relatorios.

O chuva_vazao nasceu como app do LAPLA (FECFAU/Unicamp) e passou a ser usado
tambem pela Azevedo Consultoria Ambiental e Energetica. Em vez de fixar uma
marca no codigo, a identidade e um dict sobrescrivivel — mesmo padrao do
projeto Hidroenergetico (`report_base.IDENTIDADE_AZEVEDO`).

Marca ativa:
    - default: Azevedo.
    - override: variavel de ambiente ``CHUVA_VAZAO_MARCA`` = "lapla" | "azevedo"
      (ou st.secrets["marca"] no Streamlit Cloud, se presente).

Cada dict cobre TODOS os pontos de toque (sidebar do app + capa/rodape do PDF),
para que trocar a marca nao deixe nenhum texto/logo orfao.
"""
from __future__ import annotations

import os
from pathlib import Path

ASSETS_DIR = Path(__file__).parent / "assets"


IDENTIDADE_AZEVEDO: dict[str, str] = {
    "marca": "azevedo",
    # Cabecalho tipografico (usado se o logo nao existir)
    "empresa": "AZEVEDO",
    "descricao": "Consultoria Ambiental e Energética",
    # Logo (arquivo em assets/)
    "logo_filename": "logo_azevedo.png",
    # Capa do PDF — bloco institucional no rodape da capa
    "rodape_capa_titulo": "Azevedo — Consultoria Ambiental e Energética",
    "rodape_capa_sub": "",  # sem submarca academica
    # Rodape das paginas internas do PDF (linha central)
    "rodape_paginas": "Chuva - Vazão  ·  Azevedo Consultoria Ambiental e Energética",
    # Creditos na sidebar do app (markdown do st.caption)
    "creditos_sidebar": (
        "**Azevedo** — Consultoria Ambiental e Energética\n\n"
        "[Repositório](https://github.com/viniciusazeved/chuva-vazao) · "
        "[IDF-generator](https://idf-generator.streamlit.app) (app irmã)"
    ),
}

IDENTIDADE_LAPLA: dict[str, str] = {
    "marca": "lapla",
    "empresa": "LAPLA",
    "descricao": "Laboratório de Planejamento Ambiental",
    "logo_filename": "logo_lapla.png",
    "rodape_capa_titulo": "LAPLA — Laboratório de Planejamento Ambiental",
    "rodape_capa_sub": "FECFAU / Unicamp",
    "rodape_paginas": "Gerado por Chuva - Vazão  ·  LAPLA — FECFAU/Unicamp",
    "creditos_sidebar": (
        "**LAPLA** — Laboratório de Planejamento Ambiental\n\n"
        "FECFAU / Unicamp\n\n"
        "[Repositório](https://github.com/viniciusazeved/chuva-vazao) · "
        "[IDF-generator](https://idf-generator.streamlit.app) (app irmã)"
    ),
}

_MARCAS: dict[str, dict[str, str]] = {
    "azevedo": IDENTIDADE_AZEVEDO,
    "lapla": IDENTIDADE_LAPLA,
}


def _marca_ativa_key() -> str:
    """Le a marca ativa de env var (ou st.secrets), default 'azevedo'."""
    marca = os.environ.get("CHUVA_VAZAO_MARCA", "").strip().lower()
    if not marca:
        # Streamlit Cloud: permite fixar via secrets sem env var.
        try:
            import streamlit as st  # noqa: PLC0415

            marca = str(st.secrets.get("marca", "")).strip().lower()
        except Exception:
            marca = ""
    return marca if marca in _MARCAS else "azevedo"


def identidade_ativa(override: dict[str, str] | None = None) -> dict[str, str]:
    """
    Dict da marca ativa (default Azevedo), com ``override`` parcial mesclado por
    cima. Sempre inclui ``logo_path`` resolvido (absoluto), quando o arquivo
    existe em assets/.
    """
    base = dict(_MARCAS[_marca_ativa_key()])
    if override:
        base = {**base, **override}
    logo = ASSETS_DIR / base.get("logo_filename", "")
    base["logo_path"] = str(logo) if logo.is_file() else ""
    return base
