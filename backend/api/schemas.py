"""Contratos de entrada/saída da API (validação via Pydantic)."""
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, EmailStr, Field


class TipoOcorrencia(str, Enum):
    ALAGAMENTO = "ALAGAMENTO"
    POLUICAO_AR = "POLUICAO_AR"
    TRANSITO = "TRANSITO"
    MANANCIAL = "MANANCIAL"
    DESMATAMENTO = "DESMATAMENTO"
    INVERSAO_TERMICA = "INVERSAO_TERMICA"


class StatusOcorrencia(str, Enum):
    PENDENTE = "PENDENTE"
    CONFIRMADA = "CONFIRMADA"
    REFUTADA = "REFUTADA"
    RESOLVIDA = "RESOLVIDA"


class RegistroIn(BaseModel):
    nome: str = Field(min_length=2, max_length=80)
    email: EmailStr
    senha: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    senha: str


class OcorrenciaIn(BaseModel):
    tipo: TipoOcorrencia
    descricao: str = Field(min_length=3, max_length=500)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    severidade: int = Field(default=1, ge=1, le=5)
    criada_em_cliente: datetime | None = None
    midia_url: str | None = Field(default=None, max_length=300)


class OcorrenciaPatch(BaseModel):
    versao: int = Field(ge=1)
    descricao: str | None = Field(default=None, min_length=3, max_length=500)
    severidade: int | None = Field(default=None, ge=1, le=5)
    status: StatusOcorrencia | None = None


class VotoIn(BaseModel):
    valor: int = Field(description="1 = confirma, -1 = refuta")


class Tipologia(str, Enum):
    HIDRO = "HIDRO"
    GEO = "GEO"
    NAO_CLASSIFICADO = "NAO_CLASSIFICADO"


class Magnitude(str, Enum):
    PP = "PP"
    MP = "MP"
    GP = "GP"


class RegistroCemadenIn(BaseModel):
    """Uma linha da tabela de ocorrências do CEMADEN, já normalizada."""
    id_externo: int = Field(ge=1)
    data: date
    uf: str = Field(min_length=2, max_length=2)
    municipio: str = Field(min_length=1, max_length=80)
    tipologia: Tipologia
    magnitude: Magnitude
    precisao_local: str | None = Field(default=None, max_length=30)
    lat: float = Field(ge=-35, le=6)      # caixa envolvente do território brasileiro
    lon: float = Field(ge=-75, le=-28)
    precisao_horario: str | None = Field(default=None, max_length=30)
    horario: str | None = Field(default=None, max_length=40)


class LoteCemadenIn(BaseModel):
    registros: list[RegistroCemadenIn] = Field(min_length=1, max_length=500)
