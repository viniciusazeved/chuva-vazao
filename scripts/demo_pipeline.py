"""
Demo end-to-end do pipeline chuva_vazao.

Pega um posto do RJ, calcula IDF, gera hietograma, aplica SCS-CN numa bacia
ficticia e salva os plots em `out/` + um PDF completo.

Uso:
    uv run python scripts/demo_pipeline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Garantir que o pacote e localizavel quando executado a partir de scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chuva_vazao import db, desagregacao, hidrograma, hietograma, idf, plots, report


def main() -> int:
    posto_nome = "Santa Cruz"
    TR = 10
    duracao_min = 60
    dt_min = 5
    bacia_area_km2 = 10.0
    tc_h = 1.0
    CN = 75.0

    out_dir = Path(__file__).resolve().parent.parent / "out"
    out_dir.mkdir(exist_ok=True)

    print(f"[1/6] Lendo posto {posto_nome!r} (IDF HidroFlu)...")
    coef = db.get_idf_coef(posto_nome)
    if coef is None:
        print(f"Posto {posto_nome} nao encontrado em postos_idf_coeficientes")
        return 1
    print(f"     K={coef.K}, a={coef.a}, b={coef.b}, c={coef.c}")

    print("[2/6] Montando IDFParams e tabela...")
    params = idf.params_from_convention(coef.K, coef.a, coef.b, coef.c)
    duracoes = [5, 10, 15, 30, 60, 120, 360, 720, 1440]
    TRs = [2, 5, 10, 25, 50, 100]
    idf_table = idf.calcular_idf(params, duracoes_min=duracoes, TRs=TRs)

    print(f"[3/6] Gerando hietograma por blocos alternados (TR={TR}, D={duracao_min}min, dt={dt_min}min)...")
    hieto = hietograma.blocos_alternados(params, TR=TR, duracao_total_min=duracao_min, dt_min=dt_min)
    print(f"     Altura total = {hieto.sum():.2f} mm")

    print(f"[4/6] Aplicando SCS-CN (A={bacia_area_km2} km^2, tc={tc_h}h, CN={CN})...")
    scs = hidrograma.SCSParams(area_km2=bacia_area_km2, tempo_concentracao_h=tc_h, CN=CN)
    hg = hidrograma.hidrograma_projeto(hieto, scs)
    print(f"     Q_pico = {hidrograma.Q_pico_m3s(hg):.2f} m^3/s")
    print(f"     Volume = {hidrograma.volume_escoado_m3(hg):,.0f} m^3")
    print(f"     t_pico = {hidrograma.tempo_ao_pico_min(hg):.1f} min")

    print("[5/6] Renderizando plots...")
    fig_idf = plots.plot_idf_curves(idf_table, titulo=f"Curvas IDF - {posto_nome} ({coef.estado})")
    fig_hieto = plots.plot_hietograma(hieto, titulo=f"Hietograma blocos TR={TR} D={duracao_min}min")
    fig_hg = plots.plot_hietograma_hidrograma(hg, titulo=f"Hietograma + Hidrograma - {posto_nome}")

    for nome, fig in [("idf", fig_idf), ("hietograma", fig_hieto), ("hidrograma", fig_hg)]:
        out_path = out_dir / f"{posto_nome.replace(' ', '_')}_{nome}.html"
        fig.write_html(out_path, include_plotlyjs="cdn")
        print(f"     salvo: {out_path}")

    print("[6/6] Gerando relatorio PDF...")
    inputs = report.RelatorioInputs(
        posto_descricao=coef.descricao,
        posto_estado=coef.estado,
        posto_fonte=coef.fonte,
        idf_params=params,
        idf_table=idf_table,
        TR_anos=TR,
        duracao_min=duracao_min,
        dt_min=dt_min,
        metodo_hietograma="Blocos Alternados (Chicago)",
        hietograma=hieto,
        scs_params=scs,
        hidrograma=hg,
        fig_idf=fig_idf,
        fig_hietograma=fig_hieto,
        fig_hidrograma=fig_hg,
    )
    pdf_bytes = report.gerar_relatorio_pdf(inputs)
    pdf_path = out_dir / f"{posto_nome.replace(' ', '_')}_relatorio.pdf"
    pdf_path.write_bytes(pdf_bytes)
    print(f"     salvo: {pdf_path}  ({len(pdf_bytes):,} bytes)")

    print("\n[OK] Pipeline completo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
