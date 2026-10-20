from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.schemas import RespostaResumo
from app.services.resumo import montar_resumo

router = APIRouter()


@router.get("/resumo", response_model=RespostaResumo)
async def get_resumo(marca_id: int = 0, session: AsyncSession = Depends(get_session)) -> RespostaResumo:
    return RespostaResumo(**await montar_resumo(session, marca_id))
