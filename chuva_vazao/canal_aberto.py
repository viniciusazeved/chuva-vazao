"""
Dimensionamento de canal aberto de macrodrenagem (revestido).

Ao contrario do modulo `hidraulica` (condutos fechados: manilha circular e box
celular comercial, que forcam N linhas em paralelo para vazoes altas), aqui se
dimensiona UM canal aberto de secao livre — o caso de canalizar um rio ou
implantar uma macrodrenagem. Suporta:

- Geometria trapezoidal (base b + talude z:1 H:V) e retangular (z = 0).
- Escoamento uniforme por Manning (lamina normal para uma vazao de projeto).
- Verificacao de estabilidade do revestimento em dois criterios complementares:
    (a) VELOCIDADE ADMISSIVEL por revestimento (gabiao, enrocamento, concreto,
        grama, terra) — Chow (1959), Fortier & Scobey, Maccaferri.
    (b) TENSAO TRATIVA (forca trativa, Lane 1955): tau = gama.y.S no fundo e
        ~0,76.gama.y.S no talude; para enrocamento solto dimensiona o D50 pelo
        criterio de Shields (tau_c = theta_c.(gama_s - gama).D50), com correcao
        de talude (Lane) e checagem do angulo de repouso.

Referencias:
- Chow, V. T. (1959). Open-Channel Hydraulics. McGraw-Hill (cap. 7, tabelas de
  velocidade maxima permissivel; metodo da forca trativa).
- Lane, E. W. (1955). Design of stable channels. Trans. ASCE, 120, 1234-1279.
- Maccaferri (2007). Obras de contencao / Revestimentos de canais em gabiao.
- USACE (1994). EM 1110-2-1601: Hydraulic Design of Flood Control Channels.
- Julien, P. Y. (2002). River Mechanics. Cambridge (Shields, estabilidade).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from scipy.optimize import brentq


# ---------------------------------------------------------------------------
# Constantes fisicas
# ---------------------------------------------------------------------------

GAMMA_AGUA = 9810.0     # peso especifico da agua (N/m^3), rho=1000, g=9.81
G = 9.81                # gravidade (m/s^2)
RHO_S_ROCHA = 2650.0    # massa especifica da rocha do enrocamento (kg/m^3)
PHI_REPOUSO_ENROC = 41.0  # angulo de repouso do enrocamento solto (graus)
THETA_C_SHIELDS = 0.047   # parametro critico de Shields (leito graudo)


# ---------------------------------------------------------------------------
# Revestimentos: n de Manning e limites de estabilidade
# ---------------------------------------------------------------------------
# n de Manning por revestimento de canal aberto (Chow 1959; Maccaferri).
N_REVESTIMENTO: dict[str, float] = {
    "Gabião caixa": 0.027,
    "Colchão Reno": 0.025,
    "Enrocamento (rip-rap)": 0.035,   # aproximado; refinar por Strickler(D50)
    "Concreto liso": 0.015,
    "Concreto rugoso": 0.017,
    "Grama": 0.035,
    "Solo/terra (canal escavado)": 0.030,
}

# Velocidade maxima admissivel (m/s) por revestimento. Faixas de projeto
# (Chow 1959 Tab. 7-3; Maccaferri p/ gabiao/colchao; Fortier & Scobey p/ solo).
# Enrocamento nao entra aqui: sua estabilidade vem do D50 (tensao trativa).
V_ADMISSIVEL_MS: dict[str, float] = {
    "Gabião caixa": 5.5,
    "Colchão Reno": 4.5,
    "Concreto liso": 8.0,
    "Concreto rugoso": 6.0,
    "Grama": 2.0,
    "Solo/terra (canal escavado)": 1.2,
}

# Tensao trativa admissivel (N/m^2) para revestimentos "fixos" (nao granulares).
# Gabiao/colchao: valores tipicos Maccaferri por espessura; grama/solo: Chow.
TAU_ADMISSIVEL_NM2: dict[str, float] = {
    "Gabião caixa": 300.0,
    "Colchão Reno": 200.0,
    "Concreto liso": 600.0,
    "Concreto rugoso": 500.0,
    "Grama": 80.0,
    "Solo/terra (canal escavado)": 15.0,
}

# Enrocamento ja consta em N_REVESTIMENTO; a estabilidade dele vem do D50, nao
# das tabelas de velocidade/tensao admissivel (por isso fica fora delas).
REVESTIMENTOS = list(N_REVESTIMENTO.keys())


# ---------------------------------------------------------------------------
# Geometria e Manning (trapezoidal; retangular = z 0)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GeomTrapezoidal:
    """Propriedades geometricas de uma secao trapezoidal a uma lamina y."""
    b_m: float        # largura de fundo
    z: float          # talude z:1 (H:V); z=0 => retangular
    y_m: float        # lamina d'agua
    A_m2: float       # area molhada
    P_m: float        # perimetro molhado
    R_m: float        # raio hidraulico
    T_m: float        # largura no topo (espelho d'agua)


def geometria_trapezoidal(b_m: float, z: float, y_m: float) -> GeomTrapezoidal:
    """Area, perimetro, raio hidraulico e topo de um trapezio (z:1 H:V)."""
    if b_m < 0 or z < 0 or y_m < 0:
        raise ValueError("b, z e y devem ser >= 0.")
    A = (b_m + z * y_m) * y_m
    P = b_m + 2.0 * y_m * math.sqrt(1.0 + z * z)
    R = A / P if P > 0 else 0.0
    T = b_m + 2.0 * z * y_m
    return GeomTrapezoidal(b_m=b_m, z=z, y_m=y_m, A_m2=A, P_m=P, R_m=R, T_m=T)


def manning_Q(A_m2: float, R_m: float, S: float, n: float) -> float:
    """Vazao por Manning: Q = (1/n) A R^(2/3) S^(1/2)."""
    if n <= 0 or S <= 0:
        raise ValueError("n e S devem ser positivos.")
    return (1.0 / n) * A_m2 * (R_m ** (2.0 / 3.0)) * math.sqrt(S)


def froude(v_m_s: float, A_m2: float, T_m: float) -> float:
    """Numero de Froude Fr = v / sqrt(g A / T) (profundidade hidraulica A/T)."""
    if A_m2 <= 0 or T_m <= 0:
        return 0.0
    return v_m_s / math.sqrt(G * A_m2 / T_m)


def regime(fr: float) -> str:
    if fr < 0.95:
        return "subcrítico"
    if fr > 1.05:
        return "supercrítico"
    return "crítico"


def lamina_normal_trapezoidal(
    Q_m3_s: float, b_m: float, z: float, S: float, n: float,
    y_max_m: float = 20.0,
) -> float:
    """Lamina normal y tal que Q_manning(y) = Q_projeto (brentq)."""
    if Q_m3_s <= 0:
        return 0.0

    def f(y: float) -> float:
        g = geometria_trapezoidal(b_m, z, y)
        return manning_Q(g.A_m2, g.R_m, S, n) - Q_m3_s

    if f(y_max_m) < 0:
        raise ValueError(
            f"Nem com y={y_max_m:.1f} m a secao (b={b_m}, z={z}, S={S}, n={n}) "
            f"atende Q={Q_m3_s:.1f} m3/s. Aumente a base, o talude ou a declividade."
        )
    return brentq(f, 1e-4, y_max_m)


# ---------------------------------------------------------------------------
# Tensao trativa e estabilidade de enrocamento
# ---------------------------------------------------------------------------

def tensoes_trativas(y_m: float, S: float) -> tuple[float, float]:
    """
    Tensao trativa maxima no fundo e no talude (N/m^2), metodo de Lane.

        tau_fundo  ~= gama . y . S          (canal largo; conservador)
        tau_talude ~= 0,76 . gama . y . S   (distribuicao de Lane)

    Usa y (nao R) por ser a tensao MAXIMA local, adequada a estabilidade do
    revestimento; a media seria gama.R.S.
    """
    tau_fundo = GAMMA_AGUA * y_m * S
    tau_talude = 0.76 * GAMMA_AGUA * y_m * S
    return tau_fundo, tau_talude


def fator_talude(z: float, phi_repouso_graus: float = PHI_REPOUSO_ENROC) -> float | None:
    """
    Fator de reducao de forca trativa admissivel no talude (Lane):

        K = sqrt(1 - sin^2(theta) / sin^2(phi))

    theta = angulo do talude = atan(1/z); phi = angulo de repouso do material.
    Retorna None se o talude e MAIS ingreme que o angulo de repouso (theta>=phi)
    — enrocamento solto instavel nesse talude (precisa suavizar ou usar gabiao).
    """
    if z <= 0:
        return 1.0  # parede vertical (retangular): sem componente de talude granular
    theta = math.degrees(math.atan(1.0 / z))
    if theta >= phi_repouso_graus:
        return None
    st = math.sin(math.radians(theta))
    sp = math.sin(math.radians(phi_repouso_graus))
    return math.sqrt(max(0.0, 1.0 - (st * st) / (sp * sp)))


def d50_enrocamento_m(
    tau_nm2: float,
    theta_c: float = THETA_C_SHIELDS,
    rho_s: float = RHO_S_ROCHA,
) -> float:
    """
    D50 minimo do enrocamento (m) pelo criterio de Shields:

        tau_c = theta_c . (gama_s - gama) . D50   =>   D50 = tau / [theta_c.(gama_s-gama)]

    tau_nm2 = tensao trativa atuante (usar a do fundo, ou a do talude / K_talude).
    """
    gama_s = rho_s * G
    denom = theta_c * (gama_s - GAMMA_AGUA)
    return tau_nm2 / denom if denom > 0 else float("inf")


# ---------------------------------------------------------------------------
# Dimensionamento completo
# ---------------------------------------------------------------------------

@dataclass
class CanalAbertoResult:
    """Resultado do dimensionamento/verificacao de um canal aberto."""
    # geometria e escoamento
    b_m: float
    z: float
    y_op_m: float
    altura_total_m: float
    borda_livre_m: float
    revestimento: str
    n: float
    S: float
    Q_projeto_m3_s: float
    A_m2: float
    P_m: float
    R_m: float
    T_topo_m: float
    v_m_s: float
    Fr: float
    regime: str
    # estabilidade
    tau_fundo_nm2: float
    tau_talude_nm2: float
    v_admissivel_m_s: float | None      # None p/ enrocamento (vem do D50)
    tau_admissivel_nm2: float | None    # None p/ enrocamento
    d50_enrocamento_m: float | None     # so p/ enrocamento
    talude_estavel_enroc: bool | None   # talude < angulo de repouso? (enrocamento)
    warnings: list[str] = field(default_factory=list)


def dimensionar_canal_aberto(
    Q_projeto_m3_s: float,
    b_m: float,
    z: float,
    S: float,
    revestimento: str,
    *,
    n: float | None = None,
    altura_total_m: float | None = None,
    borda_livre_min_m: float = 0.4,
    y_max_m: float = 20.0,
) -> CanalAbertoResult:
    """
    Dimensiona (acha a lamina) e verifica um canal aberto trapezoidal/retangular.

    Parameters
    ----------
    Q_projeto_m3_s : vazao de projeto.
    b_m : largura de fundo (m).
    z : talude z:1 (H:V). z=0 => retangular.
    S : declividade longitudinal (m/m).
    revestimento : chave de N_REVESTIMENTO.
    n : sobrescreve o n do revestimento (opcional).
    altura_total_m : altura construida do canal (p/ borda livre). Se None, usa
        y_op + borda_livre_min.
    borda_livre_min_m : borda livre minima recomendada (m).
    """
    if revestimento not in N_REVESTIMENTO:
        raise ValueError(f"Revestimento invalido: {revestimento}. "
                         f"Opcoes: {list(N_REVESTIMENTO)}")
    n = float(n) if n is not None else N_REVESTIMENTO[revestimento]

    y_op = lamina_normal_trapezoidal(Q_projeto_m3_s, b_m, z, S, n, y_max_m=y_max_m)
    g = geometria_trapezoidal(b_m, z, y_op)
    v = Q_projeto_m3_s / g.A_m2 if g.A_m2 > 0 else 0.0
    fr = froude(v, g.A_m2, g.T_m)
    reg = regime(fr)

    tau_fundo, tau_talude = tensoes_trativas(y_op, S)

    warnings: list[str] = []
    is_enroc = revestimento == "Enrocamento (rip-rap)"
    v_adm = V_ADMISSIVEL_MS.get(revestimento)
    tau_adm = TAU_ADMISSIVEL_NM2.get(revestimento)
    d50 = None
    talude_ok = None

    # --- Estabilidade ---
    if is_enroc:
        # D50 pelo criterio de Shields; talude exige D50 maior (fator de Lane).
        k_talude = fator_talude(z)
        d50_fundo = d50_enrocamento_m(tau_fundo)
        if k_talude is None:
            talude_ok = False
            d50 = d50_enrocamento_m(tau_talude)  # referencia (talude instavel de qq forma)
            warnings.append(
                f"Talude {z:.1f}:1 e mais ingreme que o angulo de repouso do "
                f"enrocamento (~{PHI_REPOUSO_ENROC:.0f} graus): enrocamento solto "
                f"instavel. Suavize para >= 1.5:1 (idealmente 2:1) ou use gabiao "
                f"(confinado, sem essa restricao)."
            )
        else:
            talude_ok = True
            d50_talude = d50_enrocamento_m(tau_talude) / k_talude if k_talude > 0 else float("inf")
            d50 = max(d50_fundo, d50_talude)
        if d50 is not None and math.isfinite(d50):
            warnings.append(
                f"Enrocamento: D50 minimo ~ {d50 * 100:.0f} cm (Shields, theta_c="
                f"{THETA_C_SHIELDS}). D50 > ~40 cm indica declividade/vazao "
                f"agressivas para rip-rap solto — considere gabiao ou degraus."
            )
    else:
        if v_adm is not None and v > v_adm:
            warnings.append(
                f"Velocidade {v:.2f} m/s > admissivel {v_adm:.1f} m/s para "
                f"'{revestimento}': risco de erosao/arraste do revestimento."
            )
        if tau_adm is not None and tau_fundo > tau_adm:
            warnings.append(
                f"Tensao trativa no fundo {tau_fundo:.0f} N/m2 > admissivel "
                f"{tau_adm:.0f} N/m2 para '{revestimento}'."
            )

    # --- Regime ---
    if reg == "supercrítico":
        warnings.append(
            f"Escoamento supercritico (Fr={fr:.2f}): sujeito a ondas e ressalto "
            f"hidraulico. Preveja transicoes suaves e protecao reforcada; "
            f"declividade menor ou canal em degraus reduz a energia."
        )

    # --- Altura / borda livre ---
    if altura_total_m is None:
        altura_total_m = y_op + max(borda_livre_min_m, 0.2 * y_op)
    borda_livre = altura_total_m - y_op
    if borda_livre < borda_livre_min_m:
        warnings.append(
            f"Borda livre {borda_livre:.2f} m < minima recomendada "
            f"{borda_livre_min_m:.2f} m: aumente a altura do canal."
        )

    return CanalAbertoResult(
        b_m=b_m, z=z, y_op_m=y_op, altura_total_m=altura_total_m,
        borda_livre_m=borda_livre, revestimento=revestimento, n=n, S=S,
        Q_projeto_m3_s=Q_projeto_m3_s,
        A_m2=g.A_m2, P_m=g.P_m, R_m=g.R_m, T_topo_m=g.T_m,
        v_m_s=v, Fr=fr, regime=reg,
        tau_fundo_nm2=tau_fundo, tau_talude_nm2=tau_talude,
        v_admissivel_m_s=v_adm if not is_enroc else None,
        tau_admissivel_nm2=tau_adm if not is_enroc else None,
        d50_enrocamento_m=d50,
        talude_estavel_enroc=talude_ok,
        warnings=warnings,
    )
