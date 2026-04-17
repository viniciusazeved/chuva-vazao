"""
Dimensionamento hidraulico via Manning.

Cobre:
- Equacao de Manning para secao circular cheia e parcialmente cheia.
- Selecao de diametro comercial minimo que atenda a vazao de projeto.
- Equacao de Manning para secao retangular (box culvert).
- Tabela de coeficientes n por material.
- Validacoes de velocidade (sedimentacao/abrasao).

Referencias:
- Chaudhry, M. H. (2008). Open-Channel Flow. Springer, 2a ed.
- DAEE-SP. Manual de Calculo das Vazoes Maximas e Medias.
- ABNT NBR 15645 (drenagem pluvial urbana) e NBR 12266.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.optimize import brentq


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Diametros comerciais brasileiros (m). ABNT/NBR + manilhas convencionais.
COMMERCIAL_DIAMETERS_M: list[float] = [
    0.30, 0.40, 0.50, 0.60, 0.80, 1.00, 1.20, 1.50, 1.80, 2.00, 2.40, 3.00,
]

# Coeficiente n de Manning por material (Chaudhry 2008, DAEE).
MANNING_N: dict[str, float] = {
    "Concreto liso (manilha)": 0.013,
    "Concreto rugoso / moldado in loco": 0.015,
    "PEAD corrugado": 0.022,
    "PVC corrugado": 0.020,
    "PVC liso": 0.010,
    "Metalico corrugado": 0.024,
    "Alvenaria de pedra argamassada": 0.025,
    "Canal de terra (bom estado)": 0.030,
    "Canal em concreto revestido": 0.015,
    "Canal em gabiao": 0.027,
}

# Limites recomendados de velocidade (m/s) para drenagem pluvial.
V_MIN_ASSENTAMENTO = 0.60  # abaixo: sedimentacao
V_MAX_CONCRETO = 5.00      # acima: abrasao/cavitacao


# ---------------------------------------------------------------------------
# Manning circular
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EscoamentoCircular:
    D_m: float                # diametro (m)
    h_m: float                # lamina (m)
    fill_ratio: float         # h / D
    A_m2: float               # area molhada
    P_m: float                # perimetro molhado
    R_m: float                # raio hidraulico
    v_m_s: float              # velocidade
    Q_m3_s: float             # vazao
    S_m_per_m: float
    n: float


def manning_circular_full(D_m: float, S_m_per_m: float, n: float) -> EscoamentoCircular:
    """
    Escoamento a secao plena (h = D).

    Q = (1/n) * A * R^(2/3) * S^(1/2)
    A = pi*D^2/4, P = pi*D, R = D/4.
    """
    A = math.pi * D_m ** 2 / 4.0
    P = math.pi * D_m
    R = D_m / 4.0
    v = (1.0 / n) * (R ** (2.0 / 3.0)) * math.sqrt(S_m_per_m)
    Q = v * A
    return EscoamentoCircular(
        D_m=D_m, h_m=D_m, fill_ratio=1.0,
        A_m2=A, P_m=P, R_m=R, v_m_s=v, Q_m3_s=Q,
        S_m_per_m=S_m_per_m, n=n,
    )


def _theta_from_h(D_m: float, h_m: float) -> float:
    """Angulo central (rad) para uma lamina h num tubo de diametro D."""
    if not (0 <= h_m <= D_m):
        raise ValueError(f"Lamina h={h_m} fora do intervalo [0, D={D_m}].")
    return 2.0 * math.acos(1.0 - 2.0 * h_m / D_m)


def manning_circular_partial(
    D_m: float, h_m: float, S_m_per_m: float, n: float,
) -> EscoamentoCircular:
    """
    Escoamento parcialmente cheio, formulas trigonometricas.

    theta = 2 * arccos(1 - 2h/D)
    A = (D^2 / 8) * (theta - sin(theta))
    P = D * theta / 2
    R = A / P
    """
    if h_m <= 0:
        return EscoamentoCircular(
            D_m=D_m, h_m=0, fill_ratio=0, A_m2=0, P_m=0, R_m=0,
            v_m_s=0, Q_m3_s=0, S_m_per_m=S_m_per_m, n=n,
        )
    theta = _theta_from_h(D_m, h_m)
    A = (D_m ** 2 / 8.0) * (theta - math.sin(theta))
    P = D_m * theta / 2.0
    R = A / P
    v = (1.0 / n) * (R ** (2.0 / 3.0)) * math.sqrt(S_m_per_m)
    Q = v * A
    return EscoamentoCircular(
        D_m=D_m, h_m=h_m, fill_ratio=h_m / D_m,
        A_m2=A, P_m=P, R_m=R, v_m_s=v, Q_m3_s=Q,
        S_m_per_m=S_m_per_m, n=n,
    )


def lamina_para_vazao_circular(
    Q_target_m3_s: float, D_m: float, S_m_per_m: float, n: float,
) -> float:
    """
    Dado Q desejado em tubo de diametro D, encontra a lamina h (m) via brentq.

    Se Q >= Q_full, retorna D (tubo plena). Se Q = 0, retorna 0.
    """
    if Q_target_m3_s <= 0:
        return 0.0
    Q_full = manning_circular_full(D_m, S_m_per_m, n).Q_m3_s
    if Q_target_m3_s >= Q_full:
        return D_m

    def f(h):
        return manning_circular_partial(D_m, h, S_m_per_m, n).Q_m3_s - Q_target_m3_s

    return brentq(f, 1e-6, D_m - 1e-6)


# ---------------------------------------------------------------------------
# Dimensionamento circular
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DimensionamentoCircular:
    D_adotado_m: float
    Q_projeto_m3_s: float
    Q_fator_seguranca_m3_s: float
    fator_seguranca: float
    lamina_max_permitida: float
    operacao: EscoamentoCircular
    warnings: list[str]


def size_circular_culvert(
    Q_projeto_m3_s: float,
    S_m_per_m: float,
    n: float,
    fator_seguranca: float = 1.10,
    lamina_max_ratio: float = 0.80,
    diametros_comerciais: list[float] | None = None,
) -> DimensionamentoCircular:
    """
    Seleciona o menor diametro comercial que atenda a vazao de projeto.

    Criterio: lamina de operacao <= lamina_max_ratio * D (norma DAEE/ABNT).
    Aplica fator de seguranca sobre Q antes da selecao; operacao real e
    calculada com Q sem fator.
    """
    if diametros_comerciais is None:
        diametros_comerciais = COMMERCIAL_DIAMETERS_M

    Q_dim = Q_projeto_m3_s * fator_seguranca

    for D in diametros_comerciais:
        # Capacidade com a lamina-limite
        h_limite = lamina_max_ratio * D
        Q_cap = manning_circular_partial(D, h_limite, S_m_per_m, n).Q_m3_s
        if Q_cap >= Q_dim:
            # Operacao real (Q projeto, sem fator)
            h_op = lamina_para_vazao_circular(Q_projeto_m3_s, D, S_m_per_m, n)
            op = manning_circular_partial(D, h_op, S_m_per_m, n)
            warnings = validar_velocidade(op.v_m_s)
            return DimensionamentoCircular(
                D_adotado_m=D,
                Q_projeto_m3_s=Q_projeto_m3_s,
                Q_fator_seguranca_m3_s=Q_dim,
                fator_seguranca=fator_seguranca,
                lamina_max_permitida=h_limite,
                operacao=op,
                warnings=warnings,
            )

    raise ValueError(
        f"Nenhum diametro comercial atende Q={Q_projeto_m3_s:.3f} m^3/s com fator {fator_seguranca}. "
        f"Maior testado: {diametros_comerciais[-1]} m. Considere canal retangular ou multiplas linhas."
    )


# ---------------------------------------------------------------------------
# Manning retangular
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EscoamentoRetangular:
    b_m: float                # largura
    h_m: float                # altura de agua
    A_m2: float
    P_m: float
    R_m: float
    v_m_s: float
    Q_m3_s: float
    S_m_per_m: float
    n: float


def manning_rectangular(b_m: float, h_m: float, S_m_per_m: float, n: float) -> EscoamentoRetangular:
    A = b_m * h_m
    P = b_m + 2.0 * h_m
    R = A / P if P > 0 else 0.0
    v = (1.0 / n) * (R ** (2.0 / 3.0)) * math.sqrt(S_m_per_m)
    Q = v * A
    return EscoamentoRetangular(
        b_m=b_m, h_m=h_m, A_m2=A, P_m=P, R_m=R, v_m_s=v, Q_m3_s=Q,
        S_m_per_m=S_m_per_m, n=n,
    )


def size_box_culvert(
    Q_projeto_m3_s: float,
    S_m_per_m: float,
    n: float,
    razao_b_h: float = 1.5,
    fator_seguranca: float = 1.10,
    lamina_max_ratio: float = 0.85,
) -> dict:
    """
    Dimensionamento iterativo: fixa b/h e acha h via brentq tal que
    Q_capacidade(b=ratio*h, h_lamina_max) = Q_projeto * fator.
    """
    Q_dim = Q_projeto_m3_s * fator_seguranca

    def f(h_total):
        b = razao_b_h * h_total
        h_lamina = lamina_max_ratio * h_total
        return manning_rectangular(b, h_lamina, S_m_per_m, n).Q_m3_s - Q_dim

    try:
        h_total = brentq(f, 0.1, 6.0)
    except ValueError as exc:
        raise ValueError(
            f"Nao convergiu: verifique inputs (Q={Q_projeto_m3_s}, S={S_m_per_m}, n={n})."
        ) from exc

    b = razao_b_h * h_total
    # Operacao real com Q sem fator
    def f_op(h_lamina):
        return manning_rectangular(b, h_lamina, S_m_per_m, n).Q_m3_s - Q_projeto_m3_s

    h_op = brentq(f_op, 1e-6, h_total - 1e-6)
    op = manning_rectangular(b, h_op, S_m_per_m, n)

    return {
        "b_m": b,
        "h_total_m": h_total,
        "h_lamina_max_m": lamina_max_ratio * h_total,
        "operacao": op,
        "Q_projeto_m3_s": Q_projeto_m3_s,
        "Q_dim_m3_s": Q_dim,
        "fator_seguranca": fator_seguranca,
        "warnings": validar_velocidade(op.v_m_s),
    }


# ---------------------------------------------------------------------------
# Validacoes
# ---------------------------------------------------------------------------

def validar_velocidade(v_m_s: float) -> list[str]:
    warnings = []
    if v_m_s < V_MIN_ASSENTAMENTO:
        warnings.append(
            f"Velocidade {v_m_s:.2f} m/s < {V_MIN_ASSENTAMENTO:.2f} m/s: "
            f"risco de sedimentacao/assoreamento."
        )
    if v_m_s > V_MAX_CONCRETO:
        warnings.append(
            f"Velocidade {v_m_s:.2f} m/s > {V_MAX_CONCRETO:.2f} m/s: "
            f"risco de abrasao/erosao em concreto."
        )
    return warnings
