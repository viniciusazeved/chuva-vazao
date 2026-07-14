"""
Dimensionamento de canal aberto de macrodrenagem (revestido).

Ao contrario do modulo `hidraulica` (condutos fechados: manilha circular e box
celular comercial, que forcam N linhas em paralelo para vazoes altas), aqui se
dimensiona UM canal aberto de secao livre — o caso de canalizar um rio ou
implantar uma macrodrenagem. Suporta:

- Geometria trapezoidal (base b + talude z:1 H:V) e retangular (z = 0).
- Escoamento uniforme por Manning (lamina normal para uma vazao de projeto).
- Verificacao de estabilidade do revestimento em dois criterios complementares:
    (a) Revestimentos FIXOS (gabiao, colchao, concreto, grama, terra): VELOCIDADE
        e TENSAO ADMISSIVEIS tabeladas — Chow (1959), Fortier & Scobey, Maccaferri.
    (b) Revestimentos GRANULARES (enrocamento / pedra lancada): o n de Manning NAO
        e fixo — cresce com o tamanho da pedra (Strickler, n = D50^(1/6)/21,1) — e a
        estabilidade vem do D50 pela TENSAO TRATIVA (Lane 1955 + Shields):
        tau = gama.y.S no fundo, ~0,76.gama.y.S no talude; D50 minimo por
        tau_c = theta_c.(gama_s - gama).D50, com correcao de talude (Lane) e
        checagem do angulo de repouso. Distingue rip-rap (pedra de protecao,
        dezenas de kg) de enrocamento/pedrao e matacao (blocos de centenas de kg
        a > 1 t), que sao materiais de porte e rugosidade diferentes.

Referencias:
- Chow, V. T. (1959). Open-Channel Hydraulics. McGraw-Hill (cap. 7, tabelas de
  velocidade maxima permissivel; metodo da forca trativa).
- Strickler, A. (1923). n = D^(1/6)/21,1 (rugosidade de leito granular).
- Lane, E. W. (1955). Design of stable channels. Trans. ASCE, 120, 1234-1279.
- Bathurst, J. C. (1985). Flow resistance estimation in mountain rivers. J. Hydr.
  Eng. ASCE 111(4) — n sobe com rugosidade relativa grande (lamina rasa).
- Maccaferri (2007). Obras de contencao / Revestimentos de canais em gabiao.
- USACE (1994). EM 1110-2-1601: Hydraulic Design of Flood Control Channels.
- DAEE-CETESB (1980); DNIT (2006); Rio-Aguas/PCRJ (2019) — n tabelado de canais.
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
# Enrocamento: n de Manning por Strickler e conversao peso <-> D50
# ---------------------------------------------------------------------------
# Abaixo desta razao de submergencia (y/D50) a rugosidade relativa e grande
# (lamina rasa sobre pedra grauda) e o n efetivo passa a superar o Strickler
# (Bathurst 1985; Limerinos 1970): dispara aviso para folga na borda livre.
SUBMERGENCIA_MIN = 6.0


def n_manning_strickler(d50_m: float, coef: float = 21.1) -> float:
    """
    n de Manning de leito granular por Strickler: n = D50^(1/6) / coef.

    D50 em metros; coef=21,1 (Strickler 1923). Reproduz n~0,035 para rip-rap
    D50~17 cm (o valor fixo classico) e cresce ate ~0,046 para matacao D50~85 cm,
    coerente com as faixas tabeladas de canais revestidos (DAEE-CETESB; Rio-Aguas).
    """
    if d50_m <= 0:
        raise ValueError("D50 deve ser > 0.")
    return d50_m ** (1.0 / 6.0) / coef


def d50_de_peso(w50_kg: float, rho_s: float = RHO_S_ROCHA) -> float:
    """
    Diametro nominal D50 (m) de uma pedra de peso mediano W50 (kg):

        D50 = (W50 / rho_rocha)^(1/3)   (D_n50, lado do cubo de igual volume;
                                         CIRIA/CUR Rock Manual)

    E como o projetista especifica enrocamento no campo: pelo peso da pedra
    (ex.: pedrao "de 500 kg"), nao pelo diametro. Obs.: a convencao do diametro
    ESFERICO (peneira) daria D ~24% maior para o mesmo peso; aqui usamos o nominal.
    """
    if w50_kg <= 0:
        raise ValueError("W50 deve ser > 0.")
    return (w50_kg / rho_s) ** (1.0 / 3.0)


def peso_de_d50(d50_m: float, rho_s: float = RHO_S_ROCHA) -> float:
    """Peso mediano W50 (kg) de uma pedra de diametro nominal D50 (m)."""
    return rho_s * d50_m ** 3


# ---------------------------------------------------------------------------
# Revestimentos FIXOS: n de Manning tabelado e limites de estabilidade
# ---------------------------------------------------------------------------
# n de Manning por revestimento nao-granular (Chow 1959; Maccaferri; DAEE-CETESB).
N_REVESTIMENTO: dict[str, float] = {
    "Gabião caixa": 0.027,
    "Colchão Reno": 0.025,
    "Concreto liso": 0.015,
    "Concreto rugoso": 0.017,
    "Grama": 0.035,
    "Solo/terra (canal escavado)": 0.030,
}

# Velocidade maxima admissivel (m/s) por revestimento. Faixas de projeto
# (Chow 1959 Tab. 7-3; Maccaferri p/ gabiao/colchao; Fortier & Scobey p/ solo).
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


# ---------------------------------------------------------------------------
# Revestimentos GRANULARES: enrocamento / pedra lancada (n por Strickler, D50)
# ---------------------------------------------------------------------------
# O n NAO e fixo: cresce com o tamanho da pedra. A estabilidade vem do D50 pela
# tensao trativa (Shields). "rip-rap" (pedra de protecao, dezenas de kg) e
# "enrocamento/pedrao" (bloco de pedreira, centenas de kg) sao materiais de porte
# e rugosidade diferentes -> classes distintas. Cada classe e ancorada no PESO
# mediano tipico (W50); o D50 nominal e o n de Manning sao derivados dele.

@dataclass(frozen=True)
class ClasseEnrocamento:
    """Classe granular de enrocamento: peso mediano e D50/n derivados."""
    nome: str
    w50_kg: float          # peso mediano representativo da pedra
    d50_m: float           # diametro nominal derivado do peso

    @property
    def n_manning(self) -> float:
        """n de Manning da classe por Strickler(D50)."""
        return n_manning_strickler(self.d50_m)


def _classe_enroc(nome: str, w50_kg: float) -> ClasseEnrocamento:
    return ClasseEnrocamento(nome, w50_kg, d50_de_peso(w50_kg))


# Pesos medianos tipicos (kg): rip-rap = dezenas de kg (pedra de protecao de
# margem); pedrao ~500 kg (bloco de pedreira); matacao > 1 t. D50 e n derivados.
# (Portes usuais de enrocamento no Brasil; DAEE-CETESB; DNIT; USACE EM 1110-2-1601.)
CLASSES_ENROCAMENTO: dict[str, ClasseEnrocamento] = {
    nome: _classe_enroc(nome, w50) for nome, w50 in (
        ("Rip-rap (pedra de proteção)", 40.0),
        ("Rachão", 150.0),
        ("Enrocamento (pedrão)", 500.0),
        ("Matacão", 1500.0),
    )
}

# Lista completa para a UI: fixos primeiro, granulares depois (do menor ao maior).
REVESTIMENTOS = list(N_REVESTIMENTO.keys()) + list(CLASSES_ENROCAMENTO.keys())


def n_revestimento(nome: str) -> float:
    """n de Manning de um revestimento (fixo tabelado, ou granular por Strickler)."""
    if nome in N_REVESTIMENTO:
        return N_REVESTIMENTO[nome]
    if nome in CLASSES_ENROCAMENTO:
        return CLASSES_ENROCAMENTO[nome].n_manning
    raise ValueError(f"Revestimento invalido: {nome}. Opcoes: {REVESTIMENTOS}")


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
    v_admissivel_m_s: float | None      # None p/ granular (estabilidade vem do D50)
    tau_admissivel_nm2: float | None    # None p/ granular
    d50_enrocamento_m: float | None     # D50 MINIMO por Shields (so granular)
    talude_estavel_enroc: bool | None   # talude < angulo de repouso? (granular)
    d50_material_m: float | None = None       # D50 nominal da pedra ESCOLHIDA (classe)
    w50_material_kg: float | None = None      # peso mediano da pedra escolhida
    submergencia_y_d50: float | None = None   # y/D50 (razao de submergencia; granular)
    estavel_granular: bool | None = None      # pedra escolhida >= D50 minimo? (granular)
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
    revestimento : chave de N_REVESTIMENTO (fixo) ou CLASSES_ENROCAMENTO (granular).
    n : sobrescreve o n do revestimento (opcional). Para granular, o padrao vem de
        Strickler(D50 da classe); passar n aqui NAO altera o D50 usado no Shields.
    altura_total_m : altura construida do canal (p/ borda livre). Se None, usa
        y_op + borda_livre_min.
    borda_livre_min_m : borda livre minima recomendada (m).
    """
    is_granular = revestimento in CLASSES_ENROCAMENTO
    if revestimento not in N_REVESTIMENTO and not is_granular:
        raise ValueError(f"Revestimento invalido: {revestimento}. "
                         f"Opcoes: {REVESTIMENTOS}")
    n = float(n) if n is not None else n_revestimento(revestimento)

    y_op = lamina_normal_trapezoidal(Q_projeto_m3_s, b_m, z, S, n, y_max_m=y_max_m)
    g = geometria_trapezoidal(b_m, z, y_op)
    v = Q_projeto_m3_s / g.A_m2 if g.A_m2 > 0 else 0.0
    fr = froude(v, g.A_m2, g.T_m)
    reg = regime(fr)

    tau_fundo, tau_talude = tensoes_trativas(y_op, S)

    warnings: list[str] = []
    v_adm = V_ADMISSIVEL_MS.get(revestimento) if not is_granular else None
    tau_adm = TAU_ADMISSIVEL_NM2.get(revestimento) if not is_granular else None
    d50_min = None          # D50 minimo por Shields
    d50_material = None      # D50 nominal da pedra escolhida (classe)
    w50_material = None
    submergencia = None
    talude_ok = None
    estavel_granular = None

    # --- Estabilidade ---
    if is_granular:
        classe = CLASSES_ENROCAMENTO[revestimento]
        d50_material = classe.d50_m
        w50_material = classe.w50_kg
        submergencia = y_op / d50_material if d50_material > 0 else None

        # D50 minimo pelo criterio de Shields; talude exige D50 maior (fator de Lane).
        k_talude = fator_talude(z)
        d50_fundo = d50_enrocamento_m(tau_fundo)
        if k_talude is None:
            talude_ok = False
            d50_min = max(d50_fundo, d50_enrocamento_m(tau_talude))
            warnings.append(
                f"Talude {z:.1f}:1 e mais ingreme que o angulo de repouso do "
                f"enrocamento (~{PHI_REPOUSO_ENROC:.0f} graus): pedra solta "
                f"instavel no talude. Suavize para >= 1.5:1 (idealmente 2:1) ou use "
                f"gabiao (confinado, sem essa restricao)."
            )
        else:
            talude_ok = True
            d50_talude = d50_enrocamento_m(tau_talude) / k_talude if k_talude > 0 else float("inf")
            d50_min = max(d50_fundo, d50_talude)

        # Pedra escolhida x D50 minimo exigido pela tensao trativa.
        estavel_granular = math.isfinite(d50_min) and d50_material >= d50_min
        if not estavel_granular:
            warnings.append(
                f"{revestimento} (D50~{d50_material * 100:.0f} cm, pedra ~{w50_material:.0f} kg) "
                f"INSUFICIENTE para a tensao trativa: Shields exige D50 >= "
                f"{d50_min * 100:.0f} cm (pedra ~{peso_de_d50(d50_min):.0f} kg). Suba de "
                f"classe (pedrao/matacao), alargue a base/suavize o talude, ou reduza a "
                f"declividade."
            )
        else:
            warnings.append(
                f"{revestimento}: pedra ~{w50_material:.0f} kg (D50~{d50_material * 100:.0f} cm) "
                f">= D50 minimo de Shields ~{d50_min * 100:.0f} cm: estavel a tensao trativa. "
                f"n de Manning por Strickler(D50) = {n:.3f}."
            )

        # Lamina rasa sobre pedra grauda: rugosidade relativa grande, n efetivo maior.
        if submergencia is not None and submergencia < SUBMERGENCIA_MIN:
            warnings.append(
                f"Lamina rasa sobre pedra grauda (y/D50 = {submergencia:.1f} < "
                f"{SUBMERGENCIA_MIN:.0f}): rugosidade relativa grande. Correlacoes de "
                f"submergencia (HEC-15/Blodgett; Limerinos 1970) indicam n efetivo ~+30 a "
                f"+50% acima do Strickler ({n:.3f}) nessa faixa. O Strickler segue "
                f"conservador para a ESTABILIDADE da pedra (n baixo -> tensao maior), mas "
                f"subestima a lamina: adote borda livre com folga para a CAPACIDADE."
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
        v_admissivel_m_s=v_adm,
        tau_admissivel_nm2=tau_adm,
        d50_enrocamento_m=d50_min,
        talude_estavel_enroc=talude_ok,
        d50_material_m=d50_material,
        w50_material_kg=w50_material,
        submergencia_y_d50=submergencia,
        estavel_granular=estavel_granular,
        warnings=warnings,
    )
