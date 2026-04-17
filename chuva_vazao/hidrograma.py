"""
Geracao de hidrograma de projeto por SCS/NRCS.

Etapas:
1. Escoamento direto via SCS-CN: Q = (P - Ia)^2 / (P - Ia + S), Ia = 0.2 * S,
   S = 25400/CN - 254 (mm).
2. Hidrograma unitario triangular (adimensional SCS): pico Q_p = 0.208 * A / t_p
   [m^3/s por mm, A em km^2, t_p em h], tempo ao pico t_p = D/2 + t_lag,
   t_lag = 0.6 * t_c, t_base = 2.67 * t_p.
3. Convolucao: h_hietograma (mm por dt) -> h_excesso (via SCS-CN aplicado
   sequencialmente sobre a chuva acumulada) -> hidrograma Q(t) pela convolucao
   com a UH.

Referencia:
- NRCS (1972). National Engineering Handbook, Section 4: Hydrology.
- Chow, Maidment & Mays (1988). Applied Hydrology, cap. 7.
- Tucci (2009). Hidrologia: Ciencia e Aplicacao, 4ed.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SCSParams:
    """Parametros da bacia para calculo SCS."""
    area_km2: float
    tempo_concentracao_h: float
    CN: float

    @property
    def S_mm(self) -> float:
        """Retencao potencial maxima S = 25400/CN - 254 (mm)."""
        if not (0 < self.CN <= 100):
            raise ValueError(f"CN fora do intervalo (0, 100]: {self.CN}")
        return 25400.0 / self.CN - 254.0

    @property
    def Ia_mm(self) -> float:
        """Abstracao inicial Ia = 0.2 * S."""
        return 0.2 * self.S_mm


# ---------------------------------------------------------------------------
# SCS-CN (escoamento direto)
# ---------------------------------------------------------------------------

def escoamento_direto_scs(P_mm: float, params: SCSParams) -> float:
    """
    Escoamento direto Q (mm) a partir de precipitacao acumulada P (mm).

    Formula SCS:
        Q = 0 se P <= Ia
        Q = (P - Ia)^2 / (P - Ia + S) caso contrario
    """
    if P_mm <= params.Ia_mm:
        return 0.0
    return (P_mm - params.Ia_mm) ** 2 / (P_mm - params.Ia_mm + params.S_mm)


def chuva_excedente(
    hietograma_mm: pd.Series,
    params: SCSParams,
) -> pd.Series:
    """
    Aplica SCS-CN sobre o hietograma, produzindo a chuva excedente por intervalo (mm).

    Metodo: calcula P_acumulada ao final de cada intervalo, deriva Q_acumulado
    pela formula SCS, e retorna Q_incremental = diff(Q_acumulado).
    """
    P_acum = hietograma_mm.cumsum()
    Q_acum = P_acum.apply(lambda p: escoamento_direto_scs(p, params))
    Q_inc = Q_acum.diff().fillna(Q_acum.iloc[0])
    return Q_inc.rename("excedente_mm")


# ---------------------------------------------------------------------------
# Hidrograma Unitario Triangular SCS
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UHTriangular:
    """Hidrograma unitario triangular SCS."""
    t_pico_h: float        # tempo ao pico (h)
    t_base_h: float        # tempo de base (h)
    Q_pico_m3s_por_mm: float  # vazao de pico por mm de chuva excedente (m^3/s / mm)

    def ordenadas(self, dt_min: float) -> pd.Series:
        """
        Discretiza a UH em passos dt_min (minutos). Retorna m^3/s por mm.

        Index = tempo (min).
        """
        dt_h = dt_min / 60.0
        n = int(np.ceil(self.t_base_h / dt_h)) + 1
        tempos_h = np.arange(n) * dt_h
        valores = np.zeros(n)
        for i, t in enumerate(tempos_h):
            if t <= self.t_pico_h:
                valores[i] = self.Q_pico_m3s_por_mm * (t / self.t_pico_h)
            elif t <= self.t_base_h:
                valores[i] = self.Q_pico_m3s_por_mm * (
                    (self.t_base_h - t) / (self.t_base_h - self.t_pico_h)
                )
            else:
                valores[i] = 0.0
        return pd.Series(
            valores,
            index=tempos_h * 60.0,
            name="Q_m3s_por_mm",
        ).rename_axis("tempo_min")


def uh_triangular_scs(
    area_km2: float,
    tempo_concentracao_h: float,
    duracao_chuva_min: float,
) -> UHTriangular:
    """
    Constroi a UH triangular SCS.

    Relacoes SCS:
        t_lag = 0.6 * t_c
        t_pico = D/2 + t_lag  (D = duracao unitaria)
        t_base = 2.67 * t_pico
        Q_pico = 0.208 * A / t_pico   [m^3/s por mm, com A em km^2 e t_pico em h]

    A constante 0.208 e derivada de conservacao de massa: volume sob a UH
    triangular = 0.5 * t_base * Q_pico = A * 1mm. Para A em km^2 e tempos em h:
        Q_pico [m^3/s/mm] = 2 * A * 1000 [m^3/mm] / (2.67 * t_pico * 3600) = 0.208 * A / t_pico.
    Valor 2.08 aparece em literatura com unidades inglesas (cfs, sq mi, inch).
    """
    D_h = duracao_chuva_min / 60.0
    t_lag = 0.6 * tempo_concentracao_h
    t_pico = D_h / 2.0 + t_lag
    t_base = 2.67 * t_pico
    Q_pico = 0.208 * area_km2 / t_pico
    return UHTriangular(t_pico_h=t_pico, t_base_h=t_base, Q_pico_m3s_por_mm=Q_pico)


# ---------------------------------------------------------------------------
# Convolucao hietograma excedente × UH
# ---------------------------------------------------------------------------

def hidrograma_projeto(
    hietograma_mm: pd.Series,
    params: SCSParams,
) -> pd.DataFrame:
    """
    Gera o hidrograma de projeto a partir do hietograma bruto.

    Etapas:
    1. Aplica SCS-CN -> hietograma de chuva excedente.
    2. Constroi UH triangular SCS a partir da area, t_c e duracao.
    3. Convolui hietograma excedente (mm por dt) com UH (m3/s por mm)
       -> vazao Q(t) (m3/s).

    Returns
    -------
    pd.DataFrame
        Colunas: tempo_min (index), hietograma_mm, excedente_mm, Q_m3s.
    """
    if hietograma_mm.empty:
        raise ValueError("Hietograma vazio.")

    excedente = chuva_excedente(hietograma_mm, params)

    dt_min = float(hietograma_mm.index[0])
    if len(hietograma_mm) > 1:
        dt_min = float(hietograma_mm.index[1] - hietograma_mm.index[0])
    duracao_min = float(hietograma_mm.index[-1])

    uh = uh_triangular_scs(
        area_km2=params.area_km2,
        tempo_concentracao_h=params.tempo_concentracao_h,
        duracao_chuva_min=dt_min,  # UH corresponde a um bloco unitario dt
    )
    uh_series = uh.ordenadas(dt_min=dt_min)

    # Convolucao numerica
    conv = np.convolve(excedente.values, uh_series.values, mode="full")
    n = len(conv)
    tempos = np.arange(n) * dt_min

    df = pd.DataFrame({
        "tempo_min": tempos,
        "Q_m3s": conv,
    }).set_index("tempo_min")

    # Alinhar hietograma e excedente no mesmo dataframe (nao-convoluidos)
    hietograma_aligned = hietograma_mm.reindex(df.index, fill_value=0.0)
    excedente_aligned = excedente.reindex(df.index, fill_value=0.0)
    df.insert(0, "hietograma_mm", hietograma_aligned.values)
    df.insert(1, "excedente_mm", excedente_aligned.values)

    return df


# ---------------------------------------------------------------------------
# Metricas do hidrograma
# ---------------------------------------------------------------------------

def Q_pico_m3s(hidrograma: pd.DataFrame) -> float:
    """Vazao maxima (m^3/s)."""
    return float(hidrograma["Q_m3s"].max())


def volume_escoado_m3(hidrograma: pd.DataFrame) -> float:
    """
    Volume total escoado (m^3) integrando Q(t) no tempo.

    Regra do trapezio com dt em segundos.
    """
    dt_min = float(hidrograma.index[1] - hidrograma.index[0]) if len(hidrograma) > 1 else 1.0
    dt_s = dt_min * 60.0
    return float(np.trapezoid(hidrograma["Q_m3s"].values, dx=dt_s))


def tempo_ao_pico_min(hidrograma: pd.DataFrame) -> float:
    """Tempo em que ocorre Q_pico (min)."""
    return float(hidrograma["Q_m3s"].idxmax())


# ---------------------------------------------------------------------------
# Metodo Racional
# ---------------------------------------------------------------------------

# Tabela C de escoamento superficial (ABRH/DAEE/CETESB).
# Valores tipicos para eventos de projeto; literatura mais detalhada da
# intervalos por TR.
C_USO_SOLO: dict[str, float] = {
    # Areas comerciais
    "Area central densa": 0.85,
    "Area comercial de bairro": 0.70,
    # Areas residenciais
    "Residencial com casas isoladas": 0.35,
    "Residencial densa (>40% impermeabilizado)": 0.55,
    "Residencial com predios e sobrados": 0.60,
    "Residencial com apartamentos": 0.65,
    # Areas industriais
    "Industrial leve": 0.60,
    "Industrial pesada": 0.75,
    # Areas publicas e verdes
    "Parques e cemiterios": 0.15,
    "Campos esportivos": 0.25,
    # Superficies especificas
    "Asfalto": 0.85,
    "Concreto": 0.90,
    "Telhados": 0.80,
    "Solo arenoso (plano, <2%)": 0.10,
    "Solo arenoso (media, 2-7%)": 0.15,
    "Solo arenoso (ingreme, >7%)": 0.20,
    "Solo argiloso (plano, <2%)": 0.17,
    "Solo argiloso (media, 2-7%)": 0.22,
    "Solo argiloso (ingreme, >7%)": 0.35,
}


def rational_method(C: float, i_mmh: float, A_km2: float) -> float:
    """
    Metodo Racional: Q = C * i * A / 3.6.

        Q : m^3/s
        C : coeficiente de escoamento (-)
        i : intensidade da chuva (mm/h)
        A : area de drenagem (km^2)

    Valido para bacias pequenas (A <= ~2 km^2). Para areas maiores, use SCS-HU.
    Pressupoe chuva de duracao = tc e uniforme sobre a bacia.
    """
    if not (0 <= C <= 1):
        raise ValueError(f"C deve estar em [0, 1], recebi {C}.")
    if i_mmh < 0 or A_km2 < 0:
        raise ValueError("i e A devem ser positivos.")
    return C * i_mmh * A_km2 / 3.6


def select_method(area_km2: float) -> str:
    """
    Escolhe metodo de transformacao chuva-vazao pelo tamanho da bacia.

    Regras (DAEE):
        A <= 2 km^2  -> Racional
        2 < A <= 250 -> SCS-HU triangular
        A > 250      -> aviso (modelo distribuido recomendado)
    """
    if area_km2 <= 0:
        raise ValueError("area deve ser positiva.")
    if area_km2 <= 2.0:
        return "Racional"
    if area_km2 <= 250.0:
        return "SCS-HU"
    return "Modelo distribuido (fora do escopo)"


def hidrograma_triangular_sintetico(
    Qp_m3_s: float,
    tc_min: float,
    dt_min: float = 1.0,
    razao_base_pico: float = 2.67,
) -> pd.DataFrame:
    """
    Gera hidrograma triangular sintetico a partir de um Q_pico e tc.

    Util quando o metodo escolhido e Racional (so Q_pico), mas queremos
    passar um hidrograma para o modulo de detencao (Puls).

    Relacao classica SCS:
        t_pico = tc
        t_base = razao_base_pico * tc  (default 2.67, mesma da UH triangular)
    """
    t_pico = tc_min
    t_base = razao_base_pico * tc_min
    n = int(np.ceil(t_base / dt_min)) + 1
    tempos = np.arange(n) * dt_min
    Q = np.zeros(n)
    for i, t in enumerate(tempos):
        if t <= t_pico:
            Q[i] = Qp_m3_s * (t / t_pico)
        elif t <= t_base:
            Q[i] = Qp_m3_s * ((t_base - t) / (t_base - t_pico))
    return pd.DataFrame({"tempo_min": tempos, "Q_m3s": Q}).set_index("tempo_min")
